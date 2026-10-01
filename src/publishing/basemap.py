"""
Vector basemap: an OpenStreetMap extract in the Protomaps schema, bundled
into the release as its own PMTiles archive (``data/basemap.pmtiles``).

* Source: the latest Protomaps daily planet build (read with HTTP range
  requests, only the tiles of the area are downloaded), another
  Protomaps-schema PMTiles URL, or a local ``.pmtiles`` file.
* Area: zooms ``0..overview_zoom`` cover a wide square around the map
  (``overview_km``), the higher zooms up to ``max_zoom`` the export extent
  grown by ``padding`` on every side.
* Styles: the official ``@protomaps/basemaps`` layer definitions (vendored,
  ``resources/basemaps/protomaps``) for each chosen flavor; the source is
  renamed, sprite icons are dropped and the Noto font stacks are replaced
  by glyphs generated from installed fonts for exactly the characters the
  extract's labels use. Vector tiles only: the viewer never requests
  anything from another site.
"""

import gzip
import json
import math
import os
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from . import mvt
from .errors import PublishingError
from .pmtiles_builder import ArchiveDescriptor, TileSink
from .progress import Progress
from .vendor.pmtiles.tile import Compression, TileType, deserialize_directory, deserialize_header, zxy_to_tileid

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STYLES_DIR = os.path.join(ROOT, "resources", "basemaps", "protomaps")
BUILDS_JSON = "https://build-metadata.protomaps.dev/builds.json"
BUILD_BASE = "https://build.protomaps.com/"
SOURCE_ID = "q2vt_basemap"
LAYER_PREFIX = "q2vt-bm-"
ATTRIBUTION = "© OpenStreetMap contributors, Protomaps"
NOTO = {"Noto Sans Regular": "regular", "Noto Sans Medium": "medium", "Noto Sans Italic": "italic"}
FONTSTACKS = {"regular": "Q2VT Basemap Regular", "medium": "Q2VT Basemap Medium",
              "italic": "Q2VT Basemap Italic"}
MAX_TILES = 200_000
USER_AGENT = "QGIS2VectorTiles-fork (basemap extract)"
FLAVOR_TITLES = {
    "en": {"light": "Light", "dark": "Dark", "white": "White", "grayscale": "Grayscale", "black": "Black"},
    "hu": {"light": "Világos", "dark": "Sötét", "white": "Fehér", "grayscale": "Szürke", "black": "Fekete"},
}


# --- range readers -------------------------------------------------------------------

class LocalReader:
    def __init__(self, path: str):
        if not os.path.isfile(path):
            raise PublishingError("Q2VT_PUB_BASEMAP", f"Basemap file not found: {path}")
        self.path = path
        self.handle = open(path, "rb")
        self.requests = 0

    def get(self, offset: int, length: int) -> bytes:
        self.requests += 1
        self.handle.seek(offset)
        data = self.handle.read(length)
        if len(data) != length:
            raise PublishingError("Q2VT_PUB_BASEMAP", "The basemap file is shorter than its directory says.")
        return data

    def close(self):
        self.handle.close()


class HttpReader:
    """HTTP(S) byte ranges with bounded retries (TLS verified by urllib)."""

    def __init__(self, url: str, timeout: float = 60, retries: int = 4,
                 opener: Optional[Callable] = None, sleep: Callable[[float], None] = time.sleep):
        self.url, self.timeout, self.retries = url, timeout, retries
        self.open = opener or urllib.request.urlopen
        self.sleep = sleep
        self.requests = 0
        self.bytes = 0

    def get(self, offset: int, length: int) -> bytes:
        end = offset + length - 1
        last = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(self.url, headers={
                "Range": f"bytes={offset}-{end}", "User-Agent": USER_AGENT,
                "Accept-Encoding": "identity"})
            try:
                self.requests += 1
                with self.open(request, timeout=self.timeout) as response:
                    status = getattr(response, "status", 200)
                    if status != 206:
                        raise PublishingError(
                            "Q2VT_PUB_RANGE_UNSUPPORTED",
                            f"The basemap server answered {status} to a range request ({self.url}).")
                    data = response.read()
                if len(data) != length:
                    raise PublishingError("Q2VT_PUB_BASEMAP",
                                          f"Short range read ({len(data)} of {length} bytes).")
                self.bytes += len(data)
                return data
            except PublishingError:
                raise
            except urllib.error.HTTPError as error:
                last = error
                if error.code not in (429, 500, 502, 503, 504):
                    raise PublishingError("Q2VT_PUB_BASEMAP",
                                          f"Basemap download failed: HTTP {error.code} ({self.url}).") from error
            except (urllib.error.URLError, OSError, TimeoutError) as error:
                last = error
            self.sleep(min(30.0, 1.5 * 2 ** attempt))
        raise PublishingError("Q2VT_PUB_BASEMAP", f"Basemap download failed after retries: {last}")

    def close(self):
        pass


def latest_build_url(opener: Optional[Callable] = None, timeout: float = 30) -> str:
    """URL of the newest Protomaps daily planet build."""
    open_ = opener or urllib.request.urlopen
    request = urllib.request.Request(BUILDS_JSON, headers={"User-Agent": USER_AGENT})
    try:
        with open_(request, timeout=timeout) as response:
            builds = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise PublishingError("Q2VT_PUB_BASEMAP",
                              f"The list of Protomaps builds could not be read ({BUILDS_JSON}): {error}. "
                              "Enter a basemap URL or a local .pmtiles file.") from error
    keys = sorted(item["key"] for item in builds if isinstance(item, dict)
                  and str(item.get("key", "")).endswith(".pmtiles"))
    if not keys:
        raise PublishingError("Q2VT_PUB_BASEMAP", "No Protomaps build is listed.")
    return BUILD_BASE + keys[-1]


def open_source(source: str, opener: Optional[Callable] = None):
    if not source:
        return HttpReader(latest_build_url(opener), opener=opener)
    if source.startswith(("http://", "https://")):
        return HttpReader(source, opener=opener)
    return LocalReader(source)


# --- area ------------------------------------------------------------------------------

def mercator_to_lonlat(x: float, y: float) -> Tuple[float, float]:
    r = 6378137.0
    return math.degrees(x / r), math.degrees(2 * math.atan(math.exp(y / r)) - math.pi / 2)


def areas(extent_3857, padding: float, overview_km: float):
    """(detail bbox, overview bbox) in lon/lat from a 3857 extent."""
    xmin, ymin, xmax, ymax = extent_3857
    dx, dy = (xmax - xmin) * padding, (ymax - ymin) * padding
    detail = (*mercator_to_lonlat(xmin - dx, ymin - dy), *mercator_to_lonlat(xmax + dx, ymax + dy))
    cx, cy = (xmin + xmax) / 2, (ymin + ymax) / 2
    lat = mercator_to_lonlat(cx, cy)[1]
    half = overview_km * 500.0 / max(0.05, math.cos(math.radians(lat)))  # ground km -> 3857 m
    half = max(half, (xmax - xmin) / 2 + dx, (ymax - ymin) / 2 + dy)
    overview = (*mercator_to_lonlat(cx - half, cy - half), *mercator_to_lonlat(cx + half, cy + half))
    return detail, overview


def tiles_in(bbox, z: int):
    n = 1 << z

    def tx(lon):
        return min(n - 1, max(0, int(math.floor((lon + 180.0) / 360.0 * n))))

    def ty(lat):
        lat = max(-85.0511287798, min(85.0511287798, lat))
        rad = math.radians(lat)
        return min(n - 1, max(0, int(math.floor((1 - math.asinh(math.tan(rad)) / math.pi) / 2 * n))))
    x0, x1 = tx(bbox[0]), tx(bbox[2])
    y0, y1 = ty(bbox[3]), ty(bbox[1])
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            yield x, y


def wanted_tiles(detail, overview, max_zoom: int, overview_zoom: int) -> List[Tuple[int, int, int]]:
    out = []
    for z in range(0, max_zoom + 1):
        out.extend((z, x, y) for x, y in tiles_in(overview if z <= overview_zoom else detail, z))
    return out


# --- extraction ---------------------------------------------------------------------------

@dataclass
class BasemapExtract:
    descriptor: ArchiveDescriptor
    characters: Set[str] = field(default_factory=set)
    requests: int = 0
    downloaded: int = 0
    source_attribution: str = ""


def _find(entries, tile_id: int):
    lo, hi = 0, len(entries) - 1
    found = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if entries[mid].tile_id <= tile_id:
            found = entries[mid]
            lo = mid + 1
        else:
            hi = mid - 1
    return found


def text_keys(flavor_layers: Iterable[dict]) -> Set[str]:
    """Feature properties read by the basemap's ``text-field`` expressions."""
    keys: Set[str] = set()

    def walk(value):
        if isinstance(value, list):
            if len(value) == 2 and value[0] == "get" and isinstance(value[1], str):
                keys.add(value[1])
            for item in value:
                walk(item)
    for layer in flavor_layers:
        walk((layer.get("layout") or {}).get("text-field"))
    return keys


def extract(reader, output: str, detail, overview, max_zoom: int, overview_zoom: int,
            keys: Set[str], progress: Optional[Progress] = None,
            max_tiles: int = MAX_TILES) -> BasemapExtract:
    """Copy the tiles of the area from ``reader`` into ``output`` (PMTiles,
    same MVT payloads) and collect the label characters."""
    progress = progress or Progress()
    header = deserialize_header(reader.get(0, 127))
    if header["tile_type"] != TileType.MVT:
        raise PublishingError("Q2VT_PUB_NOT_MVT", "The basemap source does not hold vector tiles.")
    compression = header["tile_compression"]
    if compression not in (Compression.GZIP, Compression.NONE):
        raise PublishingError("Q2VT_PUB_BASEMAP", f"Unsupported basemap tile compression {compression}.")
    raw_meta = reader.get(header["metadata_offset"], header["metadata_length"])
    if header["internal_compression"] == Compression.GZIP:
        raw_meta = gzip.decompress(raw_meta)
    metadata = json.loads(raw_meta.decode("utf-8") or "{}")
    if not any(isinstance(l, dict) and l.get("id") == "earth" for l in metadata.get("vector_layers", [])):
        raise PublishingError("Q2VT_PUB_BASEMAP",
                              "The basemap source is not in the Protomaps schema (no 'earth' layer).")
    top = min(max_zoom, header["max_zoom"])
    wanted = wanted_tiles(detail, overview, top, min(overview_zoom, top))
    if len(wanted) > max_tiles:
        raise PublishingError("Q2VT_PUB_BASEMAP",
                              f"The basemap area needs {len(wanted)} tiles (limit {max_tiles}); "
                              "lower its maximum zoom, padding or overview size.")
    root = deserialize_directory(reader.get(header["root_offset"], header["root_length"]))
    leaves: Dict[int, list] = {}
    located: List[Tuple[Tuple[int, int, int], int, int]] = []
    for index, (z, x, y) in enumerate(wanted):
        tile_id = zxy_to_tileid(z, x, y)
        entries = root
        for _ in range(4):
            entry = _find(entries, tile_id)
            if entry is None:
                break
            if entry.run_length > 0:
                if tile_id < entry.tile_id + entry.run_length:
                    located.append(((z, x, y), entry.offset, entry.length))
                break
            if entry.offset not in leaves:
                leaves[entry.offset] = deserialize_directory(
                    reader.get(header["leaf_directory_offset"] + entry.offset, entry.length))
            entries = leaves[entry.offset]
        if index % 256 == 0:
            progress.check()
            progress.update(0.25 * index / max(1, len(wanted)), f"Basemap: locating {index}/{len(wanted)} tiles")
    if not located:
        raise PublishingError("Q2VT_PUB_BASEMAP", "The basemap source has no tiles in this area.")
    # Fetch payloads in merged ranges (adjacent tiles are usually stored together).
    spans = sorted({(offset, length) for _, offset, length in located})
    blobs: Dict[Tuple[int, int], bytes] = {}
    group: List[Tuple[int, int]] = []

    def flush():
        if not group:
            return
        start = group[0][0]
        end = max(o + n for o, n in group)
        data = reader.get(header["tile_data_offset"] + start, end - start)
        for o, n in group:
            blobs[(o, n)] = data[o - start:o - start + n]
        group.clear()
    for offset, length in spans:
        if group and (offset - max(o + n for o, n in group) > 128 * 1024
                      or offset + length - group[0][0] > 8 * 1024 * 1024):
            flush()
            progress.check()
            progress.update(0.25 + 0.6 * len(blobs) / len(spans), f"Basemap: {len(blobs)}/{len(spans)} tiles")
        group.append((offset, length))
    flush()
    chars: Set[str] = set()
    decoded = set()
    with TileSink(output) as sink:
        for (z, x, y), offset, length in located:
            data = blobs[(offset, length)]
            sink.add(z, x, y, data)
            if (offset, length) not in decoded:
                decoded.add((offset, length))
                try:
                    for layer in mvt.decode(data).values():
                        for feature in layer["features"]:
                            for key, value in feature["properties"].items():
                                if key in keys and isinstance(value, str):
                                    chars.update(value)
                except mvt.MvtDecodeError as error:
                    raise PublishingError("Q2VT_PUB_NOT_MVT", f"Basemap tile {z}/{x}/{y}: {error}") from error
        out_meta = {key: metadata[key] for key in ("vector_layers", "attribution", "version",
                                                    "planetiler:version", "description")
                    if key in metadata}
        out_meta["name"] = "Basemap (OpenStreetMap, Protomaps schema)"
        descriptor = sink.write(TileType.MVT, compression, out_meta, progress=progress.sub(0.88, 1.0),
                                validate_kind="mvt")
    return BasemapExtract(descriptor, chars, reader.requests, getattr(reader, "bytes", 0),
                          str(metadata.get("attribution", "")))


# --- styles ---------------------------------------------------------------------------------

def load_flavor(flavor: str, locale: str) -> dict:
    lang = locale if locale in ("hu", "en") else "en"
    path = os.path.join(STYLES_DIR, f"{lang}-{flavor}.json")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _font_for(name: str, fonts: Dict[str, str]) -> str:
    """Our generated stack for any Noto font name of the basemap style
    (including the per-script stacks inside ``format`` expressions)."""
    if name in fonts:
        return fonts[name]
    if not name.startswith("Noto Sans"):
        return name
    lowered = name.lower()
    role = "italic" if "italic" in lowered else "medium" if ("medium" in lowered or "bold" in lowered) \
        else "regular"
    return FONTSTACKS[role]


def _replace_fonts(value, fonts: Dict[str, str]):
    if isinstance(value, str):
        return _font_for(value, fonts)
    if isinstance(value, list):
        return [_replace_fonts(v, fonts) for v in value]
    if isinstance(value, dict):
        return {k: _replace_fonts(v, fonts) for k, v in value.items()}
    return value


def prepare_flavor(doc: dict, fonts: Dict[str, str]) -> List[dict]:
    """Layers of one flavor for the release: own source id and layer ids, no
    sprite icons, generated font stacks."""
    out = []
    for layer in doc["layers"]:
        layer = json.loads(json.dumps(layer))
        layer["id"] = LAYER_PREFIX + layer["id"]
        if "source" in layer:
            layer["source"] = SOURCE_ID
        layout = layer.get("layout") or {}
        for key in [k for k in layout if k.startswith("icon-")]:
            del layout[key]
        paint = layer.get("paint") or {}
        for key in [k for k in paint if k.startswith("icon-")]:
            del paint[key]
        if layer.get("type") == "symbol" and "text-field" not in layout:
            continue  # icon-only (one-way arrows)
        for key in ("text-font", "text-field"):
            if key in layout:
                layout[key] = _replace_fonts(layout[key], fonts)
        out.append(layer)
    return out


def used_fontstacks(layers: Iterable[dict]) -> Set[str]:
    found: Set[str] = set()

    def walk(value):
        if isinstance(value, str) and value in FONTSTACKS.values():
            found.add(value)
        elif isinstance(value, list):
            for item in value:
                walk(item)
    for layer in layers:
        walk((layer.get("layout") or {}).get("text-font"))
    return found


def font_map() -> Dict[str, str]:
    return {noto: FONTSTACKS[role] for noto, role in NOTO.items()}


# --- glyphs (QGIS / Qt) -----------------------------------------------------------------------

FAMILIES = ("Noto Sans", "Open Sans", "Roboto", "Source Sans 3", "Source Sans Pro", "DejaVu Sans",
            "Liberation Sans", "Segoe UI", "Arial", "Helvetica")
ROLE_STYLES = {"regular": ("Regular", "Book", "Normal"),
               "medium": ("Medium", "SemiBold", "Semibold", "Bold", "Regular"),
               "italic": ("Italic", "Oblique", "Regular")}
BASIC = set("0123456789 -–.,/()'&")


def generate_glyphs(characters: Set[str], out_dir: str) -> Dict[str, str]:
    """Write ``glyphs/<Q2VT Basemap ...>/<range>.pbf`` for ``characters``;
    returns {fontstack: "family style"} of the installed fonts used."""
    from qgis.PyQt.QtGui import QFontDatabase  # pylint: disable=import-outside-toplevel
    from ..core.glyphs_generator import GlyphGenerator  # pylint: disable=import-outside-toplevel
    try:
        families = list(QFontDatabase().families())
        styles_of = QFontDatabase().styles
    except TypeError:  # Qt 6: static API
        families = list(QFontDatabase.families())
        styles_of = QFontDatabase.styles
    family = next((f for f in FAMILIES if f in families), None) or (families[0] if families else None)
    if family is None:
        raise PublishingError("Q2VT_PUB_BASEMAP", "No installed font for the basemap labels.")
    styles = list(styles_of(family))
    text = "".join(sorted(set(characters) | BASIC))
    used = {}
    work = os.path.join(out_dir, ".work")
    os.makedirs(work, exist_ok=True)
    try:
        for role, stack in FONTSTACKS.items():
            style = next((s for s in ROLE_STYLES[role] if s in styles), styles[0] if styles else "Regular")
            key = f"{family} {style}"
            target = os.path.join(out_dir, stack)
            source = os.path.join(work, GlyphGenerator.fontstack_name(family, style))
            if not os.path.isdir(source):
                GlyphGenerator({key: [(None, text)]}, "", work).generate()
            if not os.path.isdir(source):
                raise PublishingError("Q2VT_PUB_BASEMAP", f"Glyphs for '{key}' could not be generated.")
            if os.path.isdir(target):
                shutil.rmtree(target)
            shutil.copytree(source, target)
            used[stack] = key
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return used
