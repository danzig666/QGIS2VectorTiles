"""Synthetic vector-tile fixtures for the publishing tests (no QGIS).

``encode_tile`` writes real Mapbox Vector Tile protobuf (points, lines,
polygons with properties); ``make_mbtiles`` stores tiles in an MBTiles file
the way GDAL does (TMS rows, gzip payloads by default).
"""

import gzip
import json
import math
import sqlite3
import struct


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _key(number: int, wire: int) -> bytes:
    return _varint((number << 3) | wire)


def _bytes(number: int, data: bytes) -> bytes:
    return _key(number, 2) + _varint(len(data)) + data


def _zigzag(value: int) -> int:
    return (value << 1) ^ (value >> 63)


def _value(value) -> bytes:
    if isinstance(value, bool):
        return _key(7, 0) + _varint(int(value))
    if isinstance(value, int):
        return _key(6, 0) + _varint(_zigzag(value))
    if isinstance(value, float):
        return _key(3, 1) + struct.pack("<d", value)
    return _bytes(1, str(value).encode("utf-8"))


def _geometry(kind: str, coords) -> bytes:
    """MVT command stream for a point [x, y], a line [[x, y], ...] or a
    polygon [[ring], ...] in tile coordinates."""
    ints, cx, cy = [], 0, 0

    def move(points, close=False):
        nonlocal cx, cy
        x, y = points[0]
        ints.extend([(1 << 3) | 1, _zigzag(x - cx), _zigzag(y - cy)])
        cx, cy = x, y
        rest = points[1:]
        if rest:
            ints.append((len(rest) << 3) | 2)
            for x, y in rest:
                ints.extend([_zigzag(x - cx), _zigzag(y - cy)])
                cx, cy = x, y
        if close:
            ints.append((1 << 3) | 7)

    if kind == "point":
        move([coords])
    elif kind == "line":
        move(coords)
    else:
        for ring in coords:
            move(ring, close=True)
    return b"".join(_varint(i) for i in ints)


def encode_tile(layers: dict, extent: int = 4096) -> bytes:
    """``layers``: {name: [(kind, coords, properties, id or None), ...]}."""
    out = b""
    types = {"point": 1, "line": 2, "polygon": 3}
    for name, features in layers.items():
        keys, values, body = [], [], b""
        for kind, coords, properties, fid in features:
            tags = []
            for key, value in properties.items():
                if key not in keys:
                    keys.append(key)
                encoded = _value(value)
                if encoded not in values:
                    values.append(encoded)
                tags.extend([keys.index(key), values.index(encoded)])
            feature = b""
            if fid is not None:
                feature += _key(1, 0) + _varint(fid)
            feature += _bytes(2, b"".join(_varint(t) for t in tags))
            feature += _key(3, 0) + _varint(types[kind])
            feature += _bytes(4, _geometry(kind, coords))
            body += _bytes(2, feature)
        layer = _key(15, 0) + _varint(2) + _bytes(1, name.encode("utf-8")) + body
        layer += b"".join(_bytes(3, k.encode("utf-8")) for k in keys)
        layer += b"".join(_bytes(4, v) for v in values)
        layer += _key(5, 0) + _varint(extent)
        out += _bytes(3, layer)
    return out


def sample_tile(z: int, x: int, y: int, extra: str = "") -> bytes:
    """A tile with a point, a line crossing the tile and a polygon with a
    hole; properties identify the address (unique payload per tile)."""
    props = {"zxy": f"{z}/{x}/{y}", "name": f"Ő-ű {extra}", "parcel": "00123/4", "n": z * 1000 + x}
    return encode_tile({
        "points": [("point", [100 + x % 50, 200], props, 1)],
        "lines": [("line", [[-64, 2048], [2048, 2100], [4160, 2048]], props, 2)],
        "polygons": [("polygon", [[[0, 0], [4096, 0], [4096, 4096], [0, 4096], [0, 0]],
                                  [[1000, 1000], [1000, 3000], [3000, 3000], [3000, 1000], [1000, 1000]]],
                      props, 3)],
    })


def make_mbtiles(path: str, tiles: dict, metadata: dict = None, compress: str = "gzip",
                 unique: bool = True) -> str:
    """``tiles``: {(z, x, y XYZ): raw MVT bytes}. ``compress``: "gzip",
    "none" or "mixed" (alternating)."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE metadata (name text, value text)")
    constraint = ", UNIQUE (zoom_level, tile_column, tile_row)" if unique else ""
    conn.execute("CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, "
                 f"tile_data blob{constraint})")
    layers = sorted({name for data in tiles.values() for name in _layer_names(data)})
    meta = {"name": "fixture", "format": "pbf", "minzoom": str(min(k[0] for k in tiles)),
            "maxzoom": str(max(k[0] for k in tiles)), "bounds": "-180,-85,180,85",
            "center": "0,0,0", "type": "overlay", "scheme": "tms",
            "json": json.dumps({"vector_layers": [{"id": n, "fields": {}} for n in layers]})}
    meta.update(metadata or {})
    conn.executemany("INSERT INTO metadata VALUES (?, ?)",
                     [(k, v) for k, v in meta.items() if v is not None])
    for i, ((z, x, y), data) in enumerate(sorted(tiles.items())):
        if compress == "gzip" or (compress == "mixed" and i % 2):
            data = gzip.compress(data, mtime=0)
        conn.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)", (z, x, (1 << z) - 1 - y, data))
    conn.commit()
    conn.close()
    return path


def _layer_names(data: bytes):
    import publishing.mvt as mvt  # pylint: disable=import-outside-toplevel
    try:
        return mvt.layer_names(data)
    except mvt.MvtDecodeError:
        return []


def pyramid(max_zoom: int, sparse: bool = False) -> dict:
    """All tiles of zooms 0..max_zoom (``sparse``: only the column x == y)."""
    tiles = {}
    for z in range(max_zoom + 1):
        for x in range(1 << z):
            for y in range(1 << z):
                if sparse and x != y:
                    continue
                tiles[(z, x, y)] = sample_tile(z, x, y)
    return tiles


def lonlat_tile(lon: float, lat: float, z: int):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


PNG_1X1 = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00"
           b"\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff"
           b"\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82")


def fixture_style(source="q2vt_tiles"):
    """A compiled-style stand-in with the features the packaging must keep:
    expressions, sprite pattern, fonts, per-zoom layers, a raster basemap
    (removed for vector-only publishing) and visible-polygon label metadata."""
    return {
        "version": 8, "name": "fixture",
        "sources": {
            source: {"type": "vector", "tiles": ["http://localhost:9000/tiles/{z}/{x}/{y}.pbf"],
                     "minzoom": 0, "maxzoom": 4},
            "osm": {"type": "raster", "tiles": ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
                    "tileSize": 256},
        },
        "sprite": "http://localhost:9000/style/sprite/sprite",
        "glyphs": "http://localhost:9000/style/glyphs/{fontstack}/{range}.pbf",
        "layers": [
            {"id": "osm-background", "type": "raster", "source": "osm"},
            {"id": "polygons_fill", "type": "fill", "source": source, "source-layer": "polygons",
             "paint": {"fill-color": ["match", ["get", "parcel"], "00123/4", "#ffcc00", "#3366cc"],
                       "fill-opacity": ["interpolate", ["linear"], ["zoom"], 0, 0.4, 4, 0.8]}},
            {"id": "polygons_hatch_z2", "type": "fill", "source": source, "source-layer": "polygons",
             "minzoom": 2, "maxzoom": 3, "paint": {"fill-pattern": "hatch_1"}},
            {"id": "lines_line", "type": "line", "source": source, "source-layer": "lines",
             "paint": {"line-color": "#aa0000", "line-width": ["interpolate", ["exponential", 2],
                                                               ["zoom"], 0, 1, 4, 6]}},
            {"id": "points_circle", "type": "circle", "source": source, "source-layer": "points",
             "paint": {"circle-radius": 4, "circle-color": "#007700"}},
            {"id": "polygons_label", "type": "symbol", "source": source, "source-layer": "points",
             "metadata": {"q2vt:visible-polygons": "polygons", "q2vt:label-per-part": False},
             "layout": {"text-field": ["get", "parcel"], "text-font": ["Noto Sans Regular"],
                        "text-size": 12}},
        ],
    }


def write_style_assets(folder):
    """sprite/ and glyphs/ folders like the exporter writes them."""
    import os  # pylint: disable=import-outside-toplevel
    sprite = os.path.join(folder, "sprite")
    os.makedirs(sprite)
    index = json.dumps({"hatch_1": {"x": 0, "y": 0, "width": 1, "height": 1, "pixelRatio": 1}})
    for name in ("sprite", "sprite@2x"):
        with open(os.path.join(sprite, f"{name}.json"), "w", encoding="utf-8") as handle:
            handle.write(index)
        with open(os.path.join(sprite, f"{name}.png"), "wb") as handle:
            handle.write(PNG_1X1)
    glyphs = os.path.join(folder, "glyphs", "Noto Sans Regular")
    os.makedirs(glyphs)
    with open(os.path.join(glyphs, "0-255.pbf"), "wb") as handle:
        handle.write(b"\x0a\x14\x0a\x11Noto Sans Regular\x12\x05\x30\x2d\x32\x35\x35")
    return sprite, os.path.dirname(glyphs)


def fixture_bundle(folder, max_zoom=4):
    """An ExportBundle over synthetic MBTiles + style assets (no QGIS)."""
    import os  # pylint: disable=import-outside-toplevel
    from publishing.models import ExportBundle  # pylint: disable=import-outside-toplevel
    os.makedirs(folder, exist_ok=True)
    mbtiles = make_mbtiles(os.path.join(folder, "tiles.mbtiles"), pyramid(max_zoom))
    sprite, glyphs = write_style_assets(folder)
    style = fixture_style()
    with open(os.path.join(folder, "style.json"), "w", encoding="utf-8") as handle:
        json.dump(style, handle)
    return ExportBundle(export_dir=folder, mbtiles_path=mbtiles,
                        style_path=os.path.join(folder, "style.json"), style=style,
                        source_name="q2vt_tiles", sprite_dir=sprite, glyphs_dir=glyphs,
                        bounds_wgs84=(-180.0, -85.0, 180.0, 85.0),
                        view={"center": [0.0, 0.0], "zoom": 1, "minZoom": 0, "maxZoom": max_zoom})
