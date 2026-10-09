"""
Publishing validation: vector-only map data, PMTiles structure, MBTiles <->
PMTiles transport equivalence, bundle file safety and secret scanning.

Vector-only contract (docs/publishing/ARCHITECTURE.md):

* every map data source in a published style is a ``vector`` source whose
  archive holds MVT; ``raster``, ``raster-dem``, ``image``, ``video``,
  ``canvas`` sources and persisted ``geojson`` sources are refused;
* sprites, pattern textures, legend swatches, logos and glyph PBFs are
  styling/UI assets, allowed;
* the viewer may add bounded in-memory GeoJSON at runtime (visible-polygon
  label points derived from loaded tiles, a search marker, measurements);
  those never appear in a published file.
"""

import gzip
import json
import mmap
import os
import random
import re
import sqlite3
from contextlib import contextmanager
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from . import mvt
from .errors import PublishingError
from .progress import Progress
from .vendor.pmtiles.tile import (Compression, TileType, deserialize_directory,
                                  deserialize_header, tileid_to_zxy)

RASTER_SOURCE_TYPES = {"raster", "raster-dem", "image", "video", "canvas"}
RASTER_LAYER_TYPES = {"raster", "hillshade", "color-relief"}
# Runtime-only source ids the viewer may add (never in a published style).
RUNTIME_SOURCE_PREFIXES = ("q2vt_visible_", "q2vt_search_marker", "q2vt_measure")
# QGIS raster layers rendered into the release's own image archives.
RASTER_LAYER_SOURCE_PREFIX = "q2vt_raster_"


# --- vector-only style ---------------------------------------------------------

def vector_only_violations(style: dict, runtime: bool = False,
                           raster_sources: Iterable[str] = ()) -> List[str]:
    """Problems that make ``style`` not vector-only (empty list: fine).

    ``runtime=True`` accepts the viewer's bounded in-memory GeoJSON helper
    sources (``RUNTIME_SOURCE_PREFIXES``); a published file never has them.
    ``raster_sources``: ids of the QGIS raster layers' own image archives
    (``q2vt_raster_*``). Those — and only those — may be ``raster`` sources,
    without any URL (the viewer binds them to the release's archive); vector
    layers are never rasterized.
    """
    problems = []
    allowed = {sid for sid in raster_sources if sid.startswith(RASTER_LAYER_SOURCE_PREFIX)}
    for source_id, source in (style.get("sources") or {}).items():
        kind = (source or {}).get("type")
        if kind == "raster" and source_id in allowed:
            if source.get("url") or source.get("tiles"):
                problems.append(f"raster source '{source_id}' must not carry its own URL")
            continue
        if kind in RASTER_SOURCE_TYPES:
            problems.append(f"source '{source_id}' is a {kind} source")
        elif kind == "geojson":
            if not (runtime and source_id.startswith(RUNTIME_SOURCE_PREFIXES)):
                problems.append(f"source '{source_id}' is a GeoJSON geometry source")
        elif kind != "vector":
            problems.append(f"source '{source_id}' has unsupported type '{kind}'")
    for layer in style.get("layers") or []:
        if layer.get("type") == "raster" and layer.get("source") in allowed:
            continue
        if layer.get("type") in RASTER_LAYER_TYPES:
            problems.append(f"layer '{layer.get('id')}' is a {layer.get('type')} layer")
    return problems


def assert_vector_only(style: dict, raster_sources: Iterable[str] = ()) -> None:
    problems = vector_only_violations(style, raster_sources=raster_sources)
    if problems:
        raise PublishingError("Q2VT_PUB_RASTER_SOURCE", "; ".join(problems[:5]))


def strip_raster_background(style: dict) -> List[str]:
    """Remove raster basemap sources/layers (the legacy OSM / Blue Marble
    backgrounds) from a style that is about to be published vector-only.
    Returns the removed source ids. The style is modified in place."""
    removed = [sid for sid, src in (style.get("sources") or {}).items()
               if (src or {}).get("type") in RASTER_SOURCE_TYPES]
    for sid in removed:
        del style["sources"][sid]
    style["layers"] = [layer for layer in style.get("layers") or []
                       if layer.get("type") not in RASTER_LAYER_TYPES
                       and layer.get("source") not in removed]
    return removed


def visible_polygon_helpers(style: dict) -> Dict[str, List[str]]:
    """``{polygon source layer: [label style layer ids]}`` of the
    visible-polygon label helper (metadata ``q2vt:visible-polygons``)."""
    helpers: Dict[str, List[str]] = {}
    for layer in style.get("layers") or []:
        polygons = (layer.get("metadata") or {}).get("q2vt:visible-polygons")
        if polygons:
            helpers.setdefault(polygons, []).append(layer["id"])
    return helpers


def required_source_layers(style: dict) -> List[str]:
    """Source layers the style draws or its helpers read (visible-polygon
    datasets have no visible style layer of their own)."""
    names = {layer["source-layer"] for layer in style.get("layers") or [] if layer.get("source-layer")}
    names.update(visible_polygon_helpers(style))
    return sorted(names)


# --- PMTiles -------------------------------------------------------------------

@contextmanager
def open_pmtiles(path: str) -> Iterator["PmtilesFile"]:
    """Memory-mapped read access; the map and file are always closed."""
    handle = open(path, "rb")
    mapping = None
    try:
        size = os.fstat(handle.fileno()).st_size
        if size < 127:
            raise PublishingError("Q2VT_PUB_PMTILES_INVALID", "File shorter than a PMTiles header.")
        mapping = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        yield PmtilesFile(mapping, size)
    finally:
        if mapping is not None:
            mapping.close()
        handle.close()


class PmtilesFile:
    """Header, metadata and tiles of an open PMTiles v3 archive."""

    def __init__(self, data, size: int):
        self.data = data
        self.size = size
        if bytes(data[0:7]) != b"PMTiles" or data[7] != 3:
            raise PublishingError("Q2VT_PUB_PMTILES_INVALID", "Not a PMTiles v3 archive.")
        try:
            self.header = deserialize_header(bytes(data[0:127]))
        except Exception as error:  # noqa: BLE001 - unknown enum values, bad magic
            raise PublishingError("Q2VT_PUB_PMTILES_INVALID", f"Bad header: {error!r}") from error

    def get_bytes(self, offset: int, length: int) -> bytes:
        if offset < 0 or length < 0 or offset + length > self.size:
            raise PublishingError("Q2VT_PUB_PMTILES_INVALID",
                                  f"Range {offset}+{length} is outside the file ({self.size}).")
        return bytes(self.data[offset:offset + length])

    def _decompress(self, data: bytes, compression) -> bytes:
        if compression == Compression.GZIP:
            return gzip.decompress(data)
        if compression in (Compression.NONE, Compression.UNKNOWN):
            return data
        raise PublishingError("Q2VT_PUB_PMTILES_INVALID", f"Unsupported compression {compression}.")

    def metadata(self) -> dict:
        h = self.header
        raw = self._decompress(self.get_bytes(h["metadata_offset"], h["metadata_length"]),
                               h["internal_compression"])
        value = json.loads(raw.decode("utf-8")) if raw else {}
        if not isinstance(value, dict):
            raise PublishingError("Q2VT_PUB_PMTILES_INVALID", "Metadata is not a JSON object.")
        return value

    def directory(self, offset: int, length: int):
        raw = self.get_bytes(offset, length)
        # deserialize_directory decompresses gzip itself (internal compression).
        return deserialize_directory(raw)

    def entries(self) -> Iterator[Tuple[int, int, int]]:
        """(tile id, data offset, length) for every addressed tile, in id order."""
        h = self.header
        yield from self._walk(h["root_offset"], h["root_length"], 0)

    def _walk(self, offset: int, length: int, depth: int):
        if depth > 3:
            raise PublishingError("Q2VT_PUB_PMTILES_INVALID", "Directory nesting deeper than 4.")
        for entry in self.directory(offset, length):
            if entry.run_length == 0:
                yield from self._walk(self.header["leaf_directory_offset"] + entry.offset,
                                      entry.length, depth + 1)
            else:
                for i in range(entry.run_length):
                    yield entry.tile_id + i, entry.offset, entry.length

    def tile(self, offset: int, length: int) -> bytes:
        return self.get_bytes(self.header["tile_data_offset"] + offset, length)

    def tiles(self) -> Iterator[Tuple[Tuple[int, int, int], bytes]]:
        for tile_id, offset, length in self.entries():
            yield tileid_to_zxy(tile_id), self.tile(offset, length)


IMAGE_TILE_TYPES = (TileType.PNG, TileType.JPEG, TileType.WEBP)
_IMAGE_MAGIC = {TileType.PNG: b"\x89PNG\r\n\x1a\n", TileType.JPEG: b"\xff\xd8\xff",
                TileType.WEBP: b"RIFF"}


# Sampled tiles up to this size (compressed) are decoded feature by feature;
# bigger ones (a zoomed-out tile holding a whole city: several MB, 40 s of
# Python for a sample) get their layers checked (gzip + layer structure).
_DECODE_FEATURES_BYTES = 512 * 1024


def validate_pmtiles(path: str, expected_tiles: Optional[int] = None, sample: int = 64,
                     progress: Optional[Progress] = None, kind: str = "mvt",
                     payload_checked_sha256: Optional[str] = None) -> dict:
    """Structural + payload validation; raises PublishingError, returns a summary.

    ``kind``: ``"mvt"`` (vector tiles, decoded as MVT; the map data) or
    ``"image"`` (PNG / JPEG / WebP tiles of a QGIS raster layer, checked by
    their signature). ``sample``: tile payloads checked (0 = every tile),
    chosen deterministically across zooms (first/last tiles and a
    fixed-seed draw). ``payload_checked_sha256``: the archive's SHA-256 when
    its payloads were checked already (built and validated): the same file
    gets the structural checks only.
    """
    progress = progress or Progress()
    if kind == "image":
        return _validate_image_pmtiles(path, expected_tiles, sample, progress)
    with open_pmtiles(path) as archive:
        h = archive.header
        if h["tile_type"] != TileType.MVT:
            raise PublishingError("Q2VT_PUB_NOT_MVT", f"PMTiles tile type is {h['tile_type']}.")
        if h["tile_compression"] not in (Compression.GZIP, Compression.NONE):
            raise PublishingError("Q2VT_PUB_PMTILES_INVALID",
                                  f"Tile compression {h['tile_compression']} is not supported.")
        for part in ("root", "metadata", "leaf_directory", "tile_data"):
            if h[f"{part}_offset"] + h[f"{part}_length"] > archive.size:
                raise PublishingError("Q2VT_PUB_PMTILES_INVALID", f"{part} section beyond the file end.")
        if h["root_offset"] + h["root_length"] > 16384:
            raise PublishingError("Q2VT_PUB_PMTILES_INVALID",
                                  "Root directory is not within the first 16 KiB.")
        metadata = archive.metadata()
        layers = metadata.get("vector_layers")
        if not isinstance(layers, list) or not layers:
            raise PublishingError("Q2VT_PUB_PMTILES_INVALID", "Metadata has no vector_layers.")
        layer_ids = {layer.get("id") for layer in layers if isinstance(layer, dict)}
        entries = list(archive.entries())
        if not entries:
            raise PublishingError("Q2VT_PUB_EMPTY_ARCHIVE")
        ids = [e[0] for e in entries]
        if ids != sorted(ids) or len(set(ids)) != len(ids):
            raise PublishingError("Q2VT_PUB_PMTILES_INVALID", "Tile ids are not unique and sorted.")
        if h["addressed_tiles_count"] and h["addressed_tiles_count"] != len(entries):
            raise PublishingError("Q2VT_PUB_PMTILES_INVALID",
                                  f"Header says {h['addressed_tiles_count']} tiles, "
                                  f"directories hold {len(entries)}.")
        if expected_tiles is not None and expected_tiles != len(entries):
            raise PublishingError("Q2VT_PUB_TRANSPORT_MISMATCH",
                                  f"{len(entries)} tiles instead of {expected_tiles}.")
        for _, offset, length in entries:
            if offset + length > h["tile_data_length"]:
                raise PublishingError("Q2VT_PUB_PMTILES_INVALID", "Tile beyond the tile data section.")
        zooms = [tileid_to_zxy(i)[0] for i in (ids[0], ids[-1])]
        if zooms[0] != h["min_zoom"] or zooms[1] != h["max_zoom"]:
            raise PublishingError("Q2VT_PUB_PMTILES_INVALID",
                                  f"Header zooms {h['min_zoom']}-{h['max_zoom']} != tiles {zooms}.")
        from .pmtiles_builder import sha256_file  # pylint: disable=import-outside-toplevel
        same = payload_checked_sha256 is not None and sha256_file(path) == payload_checked_sha256
        chosen = [] if same else _sample_indices(entries, sample)
        found_layers = set()
        for done, index in enumerate(chosen):
            tile_id, offset, length = entries[index]
            data = archive.tile(offset, length)
            if h["tile_compression"] == Compression.GZIP and data[:2] != b"\x1f\x8b" and data:
                raise PublishingError("Q2VT_PUB_PMTILES_INVALID",
                                      f"Tile {tileid_to_zxy(tile_id)} is not gzip as declared.")
            try:
                decoded = mvt.decode(data, properties=False) if length <= _DECODE_FEATURES_BYTES \
                    else mvt.layer_summary(data)
            except mvt.MvtDecodeError as error:
                raise PublishingError("Q2VT_PUB_NOT_MVT",
                                      f"Tile {tileid_to_zxy(tile_id)}: {error}") from error
            unknown = set(decoded) - layer_ids
            if unknown:
                raise PublishingError("Q2VT_PUB_PMTILES_INVALID",
                                      f"Tile {tileid_to_zxy(tile_id)} has undeclared layers "
                                      f"{sorted(unknown)[:3]}.")
            found_layers.update(decoded)
            if done % 64 == 0:
                progress.check()
                progress.update(done / max(1, len(chosen)))
        return {"tiles": len(entries), "minZoom": h["min_zoom"], "maxZoom": h["max_zoom"],
                "decoded": len(chosen), "layersSeen": sorted(found_layers),
                "vectorLayers": sorted(i for i in layer_ids if i)}


def _check_structure(archive: "PmtilesFile", expected_tiles: Optional[int]) -> list:
    h = archive.header
    for part in ("root", "metadata", "leaf_directory", "tile_data"):
        if h[f"{part}_offset"] + h[f"{part}_length"] > archive.size:
            raise PublishingError("Q2VT_PUB_PMTILES_INVALID", f"{part} section beyond the file end.")
    if h["root_offset"] + h["root_length"] > 16384:
        raise PublishingError("Q2VT_PUB_PMTILES_INVALID", "Root directory is not within the first 16 KiB.")
    entries = list(archive.entries())
    if not entries:
        raise PublishingError("Q2VT_PUB_EMPTY_ARCHIVE")
    ids = [e[0] for e in entries]
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        raise PublishingError("Q2VT_PUB_PMTILES_INVALID", "Tile ids are not unique and sorted.")
    if h["addressed_tiles_count"] and h["addressed_tiles_count"] != len(entries):
        raise PublishingError("Q2VT_PUB_PMTILES_INVALID",
                              f"Header says {h['addressed_tiles_count']} tiles, directories hold {len(entries)}.")
    if expected_tiles is not None and expected_tiles != len(entries):
        raise PublishingError("Q2VT_PUB_TRANSPORT_MISMATCH", f"{len(entries)} tiles instead of {expected_tiles}.")
    for _, offset, length in entries:
        if offset + length > h["tile_data_length"]:
            raise PublishingError("Q2VT_PUB_PMTILES_INVALID", "Tile beyond the tile data section.")
    zooms = [tileid_to_zxy(i)[0] for i in (ids[0], ids[-1])]
    if zooms[0] != h["min_zoom"] or zooms[1] != h["max_zoom"]:
        raise PublishingError("Q2VT_PUB_PMTILES_INVALID",
                              f"Header zooms {h['min_zoom']}-{h['max_zoom']} != tiles {zooms}.")
    return entries


def _validate_image_pmtiles(path: str, expected_tiles: Optional[int], sample: int,
                            progress: Progress) -> dict:
    with open_pmtiles(path) as archive:
        h = archive.header
        if h["tile_type"] not in IMAGE_TILE_TYPES:
            raise PublishingError("Q2VT_PUB_RASTER_SOURCE",
                                  f"Raster layer archive tile type is {h['tile_type']}, not PNG/JPEG/WebP.")
        if h["tile_compression"] != Compression.NONE:
            raise PublishingError("Q2VT_PUB_PMTILES_INVALID", "Image tiles must not be compressed again.")
        archive.metadata()
        entries = _check_structure(archive, expected_tiles)
        magic = _IMAGE_MAGIC[h["tile_type"]]
        chosen = _sample_indices(entries, sample)
        for done, index in enumerate(chosen):
            tile_id, offset, length = entries[index]
            data = archive.tile(offset, length)
            if not data.startswith(magic) or (h["tile_type"] == TileType.WEBP and data[8:12] != b"WEBP"):
                raise PublishingError("Q2VT_PUB_PMTILES_INVALID",
                                      f"Tile {tileid_to_zxy(tile_id)} is not a {h['tile_type'].name} image.")
            if done % 64 == 0:
                progress.check()
        return {"tiles": len(entries), "minZoom": h["min_zoom"], "maxZoom": h["max_zoom"],
                "decoded": len(chosen), "tileType": h["tile_type"].name.lower()}


def _sample_indices(entries: Sequence, sample: int) -> List[int]:
    if sample <= 0 or sample >= len(entries):
        return list(range(len(entries)))
    by_zoom: Dict[int, List[int]] = {}
    for index, (tile_id, _, _) in enumerate(entries):
        by_zoom.setdefault(tileid_to_zxy(tile_id)[0], []).append(index)
    chosen = set()
    rng = random.Random(1729)  # deterministic
    per_zoom = max(2, sample // max(1, len(by_zoom)))
    for indices in by_zoom.values():
        chosen.update((indices[0], indices[-1]))
        chosen.update(rng.sample(indices, min(len(indices), per_zoom)))
    return sorted(chosen)


def mbtiles_tiles(path: str) -> Iterator[Tuple[Tuple[int, int, int], bytes]]:
    """((z, x, y XYZ), payload) of every MBTiles tile (read-only)."""
    from .pmtiles_builder import connect_readonly  # pylint: disable=import-outside-toplevel
    conn = connect_readonly(path)
    try:
        for z, x, row, data in conn.execute(
                "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles"):
            yield (z, x, (1 << z) - 1 - row), bytes(data or b"")
    finally:
        conn.close()


def compare_archives(mbtiles_path: str, pmtiles_path: str) -> dict:
    """Exhaustive transport check: the same XYZ addresses and the same
    *decompressed* MVT bytes in both archives. Raises on any difference."""
    expected = {}
    for address, data in mbtiles_tiles(mbtiles_path):
        expected[address] = mvt.payload(data)
    seen = 0
    with open_pmtiles(pmtiles_path) as archive:
        for address, data in archive.tiles():
            if address not in expected:
                raise PublishingError("Q2VT_PUB_TRANSPORT_MISMATCH", f"Extra tile {address}.")
            if mvt.payload(data) != expected[address]:
                raise PublishingError("Q2VT_PUB_TRANSPORT_MISMATCH", f"Tile {address} differs.")
            seen += 1
    if seen != len(expected):
        missing = sorted(set(expected))[:3]
        raise PublishingError("Q2VT_PUB_TRANSPORT_MISMATCH",
                              f"{len(expected) - seen} tiles missing (e.g. {missing}).")
    return {"tiles": seen}


# --- bundle file safety ---------------------------------------------------------

FORBIDDEN_EXTENSIONS = {
    ".qgs", ".qgz", ".qlr", ".qml", ".gpkg", ".gpkg-wal", ".gpkg-shm", ".shp", ".shx",
    ".dbf", ".prj", ".cpg", ".sbn", ".sbx", ".sqlite", ".db", ".mbtiles", ".parquet",
    ".geojson", ".fgb", ".gml", ".kml", ".kmz", ".csv", ".xlsx", ".vrt", ".log", ".py",
    ".pyc", ".bat", ".sh", ".exe", ".dll", ".zip", ".partial", ".tmp",
}
FORBIDDEN_NAMES = {"export_log.txt", "fidelity_report.json", "fidelity_report.html",
                   "activate_server.sh", "tiles_server.py", ".env"}
TEXT_EXTENSIONS = {".html", ".json", ".mjs", ".js", ".css", ".txt", ".md", ".svg"}
_ABS_PATH = re.compile(r"(?:[A-Za-z]:\\\\|[A-Za-z]:\\[^\\\s\"]|/home/|/Users/|/tmp/|/var/folders/|"
                       r"\\\\Users\\\\|file:///)")


def safe_relative_path(path: str) -> str:
    """Normalized ``a/b/c`` or PublishingError for absolute / ``..`` /
    control-character paths."""
    if not isinstance(path, str) or not path or "\x00" in path:
        raise PublishingError("Q2VT_PUB_PATH_UNSAFE", repr(path))
    if any(ord(c) < 32 for c in path) or "\\" in path:
        raise PublishingError("Q2VT_PUB_PATH_UNSAFE", repr(path))
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path) or "://" in path:
        raise PublishingError("Q2VT_PUB_PATH_UNSAFE", repr(path))
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise PublishingError("Q2VT_PUB_PATH_UNSAFE", repr(path))
    return "/".join(parts)


def check_public_file(relative: str, allow_mbtiles: bool = False) -> None:
    name = relative.rsplit("/", 1)[-1]
    ext = os.path.splitext(name)[1].lower()
    if name in FORBIDDEN_NAMES or (ext in FORBIDDEN_EXTENSIONS and not
                                   (allow_mbtiles and ext == ".mbtiles")):
        raise PublishingError("Q2VT_PUB_FORBIDDEN_FILE", relative)


def scan_text_for_leaks(text: str, secrets: Iterable[str] = ()) -> List[str]:
    """Absolute local paths and known secret values found in ``text``."""
    problems = []
    if _ABS_PATH.search(text):
        problems.append("absolute local path")
    for secret in secrets:
        if secret and len(secret) >= 6 and secret in text:
            problems.append("credential value")
    if re.search(r"(?i)(aws_secret_access_key|password\s*=|dbname=|host=\S+\s+port=)", text):
        problems.append("connection string or secret key name")
    return problems


def scan_bundle(root: str, files: Iterable[str], secrets: Iterable[str] = (),
                canaries: Iterable[str] = ()) -> List[str]:
    """Leak scan of published files: text files for paths/secrets, every
    file (PMTiles tiles decoded) for canary values. Returns problems."""
    secrets = [s for s in secrets if s]
    canaries = [c for c in canaries if c]
    problems = []
    for relative in files:
        path = os.path.join(root, *relative.split("/"))
        ext = os.path.splitext(relative)[1].lower()
        if ext in TEXT_EXTENSIONS:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                text = handle.read()
            scan = scan_text_for_leaks(text, secrets)
            # Third-party bundles and the documents the user publishes (a decree
            # in .txt may quote a path): secret values only.
            if relative.startswith(("assets/vendor/", "assets/maplibre-gl", "docs/")):
                scan = [p for p in scan if p == "credential value"]
            problems.extend(f"{relative}: {p}" for p in scan)
            for canary in canaries:
                if canary in text:
                    problems.append(f"{relative}: canary value")
        elif ext == ".pmtiles" and canaries:
            with open_pmtiles(path) as archive:
                for address, data in archive.tiles():
                    raw = mvt.payload(data)
                    if any(c.encode("utf-8") in raw for c in canaries):
                        problems.append(f"{relative}: canary value in tile {address}")
                        break
        elif secrets or canaries:
            with open(path, "rb") as handle:
                blob = handle.read()
            for value in (*secrets, *canaries):
                if value.encode("utf-8") in blob:
                    problems.append(f"{relative}: secret/canary bytes")
                    break
    return problems
