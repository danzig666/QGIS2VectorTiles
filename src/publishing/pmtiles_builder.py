"""
MBTiles -> PMTiles v3 packaging of the *same* MVT tiles.

The fork's tile generator writes MBTiles (GDAL MVT driver, TMS rows, gzip
payloads). This module repackages exactly those tile payloads into a PMTiles
v3 archive with the official PMTiles Python library (vendored, see
vendor/VENDOR.md); it never decodes or rebuilds geometry and never re-runs
tiling.

* The input database is opened read-only and never modified.
* Tile addresses are converted TMS row -> XYZ y and to PMTiles tile ids with
  the official ``zxy_to_tileid``; the sort runs in a temporary on-disk SQLite
  index, payloads are streamed in tile-id order (no archive in RAM).
* Identical payloads are stored once (keyed by SHA-256, not Python ``hash``).
* Cancellation is checked between tiles; partial output is deleted; the
  archive is validated before it is renamed to its final name.
"""

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import mvt
from .errors import Cancelled, PublishingError
from .progress import Progress
from .vendor.pmtiles.tile import Compression, TileType, zxy_to_tileid
from .vendor.pmtiles.writer import Writer

PMTILES_LIBRARY_VERSION = "3.8.1"  # vendor/pmtiles, see vendor/VENDOR.md
WEB_MERCATOR_LAT = 85.0511287798066


@dataclass(frozen=True)
class PmtilesOptions:
    overwrite: bool = False          # refuse to replace an existing archive by default
    validate: bool = True            # validate the finished archive before renaming it
    sample_tiles: int = 64           # MVT payloads decoded during validation (0 = all)
    temp_dir: Optional[str] = None   # address index location (default: next to the output)
    name: Optional[str] = None       # metadata "name" (default: the MBTiles name)


@dataclass
class ArchiveDescriptor:
    """What a finished tile archive holds (no tile data)."""

    path: str
    format: str                      # "pmtiles" | "mbtiles"
    tile_type: str = "mvt"
    tile_compression: str = "gzip"
    min_zoom: int = 0
    max_zoom: int = 0
    bounds: Tuple[float, float, float, float] = (-180.0, -WEB_MERCATOR_LAT, 180.0, WEB_MERCATOR_LAT)
    center: Tuple[float, float, int] = (0.0, 0.0, 0)
    addressed_tiles: int = 0
    tile_contents: int = 0
    size_bytes: int = 0
    sha256: str = ""
    vector_layers: List[str] = field(default_factory=list)
    zoom_counts: Dict[int, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def public_dict(self) -> dict:
        """Path-free description for manifests."""
        return {
            "format": self.format, "tileType": self.tile_type,
            "tileCompression": self.tile_compression,
            "minTileZoom": self.min_zoom, "maxTileZoom": self.max_zoom,
            "bounds": list(self.bounds), "center": list(self.center),
            "addressedTiles": self.addressed_tiles, "sizeBytes": self.size_bytes,
            "sha256": self.sha256, "vectorLayers": list(self.vector_layers),
        }


def sqlite_readonly_uri(path: str) -> str:
    """``file:`` URI opening ``path`` read-only (Unicode, spaces, Windows)."""
    return Path(path).resolve().as_uri() + "?mode=ro"


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


# --- MBTiles preflight -------------------------------------------------------

@dataclass
class MbtilesInfo:
    metadata: Dict[str, str]
    json_metadata: dict
    min_zoom: int
    max_zoom: int
    tile_count: int
    zoom_counts: Dict[int, int]
    gzip: bool
    warnings: List[str]


def _parse_bounds(text: Optional[str]) -> Optional[Tuple[float, float, float, float]]:
    try:
        values = tuple(float(v.strip()) for v in (text or "").split(","))
    except ValueError:
        return None
    if len(values) != 4:
        return None
    west, south, east, north = values
    if not (-180 <= west <= 180 and -180 <= east <= 180 and -90 <= south <= north <= 90):
        return None
    return values  # type: ignore[return-value]


def preflight_mbtiles(path: str) -> MbtilesInfo:
    """Read-only checks: MVT format, tile relation, address ranges, duplicates."""
    if not os.path.isfile(path):
        raise PublishingError("Q2VT_PUB_MBTILES_INVALID", f"No MBTiles file at {path}.")
    try:
        conn = sqlite3.connect(sqlite_readonly_uri(path), uri=True)
    except sqlite3.Error as error:
        raise PublishingError("Q2VT_PUB_MBTILES_INVALID", str(error)) from error
    warnings: List[str] = []
    try:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
        if "tiles" not in names or "metadata" not in names:
            raise PublishingError("Q2VT_PUB_MBTILES_INVALID",
                                  "The MBTiles file has no 'tiles' or 'metadata' relation.")
        metadata = {str(k): ("" if v is None else str(v))
                    for k, v in conn.execute("SELECT name, value FROM metadata")}
        json_metadata: dict = {}
        if metadata.get("json"):
            try:
                parsed = json.loads(metadata["json"])
                json_metadata = parsed if isinstance(parsed, dict) else {}
            except ValueError:
                warnings.append("MBTiles 'json' metadata is not valid JSON; vector_layers "
                                "taken from the tiles instead.")
        row = conn.execute("SELECT MIN(zoom_level), MAX(zoom_level), COUNT(*) FROM tiles").fetchone()
        if not row or not row[2]:
            raise PublishingError("Q2VT_PUB_EMPTY_ARCHIVE")
        min_zoom, max_zoom, count = int(row[0]), int(row[1]), int(row[2])
        if min_zoom < 0 or max_zoom > 30:
            raise PublishingError("Q2VT_PUB_MBTILES_INVALID",
                                  f"Tile zooms {min_zoom}-{max_zoom} are outside 0-30.")
        bad = conn.execute(
            "SELECT zoom_level, tile_column, tile_row FROM tiles WHERE tile_column < 0 "
            "OR tile_row < 0 OR tile_column >= (1 << zoom_level) OR tile_row >= (1 << zoom_level) "
            "LIMIT 1").fetchone()
        if bad:
            raise PublishingError("Q2VT_PUB_MBTILES_INVALID", f"Tile address out of range: {bad}.")
        duplicate = conn.execute(
            "SELECT zoom_level, tile_column, tile_row FROM tiles GROUP BY zoom_level, "
            "tile_column, tile_row HAVING COUNT(*) > 1 LIMIT 1").fetchone()
        if duplicate:
            raise PublishingError("Q2VT_PUB_MBTILES_INVALID", f"Duplicate tile address {duplicate}.")
        zoom_counts = {int(z): int(n) for z, n in conn.execute(
            "SELECT zoom_level, COUNT(*) FROM tiles GROUP BY zoom_level")}
        tile_format = metadata.get("format", "").lower()
        sample = conn.execute("SELECT tile_data FROM tiles WHERE length(tile_data) > 0 "
                              "LIMIT 1").fetchone()
        is_gzip = bool(sample) and bytes(sample[0][:2]) == b"\x1f\x8b"
        if tile_format not in ("pbf", "mvt"):
            if tile_format:
                raise PublishingError("Q2VT_PUB_NOT_MVT",
                                      f"MBTiles format is '{tile_format}', not pbf (MVT).")
            warnings.append("MBTiles metadata has no 'format'; tiles decoded to confirm MVT.")
        if sample is not None:
            try:
                mvt.decode(bytes(sample[0]), properties=False)
            except mvt.MvtDecodeError as error:
                raise PublishingError("Q2VT_PUB_NOT_MVT", f"First tile is not MVT: {error}") from error
        if metadata.get("bounds") and _parse_bounds(metadata["bounds"]) is None:
            warnings.append(f"MBTiles bounds '{metadata['bounds']}' are invalid; "
                            "computed from the tile coverage.")
        return MbtilesInfo(metadata, json_metadata, min_zoom, max_zoom, count, zoom_counts,
                           is_gzip, warnings)
    except sqlite3.Error as error:
        raise PublishingError("Q2VT_PUB_MBTILES_INVALID", str(error)) from error
    finally:
        conn.close()


# --- conversion ----------------------------------------------------------------

class _DigestWriter(Writer):
    """The official writer, deduplicating identical payloads by SHA-256
    (the upstream writer keys on Python's 64-bit ``hash`` without comparing
    bytes) and keeping its temporary tile data in ``temp_dir``."""

    def __init__(self, f, temp_dir: Optional[str] = None):
        super().__init__(f)
        self.tile_f.close()
        self.tile_f = tempfile.TemporaryFile(dir=temp_dir)

    def write_tile(self, tileid, data):
        key = hashlib.sha256(data).digest()
        if self.tile_entries and tileid < self.tile_entries[-1].tile_id:
            self.clustered = False
        from .vendor.pmtiles.tile import Entry  # pylint: disable=import-outside-toplevel
        if key in self.hash_to_offset:
            last = self.tile_entries[-1] if self.tile_entries else None
            found = self.hash_to_offset[key]
            if last is not None and tileid == last.tile_id + last.run_length and last.offset == found:
                last.run_length += 1
            else:
                self.tile_entries.append(Entry(tileid, found, len(data), 1))
        else:
            self.tile_f.write(data)
            self.tile_entries.append(Entry(tileid, self.offset, len(data), 1))
            self.hash_to_offset[key] = self.offset
            self.offset += len(data)
        self.addressed_tiles += 1


def _tile_bounds(z: int, x: int, y: int) -> Tuple[float, float, float, float]:
    import math  # pylint: disable=import-outside-toplevel
    n = 2 ** z

    def lat(row):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * row / n))))
    return x / n * 360 - 180, lat(y + 1), (x + 1) / n * 360 - 180, lat(y)


def _normalized_metadata(info: MbtilesInfo, vector_layers: List[dict], name: Optional[str]) -> dict:
    """PMTiles JSON metadata: MBTiles fields + the parsed ``json`` content."""
    meta: dict = {}
    for key in ("name", "description", "attribution", "version", "type"):
        if info.metadata.get(key):
            meta[key] = info.metadata[key]
    if name:
        meta["name"] = name
    meta["format"] = "pbf"
    meta["vector_layers"] = vector_layers
    if isinstance(info.json_metadata.get("tilestats"), dict):
        meta["tilestats"] = info.json_metadata["tilestats"]
    meta["generator"] = f"QWebMap via pmtiles-python {PMTILES_LIBRARY_VERSION}"
    return meta


def build_pmtiles(input_mbtiles: str, output_pmtiles: str,
                  options: Optional[PmtilesOptions] = None,
                  feedback=None) -> ArchiveDescriptor:
    """Repackage ``input_mbtiles`` as ``output_pmtiles`` (see module doc).

    ``feedback``: a Progress, a QgsFeedback-like object or a callable.
    """
    options = options or PmtilesOptions()
    progress = feedback if isinstance(feedback, Progress) else Progress(feedback)
    output = os.path.abspath(output_pmtiles)
    if os.path.exists(output) and not options.overwrite:
        raise PublishingError("Q2VT_PUB_PATH_UNSAFE",
                              f"{os.path.basename(output)} already exists; archives are immutable.")
    info = preflight_mbtiles(input_mbtiles)
    progress.update(0.01, f"Packaging {info.tile_count} vector tiles as PMTiles...", force=True)
    out_dir = os.path.dirname(output) or "."
    os.makedirs(out_dir, exist_ok=True)
    temp_dir = options.temp_dir or out_dir
    partial = os.path.join(out_dir, f".{os.path.basename(output)}.{uuid.uuid4().hex[:8]}.partial")
    index_path = os.path.join(temp_dir, f".q2vt_index_{uuid.uuid4().hex[:8]}.sqlite")
    source = sqlite3.connect(sqlite_readonly_uri(input_mbtiles), uri=True)
    index = sqlite3.connect(index_path)
    seen_layers: Dict[str, dict] = {}
    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    try:
        # 1. Address index on disk, sorted by PMTiles tile id.
        index.execute("PRAGMA journal_mode = OFF")
        index.execute("PRAGMA synchronous = OFF")
        index.execute("CREATE TABLE ids (tile_id INTEGER PRIMARY KEY, z INTEGER, x INTEGER, "
                      "row INTEGER)")
        batch, done = [], 0
        for z, x, row in source.execute("SELECT zoom_level, tile_column, tile_row FROM tiles"):
            y = (1 << z) - 1 - row
            batch.append((zxy_to_tileid(z, x, y), z, x, row))
            if z == info.max_zoom:
                minx, maxx = min(minx, x), max(maxx, x)
                miny, maxy = min(miny, y), max(maxy, y)
            if len(batch) >= 5000:
                index.executemany("INSERT INTO ids VALUES (?, ?, ?, ?)", batch)
                done += len(batch)
                batch = []
                progress.check()
                progress.update(0.05 * done / info.tile_count)
        if batch:
            index.executemany("INSERT INTO ids VALUES (?, ?, ?, ?)", batch)
        index.commit()

        # 2. Stream payloads in tile-id order through the official writer.
        with open(partial, "wb") as handle:
            writer = _DigestWriter(handle, temp_dir)
            try:
                written = 0
                for tile_id, z, x, row in index.execute(
                        "SELECT tile_id, z, x, row FROM ids ORDER BY tile_id"):
                    found = source.execute(
                        "SELECT tile_data FROM tiles WHERE zoom_level = ? AND tile_column = ? "
                        "AND tile_row = ?", (z, x, row)).fetchone()
                    data = bytes(found[0]) if found and found[0] is not None else b""
                    if data[:2] != b"\x1f\x8b":
                        data = gzip.compress(data, compresslevel=9, mtime=0)
                    try:  # every tile: layer names and keys (features not decoded)
                        for name, keys in mvt.layer_summary(data).items():
                            entry = seen_layers.setdefault(name, {"keys": set(), "zooms": [z, z]})
                            entry["keys"].update(keys)
                            entry["zooms"] = [min(entry["zooms"][0], z), max(entry["zooms"][1], z)]
                    except mvt.MvtDecodeError as error:
                        raise PublishingError(
                            "Q2VT_PUB_NOT_MVT", f"Tile {z}/{x}/{(1 << z) - 1 - row} is not "
                            f"MVT: {error}") from error
                    writer.write_tile(tile_id, data)
                    written += 1
                    if written % 256 == 0:
                        progress.check()
                        progress.update(0.05 + 0.80 * written / info.tile_count,
                                        f"PMTiles: {written}/{info.tile_count} tiles")
                bounds = _parse_bounds(info.metadata.get("bounds"))
                if bounds is None:
                    west, _, _, north = _tile_bounds(info.max_zoom, minx, miny)
                    _, south, east, _ = _tile_bounds(info.max_zoom, maxx, maxy)
                    bounds = (west, south, east, north)
                center = _center(info.metadata.get("center"), bounds, info.min_zoom, info.max_zoom)
                vector_layers = _complete_vector_layers(
                    info.json_metadata.get("vector_layers"), seen_layers, info.warnings)
                header = {
                    "tile_type": TileType.MVT,
                    "tile_compression": Compression.GZIP,
                    "min_lon_e7": int(round(bounds[0] * 1e7)),
                    "min_lat_e7": int(round(bounds[1] * 1e7)),
                    "max_lon_e7": int(round(bounds[2] * 1e7)),
                    "max_lat_e7": int(round(bounds[3] * 1e7)),
                    "center_lon_e7": int(round(center[0] * 1e7)),
                    "center_lat_e7": int(round(center[1] * 1e7)),
                    "center_zoom": center[2],
                }
                progress.update(0.86, "PMTiles: writing directories...", force=True)
                progress.check()
                writer.finalize(header, _normalized_metadata(info, vector_layers, options.name))
            finally:
                if not writer.tile_f.closed:
                    writer.tile_f.close()
            handle.flush()
            os.fsync(handle.fileno())
        descriptor = ArchiveDescriptor(
            path=output, format="pmtiles", min_zoom=info.min_zoom, max_zoom=info.max_zoom,
            bounds=tuple(bounds), center=center, addressed_tiles=writer.addressed_tiles,
            tile_contents=len(writer.hash_to_offset),
            vector_layers=[layer.get("id", "") for layer in vector_layers],
            zoom_counts=dict(info.zoom_counts), warnings=list(info.warnings))
        if options.validate:
            progress.update(0.9, "PMTiles: validating...", force=True)
            from .validation import validate_pmtiles  # pylint: disable=import-outside-toplevel
            validate_pmtiles(partial, expected_tiles=info.tile_count,
                             sample=options.sample_tiles, progress=progress.sub(0.9, 0.99))
        descriptor.size_bytes = os.path.getsize(partial)
        descriptor.sha256 = sha256_file(partial)
        if os.path.exists(output):  # options.overwrite
            os.remove(output)
        os.replace(partial, output)
        progress.update(1.0, f"PMTiles archive: {os.path.basename(output)} "
                             f"({descriptor.size_bytes} bytes)", force=True)
        return descriptor
    except Cancelled:
        raise
    except OSError as error:
        raise PublishingError("Q2VT_PUB_DISK", str(error)) from error
    finally:
        source.close()
        index.close()
        for leftover in (index_path, partial):
            try:
                if os.path.exists(leftover):
                    os.remove(leftover)
            except OSError:
                pass


def _complete_vector_layers(declared, seen: Dict[str, dict], warnings: List[str]) -> List[dict]:
    """MBTiles ``vector_layers`` completed with every layer found in the
    tiles (GDAL truncates the ``json`` metadata of archives with many
    layers). Declared entries keep their fields/zooms; found-only layers get
    their keys (type "String": unknown) and the zooms they occur at."""
    result, known = [], set()
    for layer in declared if isinstance(declared, list) else []:
        if isinstance(layer, dict) and layer.get("id") and layer["id"] not in known:
            result.append(dict(layer))
            known.add(layer["id"])
    missing = sorted(set(seen) - known)
    for name in missing:
        info = seen[name]
        result.append({"id": name, "fields": {key: "String" for key in sorted(info["keys"])},
                       "minzoom": info["zooms"][0], "maxzoom": info["zooms"][1]})
    if missing:
        warnings.append(f"{len(missing)} source layer(s) missing from the MBTiles metadata "
                        "were added from the tiles.")
    return result


def _center(text: Optional[str], bounds, min_zoom: int, max_zoom: int) -> Tuple[float, float, int]:
    try:
        parts = [float(v) for v in (text or "").split(",")]
        if len(parts) >= 2:
            zoom = int(parts[2]) if len(parts) > 2 else min_zoom
            return parts[0], parts[1], max(min_zoom, min(max_zoom, zoom))
    except ValueError:
        pass
    return (bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2, min_zoom


def copy_mbtiles(input_mbtiles: str, output: str) -> ArchiveDescriptor:
    """The MBTiles archive itself as a bundle output (read-only checked copy)."""
    info = preflight_mbtiles(input_mbtiles)
    if os.path.exists(output):
        raise PublishingError("Q2VT_PUB_PATH_UNSAFE", f"{os.path.basename(output)} already exists.")
    partial = output + ".partial"
    shutil.copyfile(input_mbtiles, partial)
    os.replace(partial, output)
    bounds = _parse_bounds(info.metadata.get("bounds")) or \
        (-180.0, -WEB_MERCATOR_LAT, 180.0, WEB_MERCATOR_LAT)
    layers = info.json_metadata.get("vector_layers") or []
    return ArchiveDescriptor(
        path=output, format="mbtiles", tile_compression="gzip" if info.gzip else "none",
        min_zoom=info.min_zoom, max_zoom=info.max_zoom, bounds=bounds,
        center=_center(info.metadata.get("center"), bounds, info.min_zoom, info.max_zoom),
        addressed_tiles=info.tile_count, tile_contents=info.tile_count,
        size_bytes=os.path.getsize(output), sha256=sha256_file(output),
        vector_layers=[layer.get("id", "") for layer in layers if isinstance(layer, dict)],
        zoom_counts=info.zoom_counts, warnings=info.warnings)


# --- image tiles (QGIS raster layers) and copied extracts ---------------------------

IMAGE_TILE_TYPES = {"png": TileType.PNG, "jpeg": TileType.JPEG, "webp": TileType.WEBP}


class TileSink:
    """Collects tiles in any order in a temporary on-disk SQLite store and
    writes them as a clustered PMTiles v3 archive (tile-id order) with the
    official writer. Nothing is kept in RAM; ``close()`` removes the store."""

    def __init__(self, output: str, temp_dir: Optional[str] = None):
        self.output = os.path.abspath(output)
        if os.path.exists(self.output):
            raise PublishingError("Q2VT_PUB_PATH_UNSAFE",
                                  f"{os.path.basename(self.output)} already exists; archives are immutable.")
        self.temp_dir = temp_dir or os.path.dirname(self.output) or "."
        os.makedirs(self.temp_dir, exist_ok=True)
        self.store_path = os.path.join(self.temp_dir, f".q2vt_tiles_{uuid.uuid4().hex[:8]}.sqlite")
        self.db = sqlite3.connect(self.store_path)
        self.db.execute("PRAGMA journal_mode = OFF")
        self.db.execute("PRAGMA synchronous = OFF")
        self.db.execute("CREATE TABLE t (tile_id INTEGER PRIMARY KEY, data BLOB)")
        self.count = 0
        self.zooms: Dict[int, int] = {}
        self.extent: Dict[int, List[int]] = {}   # z -> [minx, miny, maxx, maxy]

    def add(self, z: int, x: int, y: int, data: bytes) -> None:
        self.db.execute("INSERT OR REPLACE INTO t VALUES (?, ?)", (zxy_to_tileid(z, x, y), data))
        self.count += 1
        self.zooms[z] = self.zooms.get(z, 0) + 1
        box = self.extent.setdefault(z, [x, y, x, y])
        box[0], box[1] = min(box[0], x), min(box[1], y)
        box[2], box[3] = max(box[2], x), max(box[3], y)
        if self.count % 2000 == 0:
            self.db.commit()

    def write(self, tile_type, compression, metadata: dict, bounds=None,
              progress: Optional[Progress] = None, validate_kind: str = "image") -> ArchiveDescriptor:
        progress = progress or Progress()
        self.db.commit()
        if not self.count:
            raise PublishingError("Q2VT_PUB_EMPTY_ARCHIVE")
        min_zoom, max_zoom = min(self.zooms), max(self.zooms)
        if bounds is None:
            minx, miny, maxx, maxy = self.extent[max_zoom]
            west, _, _, north = _tile_bounds(max_zoom, minx, miny)
            _, south, east, _ = _tile_bounds(max_zoom, maxx, maxy)
            bounds = (west, south, east, north)
        center = ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2, min_zoom)
        partial = os.path.join(os.path.dirname(self.output),
                               f".{os.path.basename(self.output)}.{uuid.uuid4().hex[:8]}.partial")
        try:
            with open(partial, "wb") as handle:
                writer = _DigestWriter(handle, self.temp_dir)
                try:
                    for done, (tile_id, data) in enumerate(
                            self.db.execute("SELECT tile_id, data FROM t ORDER BY tile_id")):
                        writer.write_tile(tile_id, bytes(data))
                        if done % 512 == 0:
                            progress.check()
                            progress.update(0.8 * done / self.count)
                    header = {
                        "tile_type": tile_type, "tile_compression": compression,
                        "min_lon_e7": int(round(bounds[0] * 1e7)), "min_lat_e7": int(round(bounds[1] * 1e7)),
                        "max_lon_e7": int(round(bounds[2] * 1e7)), "max_lat_e7": int(round(bounds[3] * 1e7)),
                        "center_lon_e7": int(round(center[0] * 1e7)),
                        "center_lat_e7": int(round(center[1] * 1e7)), "center_zoom": center[2],
                    }
                    meta = dict(metadata)
                    meta.setdefault("generator",
                                    f"QWebMap via pmtiles-python {PMTILES_LIBRARY_VERSION}")
                    writer.finalize(header, meta)
                finally:
                    if not writer.tile_f.closed:
                        writer.tile_f.close()
                handle.flush()
                os.fsync(handle.fileno())
            from .validation import validate_pmtiles  # pylint: disable=import-outside-toplevel
            validate_pmtiles(partial, expected_tiles=self.count, sample=64, kind=validate_kind,
                             progress=progress.sub(0.85, 0.99))
            descriptor = ArchiveDescriptor(
                path=self.output, format="pmtiles",
                tile_type={TileType.MVT: "mvt", TileType.PNG: "png", TileType.JPEG: "jpeg",
                           TileType.WEBP: "webp"}[tile_type],
                tile_compression="gzip" if compression == Compression.GZIP else "none",
                min_zoom=min_zoom, max_zoom=max_zoom, bounds=tuple(bounds), center=center,
                addressed_tiles=writer.addressed_tiles, tile_contents=len(writer.hash_to_offset),
                vector_layers=[layer.get("id", "") for layer in metadata.get("vector_layers", [])
                               if isinstance(layer, dict)],
                zoom_counts=dict(self.zooms), size_bytes=os.path.getsize(partial),
                sha256=sha256_file(partial))
            os.replace(partial, self.output)
            return descriptor
        except OSError as error:
            raise PublishingError("Q2VT_PUB_DISK", str(error)) from error
        finally:
            if os.path.exists(partial):
                os.remove(partial)

    def close(self) -> None:
        try:
            self.db.close()
        finally:
            if os.path.exists(self.store_path):
                os.remove(self.store_path)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
