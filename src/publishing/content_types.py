"""
Content types and cache rules of published files (local preview server,
release inventories and object uploads use the same table).

* ``.pmtiles``: ``application/vnd.pmtiles``, never an outer Content-Encoding
  (byte offsets must stay valid; tiles inside are gzip-compressed on their own).
* XYZ ``.pbf`` tiles: ``application/vnd.mapbox-vector-tile``, stored
  uncompressed (plain static hosting cannot add the matching header).
* Modules ``.mjs``/``.js``: ``text/javascript`` (module and worker requests
  must not get an HTML fallback).
"""

import os

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".pmtiles": "application/vnd.pmtiles",
    ".mbtiles": "application/vnd.sqlite3",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}
TILE_TYPE = "application/vnd.mapbox-vector-tile"
GLYPH_TYPE = "application/x-protobuf"

IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache, no-store, must-revalidate"


def content_type(path: str) -> str:
    """Content type of a published path (``glyphs/**.pbf`` vs tiles)."""
    lower = path.lower().replace("\\", "/")
    ext = os.path.splitext(lower)[1]
    if ext == ".pbf":
        return GLYPH_TYPE if "glyphs/" in lower or lower.startswith("glyphs") else TILE_TYPE
    return CONTENT_TYPES.get(ext, "application/octet-stream")


def cache_control(path: str, mutable: bool = False) -> str:
    return NO_CACHE if mutable else IMMUTABLE
