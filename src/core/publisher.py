"""
publisher.py

Static web package: a self-contained folder that any static web server (or
a sub-directory of one) can host, with no tile server.

    web/
      index.html            MapLibre viewer (loads ./style.json)
      style.json            relative tile, sprite and glyph URLs
      tiles/{z}/{x}/{y}.pbf  uncompressed Mapbox Vector Tiles (XYZ rows)
      sprite*.json|png, glyphs/...
      maplibre-gl.mjs, maplibre-gl-shared.mjs, maplibre-gl-worker.mjs, maplibre-gl.css

The package is first written to a temporary sibling folder and renamed into
place, so an interrupted export never leaves a half-written package, and a
previous package is only replaced by a complete one.
"""

import copy
import gzip
import json
import os
import shutil
import sqlite3
import uuid
from typing import Optional

TILES_TEMPLATE = "tiles/{z}/{x}/{y}.pbf"


def portable_style(style: dict, source_name: str) -> dict:
    """``style`` with URLs relative to the style document."""
    result = copy.deepcopy(style)
    source = result.get("sources", {}).get(source_name)
    if source is not None:
        source["tiles"] = [TILES_TEMPLATE]
    if "sprite" in result:
        result["sprite"] = "sprite/sprite"
    if "glyphs" in result:
        result["glyphs"] = "glyphs/{fontstack}/{range}.pbf"
    return result


def write_xyz_tiles(mbtiles: str, out_dir: str) -> int:
    """Extract an MBTiles archive to ``out_dir/{z}/{x}/{y}.pbf`` (XYZ rows,
    gzip removed so that plain static hosting works). Returns the tile count."""
    count = 0
    with sqlite3.connect(f"file:{mbtiles}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles")
        for zoom, column, row, data in rows:
            data = bytes(data)
            if data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            y = (1 << zoom) - 1 - row
            folder = os.path.join(out_dir, str(zoom), str(column))
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, f"{y}.pbf"), "wb") as handle:
                handle.write(data)
            count += 1
    return count


def _archive_bounds(mbtiles: str):
    try:
        with sqlite3.connect(f"file:{mbtiles}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT value FROM metadata WHERE name = 'bounds'").fetchone()
        values = [float(v) for v in row[0].split(",")] if row else []
    except (sqlite3.Error, ValueError):
        return None
    return values if len(values) == 4 else None


_VIEWER = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vector tiles</title>
<link href="maplibre-gl.css" rel="stylesheet">
<style>html,body{{margin:0;height:100%}}#map{{position:absolute;inset:0}}</style>
</head>
<body>
<div id="map"></div>
<script type="module">
// MapLibre GL JS 6 is an ES module: the web server must send .mjs files as
// JavaScript (text/javascript), as current servers do.
import * as maplibregl from "./maplibre-gl.mjs";
window.maplibregl = maplibregl;
// URLs in style.json are relative to it; resolve them against this page so
// the package works from any folder of any static web server.
const base = new URL("./", window.location.href).href;
const absolute = (u) => (/^[a-z]+:/i.test(u) ? u : base + u);
fetch("style.json").then((r) => r.json()).then((style) => {{
  for (const source of Object.values(style.sources || {{}})) {{
    if (source.tiles) source.tiles = source.tiles.map(absolute);
    if (source.url) source.url = absolute(source.url);
  }}
  if (style.sprite) style.sprite = absolute(style.sprite);
  if (style.glyphs) style.glyphs = absolute(style.glyphs);
  const map = window.map = new maplibregl.Map({{container: "map", style, center: {center}, zoom: {zoom}}});
  map.on("error", (e) => (window.mapErrors = (window.mapErrors || []).concat([String(e.error && e.error.message || e)])));
  map.addControl(new maplibregl.NavigationControl());
}});
</script>
</body>
</html>
"""


def write_static_package(export_dir: str, style: dict, source_name: str,
                         viewer_dir: str, center, zoom: float,
                         target: Optional[str] = None) -> str:
    """Build ``export_dir/web`` (or ``target``) atomically; returns its path."""
    target = target or os.path.join(export_dir, "web")
    staging = f"{target}.tmp-{uuid.uuid4().hex[:8]}"
    os.makedirs(staging)
    try:
        style_dir = os.path.join(export_dir, "style")
        write_xyz_tiles(os.path.join(export_dir, "tiles.mbtiles"), os.path.join(staging, "tiles"))
        portable = portable_style(style, source_name)
        bounds = _archive_bounds(os.path.join(export_dir, "tiles.mbtiles"))
        if bounds and source_name in portable.get("sources", {}):
            portable["sources"][source_name]["bounds"] = bounds  # no requests outside
        with open(os.path.join(staging, "style.json"), "w", encoding="utf-8") as handle:
            json.dump(portable, handle, ensure_ascii=False)
        for name in ("sprite", "glyphs"):
            if os.path.isdir(os.path.join(style_dir, name)):
                shutil.copytree(os.path.join(style_dir, name), os.path.join(staging, name))
        for name in ("maplibre-gl.mjs", "maplibre-gl-shared.mjs", "maplibre-gl-worker.mjs",
                     "maplibre-gl.css", "MAPLIBRE-LICENSE.txt"):
            shutil.copy2(os.path.join(viewer_dir, name), os.path.join(staging, name))
        with open(os.path.join(staging, "index.html"), "w", encoding="utf-8") as handle:
            handle.write(_VIEWER.format(center=json.dumps([float(c) for c in center]),
                                        zoom=float(zoom)))
        if os.path.isdir(target):
            shutil.rmtree(target)
        os.replace(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target
