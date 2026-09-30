"""
validation.py

Structural validation of generated output, independent of any renderer:
style layers, visibility intervals, expression rules, sprite references and
the produced MBTiles archive.
"""

import gzip
import json
import sqlite3
from typing import Dict, Iterable, Optional, Set

from . import expressions as ex
from .dependencies import layer_field_dependencies
from .diagnostics import DiagnosticCollector

_IMAGE_PROPERTIES = (("layout", "icon-image"), ("paint", "fill-pattern"),
                     ("paint", "line-pattern"))


def _constant_images(value) -> Set[str]:
    if isinstance(value, str):
        return {value}
    names: Set[str] = set()
    if isinstance(value, list) and value and value[0] == "match":
        # ["match", key, v1, out1, ..., default]: outputs and default only.
        outputs = value[3:-1:2] + [value[-1]]
        names |= {o for o in outputs if isinstance(o, str)}
    elif isinstance(value, list) and value and value[0] in ("case", "step", "coalesce"):
        for item in value[1:]:
            if isinstance(item, str) and not item.startswith("q2vt_"):
                names.add(item)
    return names


def validate_style(style: dict, collector: DiagnosticCollector,
                   sprite_names: Optional[Iterable[str]] = None,
                   tile_layers: Optional[Dict[str, Set[str]]] = None,
                   complete: bool = True) -> None:
    """Record diagnostics for structural problems in ``style``.

    ``complete=False``: ``tile_layers`` may miss layers (bounded tile scan),
    so absent source layers are not reported.
    """
    seen_ids: Set[str] = set()
    sprites = set(sprite_names) if sprite_names is not None else None
    for layer in style.get("layers", []):
        layer_id = layer.get("id", "")
        if layer_id in seen_ids:
            collector.add("Q2VT_EXPR_INVALID", f"Duplicate style layer id '{layer_id}'",
                          component=layer_id)
        seen_ids.add(layer_id)

        minzoom, maxzoom = layer.get("minzoom", 0), layer.get("maxzoom", 24)
        if maxzoom <= minzoom:
            collector.add("Q2VT_ZOOM_EMPTY_INTERVAL",
                          f"Style layer '{layer_id}' is never visible "
                          f"(minzoom {minzoom} >= maxzoom {maxzoom})",
                          component=layer_id)

        for section in ("layout", "paint"):
            for key, value in (layer.get(section) or {}).items():
                if key in ex.CAMERA_ONLY_PROPERTIES and not ex.is_camera_only(value):
                    collector.add("Q2VT_EXPR_INVALID",
                                  f"{layer_id}: {key} does not accept feature data",
                                  component=layer_id, detail=repr(value))
                try:
                    ex.validate_zoom_usage(value)
                    ex.check_finite_numbers(value)
                except ex.ExpressionError as err:
                    collector.add("Q2VT_EXPR_INVALID", f"{layer_id}: {key}: {err}",
                                  component=layer_id, detail=repr(value))

        if sprites is not None:
            for section, key in _IMAGE_PROPERTIES:
                for name in _constant_images((layer.get(section) or {}).get(key)):
                    if name not in sprites:
                        collector.add("Q2VT_SPRITE_MISSING",
                                      f"{layer_id}: {key} '{name}' is not in the sprite sheet",
                                      component=layer_id)

        source_layer = layer.get("source-layer")
        if tile_layers is not None and source_layer:
            if source_layer not in tile_layers and not complete:
                continue
            if source_layer not in tile_layers:
                collector.add("Q2VT_SOURCE_LAYER_MISSING",
                              f"{layer_id}: source layer '{source_layer}' has no tiles",
                              component=layer_id)
            else:
                missing = layer_field_dependencies(layer) - tile_layers[source_layer]
                for name in sorted(missing):
                    collector.add("Q2VT_FIELD_MISSING",
                                  f"{layer_id}: attribute '{name}' missing from "
                                  f"'{source_layer}'", component=layer_id)


def validate_glyphs(style: dict, glyphs_dir: str, collector: DiagnosticCollector) -> None:
    """Every ``text-font`` used by a text layer must have a glyph directory."""
    import os  # pylint: disable=import-outside-toplevel

    stacks = set()
    for layer in style.get("layers", []):
        layout = layer.get("layout") or {}
        if "text-field" in layout and isinstance(layout.get("text-font"), list):
            stacks.update(f for f in layout["text-font"] if isinstance(f, str))
    for stack in sorted(stacks):
        if not os.path.isdir(os.path.join(glyphs_dir, stack)):
            collector.add("Q2VT_GLYPHS_MISSING",
                          f"No glyphs were generated for font stack '{stack}'.",
                          component=stack)


# GDAL's MVT writer lists at most this many layers in the MBTiles "json"
# metadata; the tiles themselves contain every layer.
_GDAL_METADATA_LAYER_LIMIT = 100
_MAX_SCANNED_TILES = 20000


def _varint(data: bytes, i: int):
    result = shift = 0
    while True:
        byte = data[i]
        i += 1
        result |= (byte & 0x7F) << shift
        shift += 7
        if byte < 0x80:
            return result, i


def _pbf_fields(data: bytes):
    """Minimal protobuf reader: yields (field number, value) pairs."""
    i = 0
    while i < len(data):
        key, i = _varint(data, i)
        wire = key & 7
        if wire == 0:
            value, i = _varint(data, i)
        elif wire == 2:
            size, i = _varint(data, i)
            value, i = data[i:i + size], i + size
        elif wire == 5:
            value, i = data[i:i + 4], i + 4
        elif wire == 1:
            value, i = data[i:i + 8], i + 8
        else:
            raise ValueError(f"Unsupported protobuf wire type {wire}")
        yield key >> 3, value


def tile_layers(tile: bytes) -> Dict[str, Set[str]]:
    """Layer names and attribute keys of one (optionally gzipped) MVT tile."""
    if tile[:2] == b"\x1f\x8b":
        tile = gzip.decompress(tile)
    layers: Dict[str, Set[str]] = {}
    for number, value in _pbf_fields(tile):
        if number != 3:
            continue
        name, keys = None, set()
        for inner, item in _pbf_fields(value):
            if inner == 1:
                name = item.decode("utf-8")
            elif inner == 3:
                keys.add(item.decode("utf-8"))
        if name is not None:
            layers.setdefault(name, set()).update(keys)
    return layers


def _scan_tiles(conn, wanted: Set[str]) -> Dict[str, Set[str]]:
    """Find ``wanted`` layers in the tiles themselves (bounded scan)."""
    found: Dict[str, Set[str]] = {}
    rows = conn.execute("SELECT tile_data FROM tiles LIMIT ?", (_MAX_SCANNED_TILES,))
    for (data,) in rows:
        try:
            layers = tile_layers(bytes(data))
        except (ValueError, IndexError, OSError, UnicodeDecodeError):
            continue
        for name, keys in layers.items():
            if name in wanted:
                found.setdefault(name, set()).update(keys)
    return found


def inspect_mbtiles(path: str, wanted: Optional[Iterable[str]] = None) -> dict:
    """Read MBTiles metadata and per-zoom tile counts (read-only).

    ``wanted`` source layers absent from a truncated metadata layer list are
    looked up in the tiles; ``complete`` is False when a layer could be
    missing only because the scan was bounded.
    """
    uri = f"file:{path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        metadata = dict(conn.execute("SELECT name, value FROM metadata").fetchall())
        counts = dict(conn.execute(
            "SELECT zoom_level, COUNT(*) FROM tiles GROUP BY zoom_level").fetchall())
        largest = conn.execute("SELECT MAX(LENGTH(tile_data)) FROM tiles").fetchone()[0]
        vector_layers: Dict[str, dict] = {}
        if metadata.get("json"):
            try:
                for entry in json.loads(metadata["json"]).get("vector_layers", []):
                    vector_layers[entry["id"]] = entry
            except (ValueError, KeyError, TypeError):
                pass
        complete = True
        unlisted = set(wanted or ()) - set(vector_layers)
        if unlisted and len(vector_layers) >= _GDAL_METADATA_LAYER_LIMIT:
            for name, keys in _scan_tiles(conn, unlisted).items():
                vector_layers[name] = {"id": name, "fields": {k: "" for k in keys}}
            complete = sum(counts.values()) <= _MAX_SCANNED_TILES
    return {
        "complete": complete,
        "minzoom": int(metadata["minzoom"]) if "minzoom" in metadata else None,
        "maxzoom": int(metadata["maxzoom"]) if "maxzoom" in metadata else None,
        "tile_counts": {int(k): v for k, v in counts.items()},
        "largest_tile_bytes": largest or 0,
        "vector_layers": vector_layers,
    }


def tile_layer_fields(archive: dict) -> Dict[str, Set[str]]:
    return {name: set((entry.get("fields") or {}).keys())
            for name, entry in archive.get("vector_layers", {}).items()}


def validate_archive(archive: dict, collector: DiagnosticCollector,
                     requested_min: int, requested_max: int) -> None:
    counts = archive.get("tile_counts", {})
    produced = [z for z, n in counts.items() if n > 0]
    if not produced:
        return
    if archive.get("maxzoom") is not None and archive["maxzoom"] < requested_max:
        collector.add("Q2VT_TILES_ZOOM_MISMATCH",
                      f"Archive maxzoom {archive['maxzoom']} < requested {requested_max}")
    if archive.get("minzoom") is not None and archive["minzoom"] > requested_min:
        collector.add("Q2VT_TILES_ZOOM_MISMATCH",
                      f"Archive minzoom {archive['minzoom']} > requested {requested_min}")
