"""
Immutable local web releases (PUB-06) of a vector-tile web map.

Publication folder layout::

    <publication>/
      index.html                 stable entry (bootstrap, follows current.json)
      bootstrap.<hash>.mjs
      current.json               small mutable pointer, replaced atomically
      releases/<release-id>/     immutable, directly openable release
        index.html manifest.json style.json release.json
        data/map.pmtiles  (or data/tiles/{z}/{x}/{y}.pbf for the XYZ transport)
        assets/...  sprite/...  glyphs/...  legend/...  search/...  features/...
        public-diagnostics.json  licenses/...

A release is assembled in ``releases/.staging-<id>``, validated (vector-only
style, allowlisted files, safe paths, archive structure, transport-only
style diff, leak scan) and only then renamed to ``releases/<id>``.
Activation replaces ``current.json`` with ``os.replace`` after an fsync;
failures before that leave the previous release current and intact.
"""

import datetime as _dt
import hashlib
import html
import json
import os
import re
import shutil
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional

from . import PUBLISHING_SCHEMA_VERSION
from .bundle import (inventory, portable_style, style_semantic_diff, walk_files,
                     write_json_atomic, write_text_atomic, write_xyz_tiles)
from .content_types import NO_CACHE
from .errors import Cancelled, PublishingError
from .models import ExportBundle, PublicationProfile
from .pmtiles_builder import ArchiveDescriptor, PmtilesOptions, build_pmtiles
from .progress import Progress
from .validation import (assert_vector_only, check_public_file, required_source_layers,
                         safe_relative_path, scan_bundle, validate_pmtiles, vector_only_violations)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESOURCES = os.path.join(ROOT, "resources")
ML_VIEWER = os.path.join(RESOURCES, "ml_viewer")
WEB_VIEWER = os.path.join(RESOURCES, "web_viewer")
RELEASE_ID = re.compile(r"^r-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")
LOCK_NAME = ".q2vt-lock"
LOCK_STALE_S = 6 * 3600

# Files of the bundled runtime (one maintained copy of each, see plan §3).
ML_ASSETS = ["maplibre-gl.mjs", "maplibre-gl-shared.mjs", "maplibre-gl-worker.mjs",
             "maplibre-gl.css", "visible_labels.mjs"]
LICENSES = {"MAPLIBRE-LICENSE.txt": os.path.join(ML_VIEWER, "MAPLIBRE-LICENSE.txt"),
            "PMTILES-LICENSE.txt": os.path.join(WEB_VIEWER, "vendor", "PMTILES-LICENSE.txt"),
            "PROJ4JS-LICENSE.txt": os.path.join(WEB_VIEWER, "vendor", "PROJ4JS-LICENSE.txt")}
VIEWER_EXCLUDE = {"index.html", "bootstrap.html", "bootstrap.mjs"}


def plugin_version() -> str:
    try:
        with open(os.path.join(ROOT, "metadata.txt"), encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("version="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def new_release_id(now: Optional[_dt.datetime] = None) -> str:
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return f"r-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"


@dataclass
class ReleaseResult:
    release_id: str
    publication_dir: str
    release_dir: str
    entry: str                         # releases/<id>/index.html (relative)
    files: List[dict]                  # release.json inventory
    total_bytes: int
    archive: Optional[ArchiveDescriptor]
    activated: bool = False
    zip_path: str = ""
    mbtiles_copy: str = ""
    warnings: List[str] = field(default_factory=list)


# --- locking / recovery -----------------------------------------------------------------

class PublicationLock:
    """Prevents two local builds into one publication folder at once."""

    def __init__(self, publication_dir: str):
        self.path = os.path.join(publication_dir, LOCK_NAME)
        self.fd = None

    def __enter__(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        for _ in range(2):
            try:
                self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, f"{os.getpid()} {time.time():.0f}".encode())
                return self
            except FileExistsError:
                try:
                    age = time.time() - os.path.getmtime(self.path)
                except OSError:
                    continue
                if age > LOCK_STALE_S:
                    os.remove(self.path)
                    continue
                raise PublishingError("Q2VT_PUB_DESTINATION",
                                      "Another export is writing this publication folder.")
        raise PublishingError("Q2VT_PUB_DESTINATION", "Could not lock the publication folder.")

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
        try:
            os.remove(self.path)
        except OSError:
            pass


def recover(publication_dir: str) -> List[str]:
    """Remove leftovers of interrupted builds (staging folders, temporary
    pointer files). Never touches a finished release or ``current.json``."""
    removed = []
    releases = os.path.join(publication_dir, "releases")
    if os.path.isdir(releases):
        for name in os.listdir(releases):
            if name.startswith(".staging-"):
                shutil.rmtree(os.path.join(releases, name), ignore_errors=True)
                removed.append(f"releases/{name}")
    if os.path.isdir(publication_dir):
        for name in os.listdir(publication_dir):
            if ".tmp-" in name:
                try:
                    os.remove(os.path.join(publication_dir, name))
                    removed.append(name)
                except OSError:
                    pass
    return removed


# --- pointer --------------------------------------------------------------------------

def read_current(publication_dir: str) -> Optional[dict]:
    path = os.path.join(publication_dir, "current.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as handle:
        pointer = json.load(handle)
    validate_pointer(pointer)
    return pointer


def validate_pointer(pointer: dict) -> None:
    rid = pointer.get("releaseId", "")
    if pointer.get("schemaVersion") != 1 or not RELEASE_ID.match(rid) \
            or pointer.get("entry") != f"releases/{rid}/index.html" \
            or pointer.get("manifest") != f"releases/{rid}/manifest.json":
        raise PublishingError("Q2VT_PUB_PATH_UNSAFE", "current.json is invalid or points outside "
                              "the publication.")


def pointer_for(publication_id: str, release_id: str) -> dict:
    return {"schemaVersion": 1, "publicationId": publication_id, "releaseId": release_id,
            "entry": f"releases/{release_id}/index.html",
            "manifest": f"releases/{release_id}/manifest.json",
            "activatedAt": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def list_releases(publication_dir: str) -> List[str]:
    folder = os.path.join(publication_dir, "releases")
    if not os.path.isdir(folder):
        return []
    return sorted(name for name in os.listdir(folder)
                  if RELEASE_ID.match(name) and os.path.exists(os.path.join(folder, name, "release.json")))


def activate_release(publication_dir: str, publication_id: str, release_id: str,
                     expected_release: Optional[str] = "__any__") -> dict:
    """Point ``current.json`` at a finished release (atomic replace).

    ``expected_release``: the release the caller saw as current; if another
    build activated something else meanwhile, raise a conflict instead of
    overwriting it (``"__any__"`` skips the check)."""
    if release_id not in list_releases(publication_dir):
        raise PublishingError("Q2VT_PUB_ACTIVATION", f"Release {release_id} does not exist.")
    if expected_release != "__any__":
        current = read_current(publication_dir)
        seen = current["releaseId"] if current else None
        if seen != expected_release:
            raise PublishingError("Q2VT_PUB_ACTIVATION_CONFLICT",
                                  f"current.json now points at {seen}, not {expected_release}.")
    pointer = pointer_for(publication_id, release_id)
    write_json_atomic(os.path.join(publication_dir, "current.json"), pointer, indent=1)
    return pointer


def apply_retention(publication_dir: str, keep: int, protect: Iterable[str] = ()) -> List[str]:
    """Delete the oldest releases beyond ``keep``; never the current one or
    ``protect``-ed ids."""
    current = read_current(publication_dir)
    protected = set(protect) | ({current["releaseId"]} if current else set())
    releases = list_releases(publication_dir)
    removable = [r for r in releases if r not in protected]
    excess = max(0, len(releases) - max(1, keep))
    removed = []
    for release in removable[:excess]:
        shutil.rmtree(os.path.join(publication_dir, "releases", release))
        removed.append(release)
    return removed


# --- assembly -------------------------------------------------------------------------

def _copy(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)


def _copy_tree(src: str, dst: str) -> None:
    for rel in walk_files(src):
        _copy(os.path.join(src, *rel.split("/")), os.path.join(dst, *rel.split("/")))


def _viewer_files() -> List[str]:
    return [rel for rel in walk_files(WEB_VIEWER)
            if rel not in VIEWER_EXCLUDE and not rel.endswith(".txt")]


def _render_index(template_path: str, title: str, connect: Iterable[str] = (),
                  web_tiles: Iterable[str] = ()) -> str:
    """The release page. ``web_tiles``: origins of web basemaps (XYZ tiles):
    the page may load from them, and sends its origin as referrer (tile
    services such as OpenStreetMap's require one); otherwise nothing leaves
    the site and no referrer is sent."""
    with open(template_path, encoding="utf-8") as handle:
        text = handle.read()
    valid = re.compile(r"^https://[A-Za-z0-9.-]+(:\d+)?$")
    web_tiles = sorted(o for o in web_tiles if valid.match(o))
    origins = "".join(f" {o}" for o in [*connect, *web_tiles] if valid.match(o))
    text = text.replace("__Q2VT_TITLE__", html.escape(title, quote=True)) \
               .replace("__Q2VT_CONNECT__", origins) \
               .replace("__Q2VT_IMG__", "".join(f" {o}" for o in web_tiles))
    if web_tiles:
        text = text.replace('<meta name="referrer" content="no-referrer">',
                            '<meta name="referrer" content="strict-origin-when-cross-origin">')
    return text


def build_manifest(bundle: ExportBundle, profile: PublicationProfile, release_id: str,
                   source: dict, extra: Optional[dict] = None) -> dict:
    view = dict(bundle.view or {})
    manifest = {
        "schemaVersion": PUBLISHING_SCHEMA_VERSION,
        "publicationId": profile.publication_id,
        "releaseId": release_id,
        "title": profile.title,
        "description": profile.description,
        "locale": profile.locale,
        "attribution": profile.attribution,
        "logo": None,
        "vectorOnly": True,
        "style": "style.json",
        "sources": [source],
        "view": {
            "center": [round(float(c), 7) for c in view.get("center", [0, 0])],
            "zoom": float(view.get("zoom", source["minTileZoom"])),
            "bearing": 0, "pitch": 0,
            "minZoom": float(max(0, profile.view.min_zoom if profile.view else 0)),
            "maxZoom": float(profile.view.max_view_zoom),
            "bounds": [round(float(v), 7) for v in bundle.bounds_wgs84],
        },
        "groups": bundle.groups, "layers": bundle.layers, "rules": bundle.rules,
        "components": bundle.components,
        "search": None, "featureLookup": None,
        "interaction": profile.to_dict()["interaction"],
        "tools": {"measure": profile.interaction.measure,
                  "coordinates": profile.interaction.coordinates,
                  "print": profile.interaction.print},
        "diagnostics": "public-diagnostics.json",
        "generator": {"name": "QWebMap", "version": plugin_version()},
        # The project CRS: the coordinate readout shows coordinates in it.
        "crs": getattr(bundle, "crs", None),
    }
    manifest.update(extra or {})
    return manifest


def validate_release_dir(staging: str, style: dict, archive_layers: Optional[Iterable[str]],
                         secrets: Iterable[str] = (), canaries: Iterable[str] = (),
                         pmtiles_sample: int = 64,
                         raster_sources: Optional[Dict[str, str]] = None) -> List[str]:
    """Raise PublishingError for an invalid release folder; return warnings.

    ``raster_sources``: {source id: release path} of the QGIS raster
    layers' image archives (the only raster sources a style may have)."""
    warnings = []
    files = walk_files(staging)
    for rel in files:
        safe_relative_path(rel)
        check_public_file(rel)
    raster_sources = raster_sources or {}
    assert_vector_only(style, raster_sources=set(raster_sources))
    for source_id, href in raster_sources.items():
        if href not in files:
            raise PublishingError("Q2VT_PUB_BUNDLE_INVALID", f"{href} missing")
        validate_pmtiles(os.path.join(staging, *href.split("/")), sample=pmtiles_sample, kind="image")
    for rel in files:  # vector basemap flavors (added under the map by the viewer)
        if rel.startswith("basemaps/") and rel.endswith(".json"):
            with open(os.path.join(staging, *rel.split("/")), encoding="utf-8") as handle:
                flavor = json.load(handle)
            from .basemap import SOURCE_ID  # pylint: disable=import-outside-toplevel
            flavor_style = {"sources": {SOURCE_ID: {"type": "vector"}}, "layers": flavor.get("layers", [])}
            problems = vector_only_violations(flavor_style)
            problems += [f"layer '{l.get('id')}' uses source {l.get('source')}"
                         for l in flavor_style["layers"] if l.get("source") not in (None, SOURCE_ID)]
            if problems:
                raise PublishingError("Q2VT_PUB_RASTER_SOURCE", f"{rel}: " + "; ".join(problems[:3]))
            style_fonts = {}
            for layer in flavor_style["layers"]:
                _collect_fonts((layer.get("layout") or {}).get("text-font"), style_fonts)
            for font in style_fonts:
                if not any(r.startswith(f"glyphs/{font}/") for r in files):
                    raise PublishingError("Q2VT_PUB_BUNDLE_INVALID", f"{rel}: no glyphs for font '{font}'.")
    if "data/basemap.pmtiles" in files:
        validate_pmtiles(os.path.join(staging, "data", "basemap.pmtiles"), sample=pmtiles_sample)
    if archive_layers is not None:
        missing = set(required_source_layers(style)) - set(archive_layers)
        if missing:  # datasets with no feature in the extent: nothing to draw
            warnings.append(f"{len(missing)} style source layer(s) have no tiles (empty in the "
                            f"export extent): {', '.join(sorted(missing)[:5])}")
    if "sprite" in style:
        for suffix in (".json", ".png"):
            if f"sprite/sprite{suffix}" not in files:
                raise PublishingError("Q2VT_PUB_BUNDLE_INVALID", f"sprite/sprite{suffix} missing")
    fonts = set()
    for layer in style.get("layers", []):
        stack = (layer.get("layout") or {}).get("text-font")
        if isinstance(stack, list) and all(isinstance(f, str) for f in stack):
            fonts.add(",".join(stack))
    for font in sorted(fonts):
        if not any(rel.startswith(f"glyphs/{font}/") for rel in files):
            raise PublishingError("Q2VT_PUB_BUNDLE_INVALID", f"No glyphs for font '{font}'.")
    for required in ("index.html", "manifest.json", "style.json", "assets/app.mjs",
                     "assets/maplibre-gl.mjs", "assets/maplibre-gl-worker.mjs",
                     "assets/visible_labels.mjs"):
        if required not in files:
            raise PublishingError("Q2VT_PUB_BUNDLE_INVALID", f"{required} missing")
    if "data/map.pmtiles" in files:
        validate_pmtiles(os.path.join(staging, "data", "map.pmtiles"), sample=pmtiles_sample)
    problems = scan_bundle(staging, files, secrets, canaries)
    if problems:
        raise PublishingError("Q2VT_PUB_SECRET_LEAK", "; ".join(problems[:5]))
    return warnings


def build_release(bundle: ExportBundle, profile: PublicationProfile, publication_dir: str, *,
                  transport: str = "pmtiles", activate: bool = True, feedback=None,
                  secrets: Iterable[str] = (), canaries: Iterable[str] = (),
                  extra_files: Optional[Dict[str, str]] = None,
                  extra_builders: Optional[List[Callable[[str, dict], None]]] = None,
                  manifest_extra: Optional[dict] = None,
                  connect_origins: Iterable[str] = (),
                  pmtiles_path: Optional[str] = None,
                  extra_validators: Optional[List[Callable[[str, dict], None]]] = None) -> ReleaseResult:
    """Build, validate and (optionally) activate one immutable local release.

    ``extra_files``: ``{release path: local file}`` (legend swatches, ...).
    ``extra_builders``: callables ``(staging_dir, manifest)`` that write more
    public files (search/feature indexes) and update the manifest.
    ``pmtiles_path``: an already built and validated archive to copy instead
    of converting ``bundle.mbtiles_path`` again (retry/reuse).
    ``extra_validators``: callables ``(staging_dir, manifest)`` raising
    PublishingError; they run before the release is renamed into place."""
    if transport not in ("pmtiles", "xyz"):
        raise ValueError(transport)
    progress = feedback if isinstance(feedback, Progress) else Progress(feedback)
    publication_dir = os.path.abspath(publication_dir)
    os.makedirs(os.path.join(publication_dir, "releases"), exist_ok=True)
    with PublicationLock(publication_dir):
        recover(publication_dir)
        release_id = new_release_id()
        staging = os.path.join(publication_dir, "releases", f".staging-{release_id}")
        final = os.path.join(publication_dir, "releases", release_id)
        os.makedirs(staging)
        try:
            result = _assemble(bundle, profile, staging, release_id, transport, progress,
                               extra_files or {}, extra_builders or [], manifest_extra,
                               connect_origins, pmtiles_path)
            archive, manifest, style = result
            progress.update(0.9, "Validating the web release...", force=True)
            warnings = validate_release_dir(
                staging, style, archive.vector_layers if archive else None, secrets, canaries,
                raster_sources={s["id"]: s["href"] for s in manifest["sources"]
                                if s.get("role") == "raster"})
            for validator in extra_validators or []:  # e.g. field disclosure
                validator(staging, manifest)
            files = [rel for rel in walk_files(staging)]
            items = inventory(staging, files)
            release_doc = {
                "schemaVersion": 1, "publicationId": profile.publication_id,
                "releaseId": release_id,
                "createdAt": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "buildId": bundle.build_id, "configFingerprint": bundle.config_fingerprint,
                "versions": {"plugin": plugin_version(), "maplibre": "6.11.2", "pmtilesJs": "4.5.0",
                             "pmtilesPython": "3.8.1", **{k: str(v) for k, v in
                                                          bundle.runtime_versions.items()}},
                "files": items,
            }
            write_json_atomic(os.path.join(staging, "release.json"), release_doc, indent=1)
            progress.check()
            os.replace(staging, final)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        _write_stable_entry(publication_dir, profile.title)
        activated = False
        if activate:
            activate_release(publication_dir, profile.publication_id, release_id)
            activated = True
        total = sum(item["size"] for item in items)
        progress.update(1.0, f"Web release {release_id}: {len(items)} files, {total} bytes",
                        force=True)
        return ReleaseResult(release_id, publication_dir, final, f"releases/{release_id}/index.html",
                             items, total, archive, activated, warnings=warnings)


def _assemble(bundle, profile, staging, release_id, transport, progress, extra_files,
              extra_builders, manifest_extra, connect_origins, pmtiles_path):
    source_name = bundle.source_name
    # 1. Tiles: the same MVT payloads as the validated MBTiles.
    progress.update(0.02, "Packaging vector tiles...", force=True)
    archive = None
    if transport == "pmtiles":
        target = os.path.join(staging, "data", "map.pmtiles")
        os.makedirs(os.path.dirname(target))
        if pmtiles_path:
            validate_pmtiles(pmtiles_path, sample=64)
            shutil.copyfile(pmtiles_path, target)
            archive = _describe_pmtiles(target)
        else:
            archive = build_pmtiles(bundle.mbtiles_path, target,
                                    PmtilesOptions(name=profile.title), feedback=progress.sub(0.02, 0.6))
        source = {"id": source_name, "kind": "pmtiles", "tileType": "mvt", "href": "data/map.pmtiles",
                  "minTileZoom": archive.min_zoom, "maxTileZoom": archive.max_zoom,
                  "bounds": [round(v, 7) for v in archive.bounds],
                  "sha256": archive.sha256, "sizeBytes": archive.size_bytes}
    else:
        from .pmtiles_builder import preflight_mbtiles  # pylint: disable=import-outside-toplevel
        info = preflight_mbtiles(bundle.mbtiles_path)
        write_xyz_tiles(bundle.mbtiles_path, os.path.join(staging, "data", "tiles"), progress)
        archive = copy_descriptor_from_info(bundle.mbtiles_path, info)
        source = {"id": source_name, "kind": "xyz", "tileType": "mvt",
                  "href": "data/tiles/{z}/{x}/{y}.pbf",
                  "minTileZoom": info.min_zoom, "maxTileZoom": info.max_zoom,
                  "bounds": [round(v, 7) for v in archive.bounds]}
    progress.check()
    # 2. Style: transport-only changes (source bound by the viewer).
    style = portable_style(bundle.style, source_name, tiles_template=None, vector_only=True)
    source_def = style["sources"].get(source_name)
    if source_def is None:
        raise PublishingError("Q2VT_PUB_BUNDLE_INVALID", f"Style has no source {source_name}.")
    source_def["minzoom"], source_def["maxzoom"] = source["minTileZoom"], source["maxTileZoom"]
    problems = style_semantic_diff(bundle.style, style, source_name)
    if problems:
        raise PublishingError("Q2VT_PUB_BUNDLE_INVALID",
                              "Packaging changed the style: " + "; ".join(problems[:5]))
    raster_sources = _add_raster_layers(style, bundle, staging, progress)
    basemap_manifest = _add_basemap(style, bundle, profile, staging)
    write_json_atomic(os.path.join(staging, "style.json"), style)
    # 3. Styling assets.
    if bundle.sprite_dir and os.path.isdir(bundle.sprite_dir) and "sprite" in style:
        _copy_tree(bundle.sprite_dir, os.path.join(staging, "sprite"))
    if bundle.glyphs_dir and os.path.isdir(bundle.glyphs_dir) and "glyphs" in style:
        _copy_tree(bundle.glyphs_dir, os.path.join(staging, "glyphs"))
    # 4. Viewer runtime (single maintained copies) and licences.
    for name in ML_ASSETS:
        _copy(os.path.join(ML_VIEWER, name), os.path.join(staging, "assets", name))
    for rel in _viewer_files():
        _copy(os.path.join(WEB_VIEWER, *rel.split("/")), os.path.join(staging, "assets", *rel.split("/")))
    for name, src in LICENSES.items():
        _copy(src, os.path.join(staging, "licenses", name))
    from .xyz import origins as xyz_origins  # pylint: disable=import-outside-toplevel
    web_tiles = xyz_origins(url for entry in (basemap_manifest or {}).get("xyz", []) for url in entry["tiles"])
    write_text_atomic(os.path.join(staging, "index.html"),
                      _render_index(os.path.join(WEB_VIEWER, "index.html"), profile.title,
                                    connect_origins, web_tiles))
    # 5. Logo, legend and other UI assets.
    manifest_logo = None
    if profile.logo_path and os.path.isfile(profile.logo_path):
        ext = os.path.splitext(profile.logo_path)[1].lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp"):
            manifest_logo = f"assets/logo{ext}"
            _copy(profile.logo_path, os.path.join(staging, *manifest_logo.split("/")))
    for rel, src in extra_files.items():
        safe_relative_path(rel)
        _copy(src, os.path.join(staging, *rel.split("/")))
    # 6. Manifest + public diagnostics.
    manifest = build_manifest(bundle, profile, release_id, source, manifest_extra)
    manifest["sources"].extend(raster_sources)
    manifest["basemap"] = basemap_manifest
    manifest["logo"] = manifest_logo
    for builder in extra_builders:
        builder(staging, manifest)
        progress.check()
    write_json_atomic(os.path.join(staging, "manifest.json"), manifest)
    write_json_atomic(os.path.join(staging, "public-diagnostics.json"), {
        "schemaVersion": 1, "releaseId": release_id,
        "fidelity": {k: int(v) for k, v in (bundle.diagnostics_summary or {}).items()},
        "tiles": archive.addressed_tiles if archive else None,
    })
    return archive, manifest, style


def _add_raster_layers(style: dict, bundle: ExportBundle, staging: str, progress) -> List[dict]:
    """Copy the raster layers' image archives into ``data/`` and draw them in
    the style at their QGIS layer-tree position: under every vector layer
    that is above them in the tree and under all labels (QGIS draws labels
    last). Returns the manifest sources."""
    if not bundle.raster_archives:
        return []
    order = {layer["id"]: layer.get("order", 0) for layer in bundle.layers}
    owner = {}  # style layer id -> (tree order, role)
    for component in bundle.components:
        for style_id in component["styleLayerIds"]:
            owner[style_id] = (order.get(component["layerId"], 0), component["role"])
    entries = {layer["id"]: layer for layer in bundle.layers}
    sources = []
    for raster in sorted(bundle.raster_archives, key=lambda r: -order.get(r["layerId"], 0)):
        d = raster["descriptor"]
        href = f"data/raster-{raster['layerId']}.pmtiles"
        _copy(raster["path"], os.path.join(staging, *href.split("/")))
        sid, rank = raster["sourceId"], order.get(raster["layerId"], 0)
        style["sources"][sid] = {"type": "raster", "tileSize": raster.get("tileSize", 256),
                                 "minzoom": d.min_zoom, "maxzoom": d.max_zoom,
                                 "bounds": [round(v, 7) for v in d.bounds]}
        layer_def = {"id": raster["styleLayerId"], "type": "raster", "source": sid,
                     "paint": {"raster-opacity": 1, "raster-fade-duration": 150},
                     "metadata": {"q2vt:raster-layer": raster["layerId"]}}
        entry = entries.get(raster["layerId"], {})
        if entry.get("minZoom") is not None:
            layer_def["minzoom"] = entry["minZoom"]
        if entry.get("maxZoom") is not None:
            layer_def["maxzoom"] = entry["maxZoom"]
        position = len(style["layers"])
        for index, existing in enumerate(style["layers"]):
            above = owner.get(existing.get("id"))
            if above and (above[0] < rank or above[1] in ("label", "callout")):
                position = index
                break
        style["layers"].insert(position, layer_def)
        owner[layer_def["id"]] = (rank, "raster")
        sources.append({"id": sid, "kind": "pmtiles", "role": "raster", "tileType": d.tile_type,
                        "href": href, "minTileZoom": d.min_zoom, "maxTileZoom": d.max_zoom,
                        "bounds": [round(v, 7) for v in d.bounds], "tileSize": raster.get("tileSize", 256),
                        "sha256": d.sha256, "sizeBytes": d.size_bytes, "layerId": raster["layerId"]})
        progress.check()
    return sources


def _collect_fonts(value, out: dict) -> None:
    """Font stack names in a ``text-font`` value (literal or expression)."""
    if isinstance(value, list) and value and all(isinstance(v, str) for v in value) \
            and value[0] not in ("literal", "case", "match", "step", "coalesce", "get", "format"):
        out[",".join(value)] = True
    elif isinstance(value, list):
        for item in value:
            _collect_fonts(item, out)


def _add_basemap(style: dict, bundle: ExportBundle, profile: PublicationProfile, staging: str):
    """The vector basemap: its archive, one style file per flavor and its
    glyphs. The viewer adds the chosen flavor under the map at runtime."""
    info = bundle.basemap
    from .xyz import manifest_entries  # pylint: disable=import-outside-toplevel
    xyz = manifest_entries(profile.basemap.xyz)
    xyz_ids = [entry["id"] for entry in xyz]
    if not info:
        if not xyz:
            return None
        initial = profile.basemap.initial if profile.basemap.initial in xyz_ids else "none"
        return {"source": None, "flavors": [], "xyz": xyz, "initial": initial, "attribution": ""}
    from .basemap import ATTRIBUTION, FLAVOR_TITLES, SOURCE_ID  # pylint: disable=import-outside-toplevel
    d = info["descriptor"]
    _copy(info["archive"], os.path.join(staging, "data", "basemap.pmtiles"))
    if info.get("glyphs_dir") and os.path.isdir(info["glyphs_dir"]):
        _copy_tree(info["glyphs_dir"], os.path.join(staging, "glyphs"))
    style.setdefault("glyphs", "glyphs/{fontstack}/{range}.pbf")
    titles = FLAVOR_TITLES.get(profile.locale, FLAVOR_TITLES["en"])
    os.makedirs(os.path.join(staging, "basemaps"), exist_ok=True)
    flavors = []
    for flavor_id, flavor in info["flavors"].items():
        rel = f"basemaps/{flavor_id}.json"
        write_json_atomic(os.path.join(staging, *rel.split("/")),
                          {"schemaVersion": 1, "id": flavor_id, "layers": flavor["layers"]})
        flavors.append({"id": flavor_id, "title": titles.get(flavor_id, flavor_id), "style": rel,
                        "colors": flavor.get("colors", {})})
    return {
        "source": {"id": SOURCE_ID, "kind": "pmtiles", "tileType": "mvt", "href": "data/basemap.pmtiles",
                   "minTileZoom": d.min_zoom, "maxTileZoom": d.max_zoom,
                   "bounds": [round(v, 7) for v in d.bounds], "sha256": d.sha256, "sizeBytes": d.size_bytes},
        "flavors": flavors, "xyz": xyz,
        "initial": profile.basemap.initial if profile.basemap.initial in xyz_ids
        else info.get("initial", flavors[0]["id"] if flavors else "none"),
        "attribution": ATTRIBUTION,
    }


def _describe_pmtiles(path: str) -> ArchiveDescriptor:
    from .pmtiles_builder import sha256_file  # pylint: disable=import-outside-toplevel
    from .validation import open_pmtiles  # pylint: disable=import-outside-toplevel
    with open_pmtiles(path) as archive:
        h = archive.header
        meta = archive.metadata()
    layers = [layer.get("id", "") for layer in meta.get("vector_layers", []) if isinstance(layer, dict)]
    return ArchiveDescriptor(
        path=path, format="pmtiles", min_zoom=h["min_zoom"], max_zoom=h["max_zoom"],
        bounds=(h["min_lon_e7"] / 1e7, h["min_lat_e7"] / 1e7, h["max_lon_e7"] / 1e7, h["max_lat_e7"] / 1e7),
        center=(h["center_lon_e7"] / 1e7, h["center_lat_e7"] / 1e7, h["center_zoom"]),
        addressed_tiles=h["addressed_tiles_count"], tile_contents=h["tile_contents_count"],
        size_bytes=os.path.getsize(path), sha256=sha256_file(path), vector_layers=layers)


def copy_descriptor_from_info(mbtiles: str, info) -> ArchiveDescriptor:
    from .pmtiles_builder import _complete_vector_layers, _parse_bounds  # pylint: disable=import-outside-toplevel
    from .validation import mbtiles_tiles  # pylint: disable=import-outside-toplevel
    from . import mvt  # pylint: disable=import-outside-toplevel
    seen: Dict[str, dict] = {}
    for (z, _, _), data in mbtiles_tiles(mbtiles):
        for name, keys in mvt.layer_summary(data).items():
            entry = seen.setdefault(name, {"keys": set(), "zooms": [z, z]})
            entry["keys"].update(keys)
    layers = _complete_vector_layers(info.json_metadata.get("vector_layers"), seen, [])
    bounds = _parse_bounds(info.metadata.get("bounds")) or (-180.0, -85.0511, 180.0, 85.0511)
    return ArchiveDescriptor(path=mbtiles, format="xyz", min_zoom=info.min_zoom,
                             max_zoom=info.max_zoom, bounds=bounds,
                             addressed_tiles=info.tile_count,
                             vector_layers=[layer["id"] for layer in layers])


def _write_stable_entry(publication_dir: str, title: str) -> None:
    """``index.html`` + ``bootstrap.<hash>.mjs`` at the publication root."""
    with open(os.path.join(WEB_VIEWER, "bootstrap.mjs"), "rb") as handle:
        code = handle.read()
    name = f"bootstrap.{hashlib.sha256(code).hexdigest()[:10]}.mjs"
    target = os.path.join(publication_dir, name)
    if not os.path.exists(target):
        tmp = f"{target}.tmp-{uuid.uuid4().hex[:6]}"
        with open(tmp, "wb") as handle:
            handle.write(code)
        os.replace(tmp, target)
    with open(os.path.join(WEB_VIEWER, "bootstrap.html"), encoding="utf-8") as handle:
        page = handle.read().replace("__Q2VT_TITLE__", html.escape(title, quote=True)) \
                            .replace("__Q2VT_BOOTSTRAP__", name)
    index = os.path.join(publication_dir, "index.html")
    old = None
    if os.path.exists(index):
        with open(index, encoding="utf-8") as handle:
            old = handle.read()
    if old != page:
        write_text_atomic(index, page)


STABLE_FILES_CACHE = NO_CACHE


def stable_files(publication_dir: str) -> List[str]:
    """Root files of the publication (entry, bootstrap, pointer)."""
    names = ["index.html", "current.json"]
    names += sorted(n for n in os.listdir(publication_dir) if re.match(r"^bootstrap\.[0-9a-f]{10}\.mjs$", n))
    return [n for n in names if os.path.exists(os.path.join(publication_dir, n))]


def write_zip(release_dir: str, zip_path: str) -> str:
    """Offline delivery: the release content at the ZIP root (its own
    ``index.html``); written next to the publication, never inside it."""
    tmp = f"{zip_path}.tmp-{uuid.uuid4().hex[:6]}"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            for rel in walk_files(release_dir):
                path = os.path.join(release_dir, *rel.split("/"))
                # The PMTiles tiles are already compressed: store them.
                kind = zipfile.ZIP_STORED if rel.endswith(".pmtiles") else zipfile.ZIP_DEFLATED
                archive.write(path, rel, compress_type=kind)
        os.replace(tmp, zip_path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return zip_path


def check_cancel(progress: Progress) -> None:
    if progress.canceled():
        raise Cancelled()
