"""
Shared bundle logic: portable style, XYZ tile extraction, the transport-only
style comparison and file inventories (pure Python).

``src/core/publisher.py`` (the legacy ``web/`` XYZ package) keeps its public
functions as wrappers of these.
"""

import copy
import gzip
import hashlib
import json
import os
import sqlite3
from typing import Dict, Iterable, List, Optional

from .content_types import cache_control, content_type
from .validation import RASTER_LAYER_TYPES, RASTER_SOURCE_TYPES, safe_relative_path

TILES_TEMPLATE = "tiles/{z}/{x}/{y}.pbf"
SPRITE = "sprite/sprite"
GLYPHS = "glyphs/{fontstack}/{range}.pbf"


def portable_style(style: dict, source_name: str, tiles_template: Optional[str] = TILES_TEMPLATE,
                   vector_only: bool = False) -> dict:
    """``style`` with URLs relative to the style document.

    ``tiles_template=None`` leaves the project source without ``tiles``/``url``
    (the web viewer binds the transport from the manifest). ``vector_only``
    drops raster basemap sources/layers (the legacy OSM/Blue Marble
    backgrounds)."""
    result = copy.deepcopy(style)
    if vector_only:
        raster = [sid for sid, src in result.get("sources", {}).items()
                  if src.get("type") in RASTER_SOURCE_TYPES]
        for sid in raster:
            del result["sources"][sid]
        result["layers"] = [layer for layer in result.get("layers", [])
                            if layer.get("type") not in RASTER_LAYER_TYPES
                            and layer.get("source") not in raster]
    source = result.get("sources", {}).get(source_name)
    if source is not None:
        source.pop("url", None)
        if tiles_template is None:
            source.pop("tiles", None)
        else:
            source["tiles"] = [tiles_template]
    if "sprite" in result:
        result["sprite"] = SPRITE
    if "glyphs" in result:
        result["glyphs"] = GLYPHS
    return result


def write_xyz_tiles(mbtiles: str, out_dir: str, progress=None) -> int:
    """Extract an MBTiles archive to ``out_dir/{z}/{x}/{y}.pbf`` (XYZ rows,
    gzip removed so that plain static hosting works). Returns the tile count."""
    from .pmtiles_builder import connect_readonly  # pylint: disable=import-outside-toplevel
    count = 0
    conn = connect_readonly(mbtiles)
    try:
        rows = conn.execute("SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles")
        for zoom, column, row, data in rows:
            data = bytes(data or b"")
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            y = (1 << zoom) - 1 - row
            folder = os.path.join(out_dir, str(zoom), str(column))
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, f"{y}.pbf"), "wb") as handle:
                handle.write(data)
            count += 1
            if progress is not None and count % 500 == 0:
                progress.check()
    finally:
        conn.close()
    return count


# --- transport-only guarantee ---------------------------------------------------------

_TRANSPORT_SOURCE_KEYS = {"tiles", "url", "minzoom", "maxzoom", "bounds", "scheme"}


def style_semantic_diff(original: dict, packaged: dict, source_name: str) -> List[str]:
    """Differences between the compiled style and the packaged one other than
    transport URLs (source tiles/url/zooms/bounds, sprite, glyphs) and
    removed raster basemaps. Paint/layout/filter expressions, source-layer
    names, zoom ranges, sprite keys, fonts and ``q2vt:*`` metadata must be
    identical: an empty list proves the packaging is transport-only."""
    problems = []
    raster_sources = {sid for sid, src in original.get("sources", {}).items()
                      if src.get("type") in RASTER_SOURCE_TYPES}
    expected_layers = [layer for layer in original.get("layers", [])
                       if layer.get("type") not in RASTER_LAYER_TYPES
                       and layer.get("source") not in raster_sources]
    got = packaged.get("layers", [])
    if [l.get("id") for l in expected_layers] != [l.get("id") for l in got]:
        problems.append("style layer ids or order changed")
    for before, after in zip(expected_layers, got):
        if json.dumps(before, sort_keys=True) != json.dumps(after, sort_keys=True):
            problems.append(f"layer {before.get('id')} changed")
    for sid, src in original.get("sources", {}).items():
        if sid in raster_sources:
            if sid in packaged.get("sources", {}):
                problems.append(f"raster source {sid} kept")
            continue
        new = packaged.get("sources", {}).get(sid)
        if new is None:
            problems.append(f"source {sid} missing")
            continue
        a = {k: v for k, v in src.items() if k not in _TRANSPORT_SOURCE_KEYS}
        b = {k: v for k, v in new.items() if k not in _TRANSPORT_SOURCE_KEYS}
        if a != b:
            problems.append(f"source {sid} changed beyond its transport")
    for key in set(original) | set(packaged):
        if key in ("sources", "layers", "sprite", "glyphs"):
            continue
        if json.dumps(original.get(key), sort_keys=True) != json.dumps(packaged.get(key), sort_keys=True):
            problems.append(f"top-level '{key}' changed")
    return problems


# --- inventories ----------------------------------------------------------------------------

def sha256_path(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def walk_files(root: str) -> List[str]:
    """Relative ``/`` paths of every file below ``root`` (sorted)."""
    found = []
    for folder, _, files in os.walk(root):
        for name in files:
            rel = os.path.relpath(os.path.join(folder, name), root).replace(os.sep, "/")
            found.append(rel)
    return sorted(found)


def inventory(root: str, files: Iterable[str], mutable: Iterable[str] = ()) -> List[Dict]:
    mutable = set(mutable)
    items = []
    for rel in files:
        safe_relative_path(rel)
        path = os.path.join(root, *rel.split("/"))
        items.append({"path": rel, "size": os.path.getsize(path), "sha256": sha256_path(path),
                      "contentType": content_type(rel),
                      "cacheControl": cache_control(rel, rel in mutable)})
    return items


def write_json_atomic(path: str, payload, indent: Optional[int] = None) -> None:
    """Write JSON to a temporary sibling, fsync, then ``os.replace``."""
    tmp = f"{path}.tmp-{os.getpid()}-{os.urandom(4).hex()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=indent, sort_keys=indent is not None)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def write_text_atomic(path: str, text: str) -> None:
    tmp = f"{path}.tmp-{os.getpid()}-{os.urandom(4).hex()}"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
