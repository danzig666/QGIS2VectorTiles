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

The package is first written to a temporary sibling folder; a previous
package is moved to a backup, the new one renamed into place and the backup
removed (restored if the rename fails; ``recover_static_package`` repairs an
interrupted swap). The versioned, pointer-activated layout of the web
publishing workflow is in ``src/publishing/web_builder.py``.
"""

import contextlib
import json
import os
import shutil
import sqlite3
import uuid
from typing import Optional

# Shared with the web publishing package (one implementation of each).
from ..publishing.bundle import TILES_TEMPLATE  # noqa: E402,F401  pylint: disable=wrong-import-position
from ..publishing.bundle import portable_style as _portable_style  # noqa: E402
from ..publishing.bundle import write_xyz_tiles  # noqa: E402,F401  pylint: disable=wrong-import-position


def portable_style(style: dict, source_name: str) -> dict:
    """``style`` with URLs relative to the style document."""
    return _portable_style(style, source_name, TILES_TEMPLATE)


def _archive_bounds(mbtiles: str):
    if not os.path.isfile(mbtiles):
        return None
    try:
        # A plain connection (a file: URI with a Windows or network path may
        # not open), read-only by pragma.
        with contextlib.closing(sqlite3.connect(mbtiles)) as conn:
            conn.execute("PRAGMA query_only = ON")
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
import {{ enableVisibleLabels }} from "./visible_labels.mjs";
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
  map.once("load", () => {{ window.q2vtVisibleLabels = enableVisibleLabels(map, maplibregl); }});
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
                     "maplibre-gl.css", "MAPLIBRE-LICENSE.txt", "visible_labels.mjs"):
            shutil.copy2(os.path.join(viewer_dir, name), os.path.join(staging, name))
        with open(os.path.join(staging, "index.html"), "w", encoding="utf-8") as handle:
            handle.write(_VIEWER.format(center=json.dumps([float(c) for c in center]),
                                        zoom=float(zoom)))
        _swap_in(staging, target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def _swap_in(staging: str, target: str) -> None:
    """Replace ``target`` by ``staging`` so that a crash leaves either the
    old or the new package (plus a recoverable ``.old-*`` backup), never a
    half-deleted one: old -> backup, staging -> target, backup removed; the
    backup is put back if the second rename fails."""
    backup = None
    if os.path.isdir(target):
        backup = f"{target}.old-{uuid.uuid4().hex[:8]}"
        os.replace(target, backup)
    try:
        os.replace(staging, target)
    except BaseException:
        if backup is not None:
            os.replace(backup, target)
        raise
    if backup is not None:
        shutil.rmtree(backup, ignore_errors=True)
    recover_static_package(target)


def recover_static_package(target: str) -> None:
    """Startup recovery of interrupted swaps: restore a backup when the
    package is missing, else delete leftover backups/staging folders."""
    parent, name = os.path.split(os.path.abspath(target))
    if not os.path.isdir(parent):
        return
    leftovers = sorted(n for n in os.listdir(parent)
                       if n.startswith(f"{name}.old-") or n.startswith(f"{name}.tmp-"))
    if not os.path.isdir(target):
        backups = [n for n in leftovers if n.startswith(f"{name}.old-")]
        if backups:
            os.replace(os.path.join(parent, backups[-1]), target)
            leftovers.remove(backups[-1])
    for leftover in leftovers:
        shutil.rmtree(os.path.join(parent, leftover), ignore_errors=True)
