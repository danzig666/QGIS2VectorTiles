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
USER_AGENT = "QWebMap (basemap extract)"
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


def _open_archive(reader) -> Tuple[dict, dict, list]:
    """Header, metadata and root directory of a Protomaps-schema MVT archive."""
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
    root = deserialize_directory(reader.get(header["root_offset"], header["root_length"]))
    return header, metadata, root


def _fetch(reader, header: dict, root: list, wanted: List[Tuple[int, int, int]], progress: Progress,
           share: float = 1.0):
    """([(z, x, y), offset, length] of the tiles that exist, {(offset, length): bytes})."""
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
            progress.update(share * 0.25 * index / max(1, len(wanted)),
                            f"Basemap: locating {index}/{len(wanted)} tiles")
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
            progress.update(share * (0.25 + 0.6 * len(blobs) / len(spans)),
                            f"Basemap: {len(blobs)}/{len(spans)} tiles")
        group.append((offset, length))
    flush()
    return located, blobs


def extract(reader, output: str, detail, overview, max_zoom: int, overview_zoom: int,
            keys: Set[str], progress: Optional[Progress] = None,
            max_tiles: int = MAX_TILES) -> BasemapExtract:
    """Copy the tiles of the area from ``reader`` into ``output`` (PMTiles,
    same MVT payloads) and collect the label characters."""
    progress = progress or Progress()
    header, metadata, root = _open_archive(reader)
    compression = header["tile_compression"]
    top = min(max_zoom, header["max_zoom"])
    wanted = wanted_tiles(detail, overview, top, min(overview_zoom, top))
    if len(wanted) > max_tiles:
        raise PublishingError("Q2VT_PUB_BASEMAP",
                              f"The basemap area needs {len(wanted)} tiles (limit {max_tiles}); "
                              "lower its maximum zoom, padding or overview size.")
    located, blobs = _fetch(reader, header, root, wanted, progress)
    if not located:
        raise PublishingError("Q2VT_PUB_BASEMAP", "The basemap source has no tiles in this area.")
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


# --- street names for the search ----------------------------------------------------------

STREETS_LAYER = "q2vt-streets"   # pseudo layer of the search index (no tiles, no style)
ADDRESSES_LAYER = "q2vt-addresses"  # house numbers (street + number) in the search index
ADDRESS_STREET_M = 150.0         # a house number takes the name of a street this close
STREET_ZOOM = 15                 # Protomaps' most detailed zoom
STREET_JOIN_M = 300.0            # pieces of one name closer than this are one street


def _tile_lonlat(z: int, x: int, y: int, extent: int):
    n = float(1 << z)

    def convert(px: float, py: float) -> Tuple[float, float]:
        lon = (x + px / extent) / n * 360.0 - 180.0
        lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + py / extent) / n))))
        return lon, lat
    return convert


def _metres(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lat = math.radians((a[1] + b[1]) / 2)
    return math.hypot((b[0] - a[0]) * 111320.0 * math.cos(lat), (b[1] - a[1]) * 110540.0)


def _bbox(points) -> List[float]:
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _gap_m(a: List[float], b: List[float]) -> float:
    """Ground distance between two lon/lat boxes (0 when they touch)."""
    dx = max(0.0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0.0, max(a[1], b[1]) - min(a[3], b[3]))
    lat = math.radians((a[1] + a[3]) / 2)
    return math.hypot(dx * 111320.0 * math.cos(lat), dy * 110540.0)


def _middle(line: List[Tuple[float, float]]) -> List[float]:
    """The point halfway along a line (on the street, not beside a bend)."""
    lengths = [_metres(a, b) for a, b in zip(line, line[1:])]
    half, walked = sum(lengths) / 2, 0.0
    for (a, b), length in zip(zip(line, line[1:]), lengths):
        if walked + length >= half and length > 0:
            t = (half - walked) / length
            return [round(a[0] + (b[0] - a[0]) * t, 6), round(a[1] + (b[1] - a[1]) * t, 6)]
        walked += length
    return [round(line[0][0], 6), round(line[0][1], 6)]


def street_records(reader, bbox, clip: Optional[Callable] = None, locale: str = "hu",
                   progress: Optional[Progress] = None, max_tiles: int = MAX_TILES,
                   pieces_out: Optional[Dict[str, list]] = None) -> List[dict]:
    """Search records of the named streets (Protomaps ``roads`` layer) in
    ``bbox`` (lon/lat). ``clip(line) -> [lines]`` keeps the parts inside the
    area (an extent layer's polygon); pieces of one name within
    ``STREET_JOIN_M`` of each other are one street: its bounds and a point
    halfway along its longest piece. Only names leave the export, never the
    geometry."""
    progress = progress or Progress()
    header, _metadata, root = _open_archive(reader)
    z = min(STREET_ZOOM, header["max_zoom"])
    wanted = [(z, x, y) for x, y in tiles_in(bbox, z)]
    if len(wanted) > max_tiles:
        raise PublishingError("Q2VT_PUB_BASEMAP", f"Street search: the area needs {len(wanted)} "
                                                  f"tiles (limit {max_tiles}).")
    located, blobs = _fetch(reader, header, root, wanted, progress, share=0.8)
    names: Dict[str, dict] = {}
    for (tz, tx, ty), offset, length in located:
        try:
            roads = mvt.decode(blobs[(offset, length)], geometry=True, layers_wanted={"roads"}).get("roads")
        except mvt.MvtDecodeError as error:
            raise PublishingError("Q2VT_PUB_NOT_MVT", f"Basemap tile {tz}/{tx}/{ty}: {error}") from error
        if not roads:
            continue
        convert = _tile_lonlat(tz, tx, ty, roads["extent"])
        for feature in roads["features"]:
            props = feature["properties"]
            local, plain = props.get(f"name:{locale}"), props.get("name")
            name = local if isinstance(local, str) and local.strip() else plain
            if feature["type"] != 2 or not isinstance(name, str) or not name.strip():
                continue
            name = " ".join(name.split())
            entry = names.setdefault(name, {"terms": {name}, "pieces": []})
            if isinstance(plain, str) and plain.strip():
                entry["terms"].add(" ".join(plain.split()))
            for part in mvt.lines(feature.get("geometry") or []):
                line = [convert(px, py) for px, py in part]
                for piece in (clip(line) if clip else [line]):
                    if len(piece) >= 2:
                        entry["pieces"].append(piece)
    progress.update(0.9, "Street names: grouping")
    if pieces_out is not None:  # for the house numbers (never published)
        for name, entry in names.items():
            pieces_out.setdefault(name, []).extend(entry["pieces"])
    records = []
    for name in sorted(names):
        pieces = names[name]["pieces"]
        if not pieces:
            continue
        boxes = [_bbox(piece) for piece in pieces]
        cluster = list(range(len(pieces)))

        def find(i):
            while cluster[i] != i:
                cluster[i] = cluster[cluster[i]]
                i = cluster[i]
            return i
        order = sorted(range(len(boxes)), key=lambda i: boxes[i][0])
        for position, i in enumerate(order):  # boxes sorted by west edge: compare neighbours only
            for j in order[position + 1:]:
                # Degrees of longitude for STREET_JOIN_M at this latitude (narrower in the north).
                reach = STREET_JOIN_M / (111320.0 * max(0.05, math.cos(math.radians(boxes[i][3]))))
                if boxes[j][0] - boxes[i][2] > reach:
                    break
                if _gap_m(boxes[i], boxes[j]) <= STREET_JOIN_M:
                    cluster[find(i)] = find(j)
        groups: Dict[int, List[int]] = {}
        for i in range(len(pieces)):
            groups.setdefault(find(i), []).append(i)
        terms = sorted(names[name]["terms"], key=lambda t: (t != name, t))
        for number, members in enumerate(sorted(groups.values(), key=lambda m: min(boxes[i][0] for i in m))):
            longest = max(members, key=lambda i: sum(_metres(a, b) for a, b in zip(pieces[i], pieces[i][1:])))
            bounds = [round(v, 6) for v in (min(boxes[i][0] for i in members), min(boxes[i][1] for i in members),
                                             max(boxes[i][2] for i in members), max(boxes[i][3] for i in members))]
            records.append({"layerId": STREETS_LAYER, "featureKey": f"{name}#{number + 1}", "label": name,
                            "terms": terms, "anchor": _middle(pieces[longest]), "bounds": bounds,
                            "suggestedZoom": 17})
    return records


class StreetIndex:
    """Nearest named street of a point (lon/lat), from street pieces
    ({name: [[(lon, lat), ...], ...]}) on a grid of about 150 m."""

    CELL = 0.002  # degrees

    def __init__(self, pieces: Dict[str, list]):
        self.cells: Dict[Tuple[int, int], List[tuple]] = {}
        for name, lines in pieces.items():
            for line in lines:
                for a, b in zip(line, line[1:]):
                    x0, x1 = sorted((int(math.floor(a[0] / self.CELL)), int(math.floor(b[0] / self.CELL))))
                    y0, y1 = sorted((int(math.floor(a[1] / self.CELL)), int(math.floor(b[1] / self.CELL))))
                    for cx in range(x0, x1 + 1):
                        for cy in range(y0, y1 + 1):
                            self.cells.setdefault((cx, cy), []).append((name, a, b))

    @staticmethod
    def _distance(p, a, b) -> float:
        """Metres from p to the segment a-b (local flat projection)."""
        k = math.cos(math.radians(p[1]))
        ax, ay = (a[0] - p[0]) * 111320.0 * k, (a[1] - p[1]) * 110540.0
        bx, by = (b[0] - p[0]) * 111320.0 * k, (b[1] - p[1]) * 110540.0
        dx, dy = bx - ax, by - ay
        length = dx * dx + dy * dy
        t = 0.0 if length == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length))
        return math.hypot(ax + t * dx, ay + t * dy)

    def nearest(self, point, limit_m: float = ADDRESS_STREET_M) -> Optional[str]:
        cx, cy = int(math.floor(point[0] / self.CELL)), int(math.floor(point[1] / self.CELL))
        best, best_d = None, limit_m
        # Cells within limit_m: a cell is narrower (in metres) east-west further north.
        reach_x = int(math.ceil(limit_m / (self.CELL * 111320.0 * max(0.05, math.cos(math.radians(point[1]))))))
        reach_y = int(math.ceil(limit_m / (self.CELL * 110540.0)))
        for x in range(cx - reach_x, cx + reach_x + 1):
            for y in range(cy - reach_y, cy + reach_y + 1):
                for name, a, b in self.cells.get((x, y), ()):
                    distance = self._distance(point, a, b)
                    if distance <= best_d:
                        best, best_d = name, distance
        return best


def address_records(points, streets: Optional["StreetIndex"] = None) -> Tuple[List[dict], int]:
    """Search records of house numbers: ``points`` = [(lon, lat, number,
    street or None)]; a missing street is the nearest named one. Returns
    (records, numbers without a street)."""
    records, seen, missing = [], set(), 0
    for lon, lat, number, street in points:
        if isinstance(number, float) and math.isnan(number):
            continue
        if isinstance(number, float) and number.is_integer():
            number = int(number)
        number = " ".join(str("" if number is None else number).split())
        if not number:
            continue
        street = " ".join(str(street or "").split()) or (streets.nearest((lon, lat)) if streets else None)
        if not street:
            missing += 1
            continue
        label = f"{street} {number}"
        if label in seen:
            continue
        seen.add(label)
        anchor = [round(lon, 6), round(lat, 6)]
        records.append({"layerId": ADDRESSES_LAYER, "featureKey": label, "label": label,
                        "terms": [label], "anchor": anchor, "bounds": anchor + anchor, "suggestedZoom": 18})
    return records, missing


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
            # The folder the generator writes: its own resolution of the font
            # (e.g. "Open Sans Semibold" is its own family in older releases).
            source = os.path.join(work, GlyphGenerator.resolve_fontstack(family, style)
                                  or GlyphGenerator.fontstack_name(family, style))
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
