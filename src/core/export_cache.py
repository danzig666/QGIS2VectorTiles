"""
Export cache: reuse the work of earlier exports for layers that did not change.

Two kinds of entries, both content-addressed (the key is a SHA-256 of
everything the result depends on, so a stale entry is never used — it just
stops being looked up):

* **datasets** — the GeoPackage the rule exporter writes for one output
  dataset (one rule / label / symbol layer of one QGIS layer), plus the
  diagnostics recorded while writing it. Key: the plugin code, the export
  settings (extent, zooms, field options, polygon label base, measurement
  settings, project/global variables, datum transformations), the layer's
  data source (path, provider, subset, CRS, feature order, feature key) with
  the size and modification time of its files, and the rule-group snapshot
  (filter, geometry expression, data-defined fields, materialization recipe).
  The dataset's *name* is not part of the key: reordering layers renames
  datasets but reuses them.
* **tiles** — the MBTiles of one QGIS layer's datasets (see
  tiles_generator), keyed by its datasets' keys and names, the fields the
  style needs, zoom ranges and the tiler settings.
* **rasters** (publishing) — the rendered image archive of one raster layer
  (publishing.raster_tiles.raster_cache_key): its files, style, extent,
  zooms and image settings.

Only file-based sources are cached (their files' size and modification time
show a change); database, web and memory layers are always exported. The
cache lives next to the publication output (``.q2vt-cache``), is shared by
all publications of that folder, and is pruned after every export: entries
unused for 30 days go first, then the least recently used beyond 4 GB.
"""

import dataclasses
import functools
import hashlib
import json
import os
import shutil
import time
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote as urllib_quote, unquote, urlparse

CACHE_VERSION = 1
MAX_AGE_DAYS = 30
MAX_BYTES = 4 * 1024 ** 3
_FILE_PROVIDERS = {"ogr", "delimitedtext", "spatialite", "gpx"}
_SIDE_CARS = (".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".sbn", ".sbx")


@functools.lru_cache(maxsize=1)
def code_fingerprint() -> str:
    """The exporter's own code (src/core, src/utils) and the GDAL/QGIS versions:
    a plugin update invalidates every entry."""
    digest = hashlib.sha256(f"q2vt-cache-{CACHE_VERSION}".encode())
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for folder in ("core", "utils"):
        for root, dirs, files in os.walk(os.path.join(src, folder)):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for name in sorted(files):
                if name.endswith(".py"):
                    path = os.path.join(root, name)
                    digest.update(os.path.relpath(path, src).encode())
                    with open(path, "rb") as handle:
                        digest.update(handle.read())
    try:
        from osgeo import gdal  # pylint: disable=import-outside-toplevel
        digest.update(gdal.VersionInfo("RELEASE_NAME").encode())
    except Exception:  # pylint: disable=broad-except
        pass
    try:
        from qgis.core import Qgis  # pylint: disable=import-outside-toplevel
        digest.update(str(Qgis.QGIS_VERSION_INT).encode())
    except Exception:  # pylint: disable=broad-except
        pass
    return digest.hexdigest()


def _source_files(provider: str, uri: str) -> Optional[List[str]]:
    """The files a layer reads, or None when it is not file based."""
    if provider not in _FILE_PROVIDERS:
        return None
    if provider == "delimitedtext" or uri.startswith("file:"):
        path = unquote(urlparse(uri).path)
        if os.name == "nt" and path.startswith("/") and len(path) > 2 and path[2] == ":":
            path = path[1:]
    elif provider == "spatialite":
        start = uri.find("dbname='")
        if start < 0:
            return None
        path = uri[start + 8:uri.find("'", start + 8)]
    else:
        path = uri.split("|")[0]
    if not path or not os.path.isfile(path):
        return None
    files = [path]
    stem, ext = os.path.splitext(path)
    if ext.lower() == ".shp":
        files += [stem + side for side in _SIDE_CARS[1:] if os.path.exists(stem + side)]
        files += [stem + side.upper() for side in _SIDE_CARS[1:] if os.path.exists(stem + side.upper())]
    return sorted(set(files))


def _gpkg_changes(path: str, table: Optional[str] = None) -> Optional[str]:
    """GeoPackage edit stamps (``gpkg_contents.last_change``, set by GDAL on
    every write) of ``table``, or of every table when the layer does not name
    one. Per table: an edit (or a style saved into the file) in one layer of
    a shared GeoPackage left the other layers looking changed. Read through
    SQLite, so edits still held in the ``-wal`` file count; the ``-wal`` file
    itself is not used (QGIS creates and removes it as layers open and close,
    which says nothing about edits)."""
    import sqlite3  # pylint: disable=import-outside-toplevel
    try:
        uri = "file:" + urllib_quote(os.path.abspath(path)) + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as conn:
            rows = []
            if table:
                rows = conn.execute("SELECT table_name, last_change FROM gpkg_contents "
                                    "WHERE lower(table_name) = lower(?)", (table,)).fetchall()
            if not rows:
                rows = conn.execute("SELECT table_name, last_change FROM gpkg_contents "
                                    "ORDER BY table_name").fetchall()
        return json.dumps(rows)
    except sqlite3.Error:
        return None


def _layer_name(uri: str) -> Optional[str]:
    """``layername=`` of an OGR source ("path.gpkg|layername=parcels")."""
    for part in uri.split("|")[1:]:
        key, _, value = part.partition("=")
        if key.strip().lower() == "layername" and value:
            return value
    return None


def source_fingerprint(provider: str, uri: str) -> Optional[str]:
    """Size and modification time of the source files (and a GeoPackage's
    edit stamps); None: do not cache."""
    files = _source_files(provider, uri)
    if files is None:
        return None
    parts = []
    for path in files:
        if path.lower().endswith(".gpkg"):
            # Only GeoPackage's own edit stamps: SQLite rewrites the file (time,
            # size) when QGIS merely opens and closes it, which made every
            # layer look changed in the user's QGIS.
            changes = _gpkg_changes(path, _layer_name(uri) if provider == "ogr" else None)
            if changes is None:
                return None
            parts.append(f"{os.path.abspath(path)}|{changes}")
            continue
        stat = os.stat(path)
        parts.append(f"{os.path.abspath(path)}|{stat.st_size}|{stat.st_mtime_ns}")
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def _plain(value):
    """JSON-safe form of snapshots (dataclasses, tuples, enums, Qt values)."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_plain(v) for v in value]
        return sorted(items, key=json.dumps) if isinstance(value, (set, frozenset)) else items
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return repr(value)


UNSTABLE = object()


def stable_value(value):
    """A value that prints the same in every QGIS session (expression
    variables): plain values, layers by id, lists of these; anything else
    (objects printed with their memory address) is UNSTABLE and left out."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        items = [stable_value(item) for item in value]
        return UNSTABLE if any(item is UNSTABLE for item in items) else items
    if isinstance(value, dict):
        items = {str(k): stable_value(v) for k, v in value.items()}
        return UNSTABLE if any(v is UNSTABLE for v in items.values()) else items
    identifier = getattr(value, "id", None)
    if callable(identifier):
        try:
            return f"id:{identifier()}"
        except Exception:  # pylint: disable=broad-except
            return UNSTABLE
    for method in ("toString", "isoformat"):  # QDate/QDateTime, datetime
        if callable(getattr(value, method, None)):
            try:
                return f"{type(value).__name__}:{getattr(value, method)()}"
            except Exception:  # pylint: disable=broad-except
                return UNSTABLE
    return UNSTABLE


def part_hash(part) -> str:
    """Digest of one key part (to compare exports part by part)."""
    return hashlib.sha256(json.dumps(_plain(part), sort_keys=True, ensure_ascii=False,
                                     default=repr).encode("utf-8")).hexdigest()[:16]


def miss_reasons(before: dict, now: dict, context_before: dict, context_now: dict) -> List[str]:
    """Why a dataset's key changed, in words."""
    if not before:
        return ["first export into this folder (or a new layer / rule)"]
    reasons = []
    if before.get("code") != now.get("code"):
        reasons.append("plugin, QGIS or GDAL updated")
    if before.get("source") != now.get("source"):
        reasons.append("layer data changed")
    if "key" in before and before.get("key") != now.get("key"):
        reasons.append("feature key (unique id) changed")
    if before.get("rule") != now.get("rule"):
        reasons.append("style, labels or fields changed")
    if before.get("context") != now.get("context"):
        changed = sorted(k for k in set(context_before) | set(context_now)
                         if context_before.get(k) != context_now.get(k))
        if "variables" in changed and isinstance(context_now.get("variables"), dict):
            names = sorted(k for k in set(context_before.get("variables") or {})
                           | set(context_now["variables"])
                           if (context_before.get("variables") or {}).get(k) != context_now["variables"].get(k))
            changed = [c for c in changed if c != "variables"] + [f"@{n}" for n in names[:5]]
        reasons.append("export settings changed (" + ", ".join(changed[:6]) + ")")
    return reasons or ["unknown"]


def make_key(*parts) -> str:
    payload = json.dumps([code_fingerprint()] + [_plain(p) for p in parts], sort_keys=True,
                         ensure_ascii=False, default=repr)
    key = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    debug = os.environ.get("Q2VT_CACHE_DEBUG")  # a folder: what each key was made of
    if debug:
        os.makedirs(debug, exist_ok=True)
        with open(os.path.join(debug, f"{key}.json"), "w", encoding="utf-8") as handle:
            handle.write(payload)
    return key


class ExportCache:
    """Datasets and tiles of earlier exports under ``root``."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.hits = {"datasets": 0, "tiles": 0}
        self.misses = {"datasets": 0, "tiles": 0}

    # -- paths -------------------------------------------------------------------
    def _path(self, kind: str, key: str, ext: str) -> str:
        return os.path.join(self.root, kind, key[:2], f"{key}{ext}")

    def _touch(self, *paths: str) -> None:
        now = time.time()
        for path in paths:
            try:
                os.utime(path, (now, now))
            except OSError:
                pass

    def _put_file(self, source: str, target: str) -> None:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        temp = f"{target}.{os.getpid()}.tmp"
        shutil.copyfile(source, temp)
        os.replace(temp, target)

    def _put_json(self, data: dict, target: str) -> None:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        temp = f"{target}.{os.getpid()}.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
        os.replace(temp, target)

    # -- datasets ----------------------------------------------------------------
    def get_dataset(self, key: str, target: str) -> Optional[dict]:
        """Copy the dataset of ``key`` to ``target``; returns its metadata
        (``{"empty": bool, "diagnostics": [...]}``) or None on a miss."""
        meta_path = self._path("datasets", key, ".json")
        data_path = self._path("datasets", key, ".gpkg")
        try:
            with open(meta_path, encoding="utf-8") as handle:
                meta = json.load(handle)
            if not meta.get("empty"):
                shutil.copyfile(data_path, target)
        except (OSError, ValueError):
            self.misses["datasets"] += 1
            return None
        self._touch(meta_path, data_path)
        self.hits["datasets"] += 1
        return meta

    def put_dataset(self, key: str, source: Optional[str], diagnostics: Iterable[dict]) -> None:
        """Keep the dataset written for ``key`` (None / missing file: an empty result)."""
        empty = not source or not os.path.exists(source)
        try:
            if not empty:
                self._put_file(source, self._path("datasets", key, ".gpkg"))
            self._put_json({"empty": empty, "diagnostics": list(diagnostics)},
                           self._path("datasets", key, ".json"))
        except OSError:
            pass  # a full disk only costs the reuse next time

    # -- tiles -------------------------------------------------------------------
    def get_tiles(self, key: str) -> Optional[str]:
        path = self._path("tiles", key, ".mbtiles")
        if os.path.exists(path):
            self._touch(path)
            self.hits["tiles"] += 1
            return path
        self.misses["tiles"] += 1
        return None

    def put_tiles(self, key: str, source: str) -> str:
        target = self._path("tiles", key, ".mbtiles")
        try:
            self._put_file(source, target)
        except OSError:
            return source
        return target

    # -- bundles (several files + metadata, e.g. the parcel report) -------------
    def get_bundle(self, kind: str, key: str) -> Optional[Tuple[dict, str]]:
        """(metadata, folder of the files) of an entry, or None."""
        folder = os.path.join(self.root, kind, key[:2], key)
        meta_path = os.path.join(folder, "meta.json")
        try:
            with open(meta_path, encoding="utf-8") as handle:
                meta = json.load(handle)
        except (OSError, ValueError):
            return None
        files = [os.path.join(folder, "files", *name.split("/")) for name in meta.get("_files", [])]
        if not all(os.path.exists(path) for path in files):  # partly pruned
            return None
        self._touch(meta_path, *files)
        return meta, os.path.join(folder, "files")

    def put_bundle(self, kind: str, key: str, meta: dict, files: Dict[str, str]) -> None:
        """Keep ``files`` ({relative name: local path}) and ``meta``."""
        folder = os.path.join(self.root, kind, key[:2], key)
        temp = f"{folder}.{os.getpid()}.tmp"
        try:
            shutil.rmtree(temp, ignore_errors=True)
            for name, path in files.items():
                target = os.path.join(temp, "files", *name.split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(path, target)
            os.makedirs(temp, exist_ok=True)
            with open(os.path.join(temp, "meta.json"), "w", encoding="utf-8") as handle:
                json.dump(dict(meta, _files=sorted(files)), handle, ensure_ascii=False)
            shutil.rmtree(folder, ignore_errors=True)
            os.replace(temp, folder)
        except OSError:
            shutil.rmtree(temp, ignore_errors=True)

    # -- housekeeping ------------------------------------------------------------
    def entries(self) -> List[Tuple[float, int, str]]:
        found = []
        for kind in ("datasets", "tiles", "parcels", "rasters"):
            for root, _, files in os.walk(os.path.join(self.root, kind)):
                for name in files:
                    path = os.path.join(root, name)
                    try:
                        stat = os.stat(path)
                    except OSError:
                        continue
                    found.append((stat.st_mtime, stat.st_size, path))
        return found

    def size(self) -> int:
        return sum(size for _, size, _ in self.entries())

    def prune(self, max_bytes: int = MAX_BYTES, max_age_days: float = MAX_AGE_DAYS) -> int:
        """Delete entries unused for ``max_age_days``, then the least
        recently used beyond ``max_bytes``. Returns the number of files removed."""
        entries = sorted(self.entries())
        cutoff = time.time() - max_age_days * 86400
        total = sum(size for _, size, _ in entries)
        removed = 0
        for used, size, path in entries:
            if used >= cutoff and total <= max_bytes:
                break
            if path.endswith(".tmp") or used < cutoff or total > max_bytes:
                try:
                    os.remove(path)
                    removed += 1
                    total -= size
                except OSError:
                    pass
        return removed

    # -- why a dataset was redone ---------------------------------------------------
    LAST = "last-keys.json"

    def last_components(self) -> dict:
        """The key parts of each dataset of the previous export (to tell why
        a dataset is redone)."""
        try:
            with open(os.path.join(self.root, self.LAST), encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {}

    def save_components(self, data: dict) -> None:
        os.makedirs(self.root, exist_ok=True)
        path = os.path.join(self.root, self.LAST)
        with open(path + ".tmp", "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, sort_keys=True)
        os.replace(path + ".tmp", path)

    def clear(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def summary(self) -> str:
        return (f"cache: {self.hits['datasets']} of "
                f"{self.hits['datasets'] + self.misses['datasets']} datasets and "
                f"{self.hits['tiles']} of {self.hits['tiles'] + self.misses['tiles']} "
                "layer tile sets reused")


def canonical_xml(text: str) -> str:
    """XML with sorted attributes, also inside attribute values that hold
    XML themselves (QGIS stores e.g. a callout's line symbol that way)."""
    import xml.etree.ElementTree as ET  # pylint: disable=import-outside-toplevel
    root = ET.fromstring(text.split("?>", 1)[-1] if text.lstrip().startswith("<?") else
                         "\n".join(line for line in text.splitlines() if not line.startswith("<!DOCTYPE")))
    for element in root.iter():
        for name, value in list(element.attrib.items()):
            if value.lstrip().startswith("<"):
                try:
                    element.set(name, canonical_xml(value))
                except ET.ParseError:
                    pass
    return ET.canonicalize(ET.tostring(root, encoding="unicode"), strip_text=True)


def layer_state(layer) -> Optional[dict]:
    """What a layer's derived outputs depend on: its source files and its
    style (renderer, labels). None when the source is not file based."""
    fingerprint = source_fingerprint(layer.providerType(), layer.source())
    if fingerprint is None:
        return None
    from qgis.core import QgsReadWriteContext  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtXml import QDomDocument  # pylint: disable=import-outside-toplevel
    doc = QDomDocument("q2vt")
    root = doc.createElement("style")
    doc.appendChild(root)
    layer.writeSymbology(root, doc, "", QgsReadWriteContext())
    # Qt writes attributes in a different order in every session: canonical
    # XML (sorted attributes) so that an unchanged style has the same key.
    try:
        style = canonical_xml(doc.toString())
    except Exception:  # pylint: disable=broad-except
        return None
    return {"source": layer.source(), "provider": layer.providerType(), "data": fingerprint,
            "crs": layer.crs().toWkt(), "style": style}


def diagnostics_to_dicts(items) -> List[dict]:
    out = []
    for diag in items:
        data = dataclasses.asdict(diag)
        data["severity"] = getattr(diag.severity, "value", diag.severity)
        out.append(data)
    return out


def replay_diagnostics(collector, items: Iterable[dict], old_name: str, new_name: str) -> None:
    """Diagnostics stored with a dataset, re-added under its current name."""
    try:
        from .fidelity.diagnostics import Diagnostic, Severity  # pylint: disable=import-outside-toplevel
    except ImportError:  # imported as a top-level module (unit tests)
        from fidelity.diagnostics import Diagnostic, Severity  # pylint: disable=import-outside-toplevel
    restored = []
    for data in items:
        data = dict(data)
        data["severity"] = Severity(data.get("severity", "info"))
        if data.get("component") == old_name:
            data["component"] = new_name
        try:
            restored.append(Diagnostic(**data))
        except TypeError:
            continue
    collector.extend(restored)


def source_layer_groups(rules) -> Dict[str, List[str]]:
    """{QGIS layer id: [output dataset names]} of flattened rules (with the
    polygon datasets of visible-polygon labels)."""
    groups: Dict[str, List[str]] = {}
    for rule in rules:
        names = groups.setdefault(rule.layer.id(), [])
        for name in (rule.output_dataset, getattr(rule, "visible_polygons", None)):
            if name and name not in names:
                names.append(name)
    return groups
