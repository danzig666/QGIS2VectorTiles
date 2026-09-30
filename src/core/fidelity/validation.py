"""
validation.py

Structural validation of generated output, independent of any renderer:
style layers, visibility intervals, expression rules, sprite references and
the produced MBTiles archive.
"""

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
    if isinstance(value, list) and value and value[0] in ("case", "match", "step", "coalesce"):
        for item in value[1:]:
            if isinstance(item, str) and not item.startswith("q2vt_"):
                names.add(item)
    return names


def validate_style(style: dict, collector: DiagnosticCollector,
                   sprite_names: Optional[Iterable[str]] = None,
                   tile_layers: Optional[Dict[str, Set[str]]] = None) -> None:
    """Record diagnostics for structural problems in ``style``."""
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


def inspect_mbtiles(path: str) -> dict:
    """Read MBTiles metadata and per-zoom tile counts (read-only)."""
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
    return {
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
