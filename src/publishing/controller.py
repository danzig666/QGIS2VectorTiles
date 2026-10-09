"""
Publishing controller: one publication run as named stages.

    PLAN -> EXPORT_MVT -> RECORDS -> LEGEND -> BUILD_RELEASE (PMTiles, viewer,
    indexes, validation, disclosure) -> [UPLOAD -> VERIFY_PUBLIC -> ACTIVATE]

The QGIS stages (PLAN .. LEGEND) read the live project and must run on
QGIS's main thread (the exporter keeps its NoThreading guarantees). The
release build and uploads only touch closed local files.

Local export never needs credentials; a failed or cancelled stage leaves the
previous local (and public) release current.
"""

import hashlib
import itertools
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .disclosure import assert_disclosure
from .errors import Cancelled, PublishingError
from .feature_index import build_feature_index
from .models import PublicationProfile, ReleaseState
from .progress import Progress
from .search_index import build_search_index
from .web_builder import ReleaseResult, build_release, write_zip

STAGES = ["PLAN", "EXPORT_MVT", "RECORDS", "LEGEND", "PARCELS", "RASTER", "BASEMAP", "STREETS", "TILES",
          "BUILD_RELEASE",
          "UPLOAD", "VERIFY_PUBLIC", "ACTIVATE", "COMPLETE"]


@dataclass
class LocalResult:
    state: ReleaseState
    release: Optional[ReleaseResult] = None
    publication_dir: str = ""
    export_dir: str = ""
    records_path: str = ""
    feature_counts: Dict[str, int] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    mbtiles_copy: str = ""
    zip_path: str = ""
    error: Optional[PublishingError] = None

    @property
    def entry_path(self) -> str:
        return os.path.join(self.publication_dir, "index.html") if self.publication_dir else ""


def _iter_records(path: str):
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


# File systems of other computers: thousands of small work files are slow there.
_NETWORK_FILE_SYSTEMS = {"nfs", "nfs4", "cifs", "smb3", "smbfs", "fuse.sshfs", "sshfs", "afs", "9p",
                         "fuse.rclone", "davfs", "fuse.davfs2", "ncpfs", "coda"}


def is_network_folder(path: str) -> bool:
    """Whether ``path`` is on another computer: a Windows share (UNC path) or
    mapped network drive, an NFS / SMB / SSHFS mount. Q2VT_LOCAL_WORK=1 / 0
    decides instead (tests, unusual setups)."""
    override = os.environ.get("Q2VT_LOCAL_WORK")
    if override in ("0", "1"):
        return override == "1"
    path = os.path.abspath(path)
    if os.name == "nt":
        if path.startswith(("\\\\", "//")):
            return True
        drive = os.path.splitdrive(path)[0]
        try:
            import ctypes  # pylint: disable=import-outside-toplevel
            return bool(drive) and ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4  # DRIVE_REMOTE
        except (AttributeError, OSError, ValueError):
            return False
    try:
        with open("/proc/mounts", encoding="utf-8") as handle:
            mounts = [line.split()[:3] for line in handle]
    except OSError:
        return False
    best, kind = "", ""
    for entry in mounts:
        if len(entry) < 3:
            continue
        mount = entry[1].replace("\\040", " ")
        inside = path == mount or path.startswith(mount.rstrip("/") + "/")
        if inside and len(mount) > len(best):
            best, kind = mount, entry[2]
    return kind in _NETWORK_FILE_SYSTEMS


def _local_root(base: str) -> Optional[str]:
    """The local folder of the work files of a network output folder (in the
    system temp folder, one per output folder); None for a local one."""
    if not is_network_folder(base):
        return None
    digest = hashlib.sha1(os.path.normcase(base).encode("utf-8")).hexdigest()[:12]
    return os.path.join(tempfile.gettempdir(), "q2vt-work", digest)


def publication_dirs(profile: PublicationProfile, base: Optional[str] = None):
    """(publication folder, work folder). The work folder (the export's
    datasets, tiles and logs) is on this computer when the output folder is a
    network folder: only the finished release is written there."""
    base = base or profile.output.local_directory
    if not base:
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID", "Choose a local output folder.")
    base = os.path.abspath(base)
    local = _local_root(base)
    work = os.path.join(local, profile.slug) if local else os.path.join(base, ".q2vt-work", profile.slug)
    return os.path.join(base, profile.slug), work


def cache_dir(profile: PublicationProfile, base: Optional[str] = None) -> str:
    """The export cache of a local output folder (shared by its publications;
    on this computer for a network output folder, as the work folder)."""
    base = base or profile.output.local_directory
    if not base:
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID", "Choose a local output folder.")
    base = os.path.abspath(base)
    local = _local_root(base)
    return os.path.join(local, ".q2vt-cache") if local else os.path.join(base, ".q2vt-cache")


_LOG_FILES = ("export_log.txt", "fidelity_report.html", "fidelity_report.json")


def _copy_logs(export_folder: str, profile: PublicationProfile, base: Optional[str]) -> None:
    """The export log and fidelity report of a local work folder copied next
    to the output, where they are without a local work folder."""
    base = os.path.abspath(base or profile.output.local_directory)
    if not export_folder or not os.path.isdir(export_folder) or _local_root(base) is None:
        return
    target = os.path.join(base, ".q2vt-work", profile.slug, os.path.basename(export_folder))
    try:
        os.makedirs(target, exist_ok=True)
        for name in _LOG_FILES:
            if os.path.exists(os.path.join(export_folder, name)):
                shutil.copyfile(os.path.join(export_folder, name), os.path.join(target, name))
    except OSError:
        pass  # the logs stay in the local work folder


def _filter_fields(manifest_layers: List[dict], profile: PublicationProfile, domains) -> None:
    by_id = {}
    from .provenance import layer_logical_id  # pylint: disable=import-outside-toplevel
    for config in profile.layers:
        by_id[layer_logical_id(config.layer_id)] = config
    for layer in manifest_layers:
        config = by_id.get(layer["id"])
        if config is None:
            continue
        out = []
        for flt in config.filter_fields:
            domain = (domains.get(layer["id"]) or {}).get(flt.field, {})
            item = {"field": flt.field, "title": flt.alias or flt.field, "kind": flt.kind,
                    "nulls": domain.get("nulls", 0)}
            if flt.kind == "values":
                values = domain.get("values", {})
                item["values"] = sorted(([k, n] for k, n in values.items()), key=lambda v: v[0])
                from .qgis_model import FILTER_VALUES_LIMIT  # pylint: disable=import-outside-toplevel
                item["complete"] = len(values) <= FILTER_VALUES_LIMIT
            elif flt.kind == "range":
                item["min"], item["max"] = domain.get("min"), domain.get("max")
            out.append(item)
        layer["filterFields"] = out


def _staged_feedback(parent):
    """A feedback that maps each stage's own 0..100 % into the stage's share
    of the whole export (``begin(stage)``; shares from ``weigh``), so the
    progress bar only moves forward instead of restarting at every stage."""
    from qgis.core import QgsProcessingFeedback  # pylint: disable=import-outside-toplevel

    class StagedFeedback(QgsProcessingFeedback):
        def __init__(self):
            super().__init__()
            self.parent = parent
            self.weights: Dict[str, float] = {"PLAN": 1.0}
            self.range = (0.0, 100.0)
            self.last = 0.0
            canceled = getattr(parent, "canceled", None)
            if canceled is not None:
                canceled.connect(self.cancel)  # C++ code checks this object's own flag

        def weigh(self, weights: Dict[str, float]) -> None:
            self.weights = dict(weights)

        def begin(self, stage: str) -> None:
            order = [s for s in STAGES if self.weights.get(s, 0) > 0]
            total = sum(self.weights[s] for s in order) or 1.0
            before = sum(self.weights[s] for s in order[:order.index(stage)]) if stage in order else None
            if before is None:  # a stage without a share: stays where the bar is
                self.range = (self.last, self.last)
                return
            low = 100.0 * before / total
            self.range = (max(low, self.last), 100.0 * (before + self.weights[stage]) / total)
            self.setProgress(0)

        def setProgress(self, value):  # noqa: N802
            super().setProgress(value)
            low, high = self.range
            overall = max(self.last, low + (high - low) * max(0.0, min(100.0, float(value))) / 100.0)
            self.last = overall
            self.parent.setProgress(overall)

        def isCanceled(self):  # noqa: N802
            return super().isCanceled() or bool(self.parent.isCanceled())

        def pushInfo(self, info):  # noqa: N802
            self.parent.pushInfo(info)

        def pushWarning(self, warning):  # noqa: N802
            self.parent.pushWarning(warning)

        def reportError(self, error, fatalError=False):  # noqa: N802,N803
            self.parent.reportError(error, fatalError)

        def pushDebugInfo(self, info):  # noqa: N802
            self.parent.pushDebugInfo(info)

        def pushCommandInfo(self, info):  # noqa: N802
            self.parent.pushCommandInfo(info)

        def pushConsoleInfo(self, info):  # noqa: N802
            self.parent.pushConsoleInfo(info)

    return StagedFeedback()


def _stage_weights(profile: PublicationProfile, raster_plans) -> Dict[str, float]:
    """Rough shares of the export time: the vector tiles take most of it."""
    raster_tiles = sum(getattr(plan, "tiles", 0) for plan in (raster_plans or {}).values())
    # The vector tiles' own share (TILES): they are made while the stages
    # after the datasets run, waited for before BUILD_RELEASE.
    background = os.environ.get("Q2VT_FOREGROUND_TILES") != "1"
    return {
        "PLAN": 1.0, "EXPORT_MVT": 30.0 if background else 75.0, "TILES": 45.0 if background else 0.0,
        "RECORDS": 2.0, "LEGEND": 1.0,
        "PARCELS": 2.0 if profile.parcel_info.enabled else 0.0,
        "RASTER": (min(25.0, 2.0 + raster_tiles / 150.0) if raster_plans else 0.0)
                  + (4.0 if profile.terrain.layer_id else 0.0),
        "BASEMAP": 6.0 if profile.basemap.kind == "protomaps" else 0.0,
        "STREETS": 2.0 if profile.interaction.search and (profile.interaction.street_search
                                                           or profile.interaction.address_layer_id) else 0.0,
        "BUILD_RELEASE": 5.0,
    }


def export_local(project, profile: PublicationProfile, extent_3857, feedback=None,
                 activate: bool = True, canaries=(), base_dir: Optional[str] = None,
                 stage_callback=None) -> LocalResult:
    """Run PLAN .. BUILD_RELEASE into ``<local folder>/<slug>/`` (main thread)."""
    from qgis.core import QgsProcessingFeedback  # pylint: disable=import-outside-toplevel
    from ..qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-outside-toplevel
    from . import qgis_model  # pylint: disable=import-outside-toplevel

    from qgis.core import QgsFeedback  # pylint: disable=import-outside-toplevel
    staged = _staged_feedback(feedback) if isinstance(feedback, QgsFeedback) else None
    if staged is not None:
        staged.weigh(_stage_weights(profile, None))  # raster shares once they are planned
        feedback = staged
    progress = feedback if isinstance(feedback, Progress) else Progress(feedback)
    processing_feedback = feedback if hasattr(feedback, "pushInfo") and hasattr(feedback, "setProgress") \
        else QgsProcessingFeedback()

    def stage(name):
        if stage_callback:
            stage_callback(name)
        progress.info(f"[{name}]")
        if staged is not None:
            staged.begin(name)

    stage("PLAN")
    from .crs import project_crs_info  # pylint: disable=import-outside-toplevel
    crs_info = project_crs_info(project)
    problems = qgis_model.check_profile_against_project(profile, project)
    if problems:
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID", " ".join(problems[:6]),
                              detail="\n".join(problems))
    info = profile.parcel_info
    if info.enabled:  # the clicked parcel's tile key must be its parcel id
        config = profile.layer(info.parcel_layer_id)
        if config is None or not config.included:
            raise PublishingError("Q2VT_PUB_PROFILE_INVALID",
                                  "Parcel report: publish the parcel layer (Map tab).")
        config.key_fields = [info.key_field]
        from .parcel_report import resolve_layers  # pylint: disable=import-outside-toplevel
        resolve_layers(project, info)  # layers and fields exist: fail now, not after tiling
    # Unique feature keys: checked now, not after the (long) tile export.
    key_problems = qgis_model.check_keys(project, profile, extent_3857, progress)
    if key_problems:
        raise PublishingError("Q2VT_PUB_IDENTITY", " ".join(key_problems[:-1][:4] + key_problems[-1:]),
                              detail="\n".join(key_problems))
    # Before the tile export (it drops layers that are not in the layer tree).
    street_area = _street_area(project, profile, extent_3857) \
        if profile.interaction.search and (profile.interaction.street_search
                                           or profile.interaction.address_layer_id) else None
    # Vector layers: MVT compiler; QGIS raster layers: their own raster archives.
    vector_profile, raster_configs = qgis_model.split_profile(profile, project)
    if not vector_profile.included_layer_ids():
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID",
                              "Select at least one vector layer to publish (raster layers and the "
                              "basemap are drawn under the vector map).")
    publication_dir, work_dir = publication_dirs(profile, base_dir)
    os.makedirs(work_dir, exist_ok=True)
    if _local_root(os.path.abspath(base_dir or profile.output.local_directory)):
        progress.info(f"Network output folder: the work files are on this computer, in {work_dir}")
    raster_plans = _plan_rasters(project, profile, raster_configs, extent_3857)
    if staged is not None:
        staged.weigh(_stage_weights(profile, raster_plans))
    keys, _ = qgis_model.feature_keys(vector_profile)

    cache = None
    if profile.output.reuse_unchanged:
        from ..core.export_cache import ExportCache  # pylint: disable=import-outside-toplevel
        cache = ExportCache(cache_dir(profile, base_dir))

    stage("EXPORT_MVT")
    exporter = QGIS2VectorTiles(
        min_zoom=profile.view.min_zoom, max_zoom=profile.view.max_zoom, extent=extent_3857,
        output_dir=work_dir, include_required_fields_only=1 if profile.output.include_all_fields else 0,
        cpu_percent=profile.output.cpu_percent, cent_source=profile.output.polygon_labels_base,
        background_type=2,  # vector-only: never the OSM / Blue Marble raster backgrounds
        viewer=0, feedback=processing_feedback, fidelity_mode=profile.output.fidelity_mode,
        overzoom=profile.output.overzoom, serve=False,
        static_package=profile.output.xyz_package, parallel=False,
        layer_ids=vector_profile.included_layer_ids(), archive_format="mbtiles",
        add_result_layer=False, feature_keys=keys,
        extra_tile_fields=qgis_model.tile_fields(vector_profile), cache=cache,
        scale_limits={c.layer_id: (c.min_scale, c.max_scale) for c in vector_profile.layers
                      if c.included and (c.min_scale or c.max_scale)},
        background_tiles=os.environ.get("Q2VT_FOREGROUND_TILES") != "1",
        fast_markers=profile.output.fast_markers)
    progress.check()
    # The tiles are made in the background (ogr2ogr processes) while the
    # stages after the datasets run: finish_tiles() before BUILD_RELEASE.
    try:
        if not exporter.convert_project_to_vector_tiles():
            raise PublishingError("Q2VT_PUB_BUNDLE_INVALID",
                                  "The vector tile export produced no tiles (see the export log).")
        bundle = exporter.export_bundle(profile.publication_id)
        bundle.crs = crs_info
        progress.check()

        stage("RECORDS")
        records = qgis_model.collect_records(
            project, vector_profile, exporter.rules, extent_3857,
            os.path.join(exporter.output_path, "q2vt_records.jsonl"), progress,
            max_zoom=profile.view.max_view_zoom)
        qgis_model.raise_identity_problems(records)
        progress.check()

        stage("LEGEND")
        legend_dir = os.path.join(exporter.output_path, "legend")
        swatches = qgis_model.render_swatches(project, vector_profile, legend_dir)
        model = qgis_model.logical_model(project, profile, exporter.rules, bundle.style, swatches)
        bundle.groups, bundle.layers = model["groups"], model["layers"]
        bundle.rules, bundle.components = model["rules"], model["components"]
        _filter_fields(bundle.layers, profile, records.filter_domains)
        themes = qgis_model.theme_presets(project, profile, model, bundle.warnings)
        bundle.feature_count = dict(records.counts)
        extra_files = {path: os.path.join(legend_dir, os.path.basename(path))
                       for path in set(swatches.values())}

        from .provenance import layer_logical_id  # pylint: disable=import-outside-toplevel
        searchable = {layer_logical_id(c.layer_id): list(c.search_fields)
                      for c in profile.layers if c.included and c.search_fields}
        lookup = {layer_logical_id(c.layer_id): {"popup": [p.field for p in c.popup_fields]}
                  for c in profile.layers if c.included and (c.popup_fields or c.deep_links)}

        streets: List[dict] = []  # street name and house number records (STREETS stage)

        def indexes(staging: str, manifest: dict) -> None:
            from .basemap import ADDRESSES_LAYER, STREETS_LAYER  # pylint: disable=import-outside-toplevel
            kinds = {record["layerId"] for record in streets}
            wanted = dict(searchable, **{k: ["name"] for k in (STREETS_LAYER, ADDRESSES_LAYER) if k in kinds})
            search = build_search_index(itertools.chain(_iter_records(records.path), streets), wanted,
                                        os.path.join(staging, "search"))
            if search:
                with open(os.path.join(staging, "search", "manifest.json"), "w", encoding="utf-8") as h:
                    json.dump(search, h, ensure_ascii=False)
                manifest["search"] = {"manifest": "search/manifest.json", "mode": search["mode"],
                                      "records": search["records"]}
            features = build_feature_index(_iter_records(records.path), lookup,
                                           os.path.join(staging, "features"))
            if features:
                with open(os.path.join(staging, "features", "manifest.json"), "w", encoding="utf-8") as h:
                    json.dump(features, h, ensure_ascii=False)
                manifest["featureLookup"] = {"manifest": "features/manifest.json",
                                             "records": features["records"]}

        approved: Dict[str, set] = {}
        filter_by_layer = {layer_logical_id(c.layer_id): {f.field for f in c.filter_fields}
                           | ({c.height_field} if c.height_field else set())  # the 3D height (reviewed)
                           for c in profile.layers if c.included}
        for component in bundle.components:
            names = filter_by_layer.get(component["layerId"], set())
            for source_layer in [component.get("sourceLayer")] + component["dependsOnSourceLayers"]:
                if source_layer:
                    approved.setdefault(source_layer, set()).update(names)

        def disclosure(staging: str, manifest: dict) -> None:
            archive = os.path.join(staging, "data", "map.pmtiles")
            assert_disclosure(archive, approved, allow_all=profile.output.include_all_fields)

        parcel_manifest = None
        if info.enabled:
            stage("PARCELS")
            from .parcel_report import cached_parcel_report  # pylint: disable=import-outside-toplevel
            parcel_dir = os.path.join(exporter.output_path, "parcels")
            report = cached_parcel_report(cache, project, profile, extent_3857, parcel_dir, legend_dir,
                                          progress.sub(0.0, 1.0))
            bundle.warnings.extend(report.warnings)
            for name in os.listdir(parcel_dir):
                extra_files[f"parcels/{name}"] = os.path.join(parcel_dir, name)
            extra_files.update(report.swatches)
            parcel_manifest = {"manifest": "parcels/manifest.json", "catalog": "parcels/catalog.json",
                               "layerId": report.manifest["layerId"], "records": report.records,
                               # The zoning layer and its code field: zone popups can show the zone's regulations.
                               "zoningLayerId": layer_logical_id(info.zoning_layer_id) if info.zoning_layer_id else None,
                               "zoneCodeField": info.zoning_code_field or None}
            progress.check()

        stage("RASTER")
        # Raster layers, then the terrain, each in its own part of the stage's bar.
        split = 0.6 if raster_configs and profile.terrain.layer_id else (1.0 if raster_configs else 0.0)
        bundle.raster_archives = _render_rasters(project, profile, raster_configs, raster_plans,
                                                 work_dir, progress.sub(0.0, split), bundle.warnings, cache)
        bundle.warnings.extend(_vector_blend_warnings(project, profile))
        bundle.terrain = _render_terrain(project, profile, extent_3857, work_dir, progress.sub(split, 1.0),
                                         bundle.warnings)
        progress.check()
        published = {r["layerId"] for r in bundle.raster_archives}
        dropped = {c["layerId"] for c in bundle.components if c["role"] == "raster"} - published
        if dropped:  # raster layers that draw nothing in the extent
            bundle.layers = [layer for layer in bundle.layers if layer["id"] not in dropped]
            bundle.components = [c for c in bundle.components if c["layerId"] not in dropped]
            for preset in themes["presets"]:
                preset["layers"] = {k: v for k, v in preset["layers"].items() if k not in dropped}

        if profile.basemap.kind == "protomaps":
            stage("BASEMAP")
            bundle.basemap = _prepare_basemap(profile, extent_3857, work_dir, progress)
            progress.check()

        addresses = profile.interaction.search and bool(profile.interaction.address_layer_id)
        if profile.interaction.search and (profile.interaction.street_search or addresses):
            stage("STREETS")
            pieces: Dict[str, list] = {}
            needs_streets = profile.interaction.street_search or (
                addresses and not profile.interaction.address_street_field)
            found = _street_records(street_area, profile, bundle.basemap, progress, bundle.warnings,
                                    pieces) if needs_streets else []
            if profile.interaction.street_search:
                streets.extend(found)
            if addresses:
                streets.extend(_address_records(project, profile, street_area, pieces, progress, bundle.warnings))
            progress.check()

        stage("TILES")
        if not exporter.finish_tiles():
            raise PublishingError("Q2VT_PUB_BUNDLE_INVALID",
                                  "The vector tile export produced no tiles (see the export log).")
        bundle.diagnostics_summary = exporter.diagnostics.counts()
        if cache is not None:
            cache.prune()
        progress.check()

        stage("BUILD_RELEASE")
        release = build_release(
            bundle, profile, publication_dir, transport="pmtiles", activate=activate,
            feedback=progress, canaries=canaries, extra_files=extra_files, extra_builders=[indexes],
            extra_validators=[disclosure], manifest_extra={
                "themes": themes, "ui": {"accent": profile.accent_color}, "parcelInfo": parcel_manifest})
        result = LocalResult(ReleaseState.LOCAL_READY, release, publication_dir, exporter.output_path,
                             records.path, dict(records.counts), list(release.warnings))
        if profile.output.archive == "both":
            result.mbtiles_copy = os.path.join(os.path.dirname(publication_dir),
                                               f"{profile.slug}-{release.release_id}.mbtiles")
            shutil.copyfile(bundle.mbtiles_path, result.mbtiles_copy)
        if profile.output.zip:
            result.zip_path = write_zip(release.release_dir, os.path.join(
                os.path.dirname(publication_dir), f"{profile.slug}-{release.release_id}.zip"))
        for warning in bundle.warnings:
            result.warnings.append(warning)
        return result
    finally:
        exporter.abort_tiles()  # cancelled or failed before the tiles were finished
        _copy_logs(exporter.export_folder, profile, base_dir)


def _plan_rasters(project, profile, configs, extent_3857) -> Dict[str, object]:
    """Zooms and tile estimates of the raster layers (fails early on runaway sizes)."""
    from .raster_tiles import MAX_TILES_PER_LAYER, plan_layer  # pylint: disable=import-outside-toplevel
    plans = {}
    for config in configs:
        layer = project.mapLayer(config.layer_id)
        plan = plan_layer(project, layer, config, profile, extent_3857)
        if plan.tiles > MAX_TILES_PER_LAYER:
            raise PublishingError(
                "Q2VT_PUB_PROFILE_INVALID",
                f'Raster layer "{layer.name()}": about {plan.tiles} tiles at zooms {plan.min_zoom}-'
                f"{plan.max_zoom}; lower its maximum zoom (Interaction tab) or the extent.")
        plans[config.layer_id] = plan
    return plans


def _render_rasters(project, profile, configs, plans, work_dir, progress, warnings,
                    cache=None) -> List[dict]:
    """Render each raster layer into ``<work>/rasters/<id>.pmtiles`` (reused
    from the export cache while the layer, its style and its settings are
    unchanged)."""
    from .provenance import layer_logical_id  # pylint: disable=import-outside-toplevel
    from .raster_tiles import (blend_name, raster_source_id, raster_style_layer_id,  # pylint: disable=import-outside-toplevel
                               tile_format)
    out = []
    folder = os.path.join(work_dir, "rasters")
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)
    total = max(1, sum(plans[c.layer_id].tiles for c in configs))
    done = 0
    # Metatiles rendered / tiles encoded at once (the CPU share of the export).
    threads = max(1, int((os.cpu_count() or 1) * profile.output.cpu_percent / 100))
    for config in configs:
        layer = project.mapLayer(config.layer_id)
        plan = plans[config.layer_id]
        lid = layer_logical_id(layer.id())
        start = done / total
        done += plan.tiles
        blend = _raster_blend(project, profile, layer, blend_name(layer), plan.warnings)
        descriptor = _cached_raster(cache, project, layer, config, plan, blend,
                                    os.path.join(folder, f"{lid}.pmtiles"),
                                    progress.sub(start, done / total), threads)
        warnings.extend(plan.warnings)
        if descriptor is None:
            warnings.append(f'Raster layer "{layer.name()}" draws nothing in the export extent; '
                            "it is not published.")
            continue
        progress.info(f'Raster layer "{layer.name()}": {descriptor.addressed_tiles} tiles, '
                      f"{descriptor.size_bytes / 1e6:.1f} MB ({tile_format(config.raster_format)})")
        out.append({"layerId": lid, "sourceId": raster_source_id(lid),
                    "styleLayerId": raster_style_layer_id(lid), "path": descriptor.path,
                    "descriptor": descriptor, "tileSize": 256})
    return out


def _cached_raster(cache, project, layer, config, plan, blend: str, output: str, progress,
                   threads: int = 1):
    """render_layer, or its archive from an earlier export with the same key
    (raster_tiles.raster_cache_key): rendering is the slow part and rasters
    rarely change."""
    from .raster_tiles import (descriptor_from_dict, descriptor_to_dict,  # pylint: disable=import-outside-toplevel
                               raster_cache_key, render_layer)
    key = raster_cache_key(project, layer, config, plan, blend) if cache is not None else None
    if key:
        hit = cache.get_bundle("rasters", key)
        if hit is not None:
            meta, folder = hit
            plan.warnings.extend(meta.get("warnings", []))
            progress.info(f'Raster layer "{layer.name()}": unchanged, reused from the export cache')
            if meta.get("empty"):
                return None
            source = os.path.join(folder, "layer.pmtiles")
            try:
                os.link(source, output)  # same disk: no copy of a large archive
            except OSError:
                shutil.copyfile(source, output)
            return descriptor_from_dict(meta["descriptor"], output)
    before = len(plan.warnings)
    descriptor = render_layer(project, layer, config, plan, output, progress,
                              title=config.title or layer.name(), blend=blend, threads=threads)
    if key:
        meta = {"warnings": plan.warnings[before:], "empty": descriptor is None}
        if descriptor is not None:
            meta["descriptor"] = descriptor_to_dict(descriptor)
        cache.put_bundle("rasters", key, meta,
                         {"layer.pmtiles": descriptor.path} if descriptor is not None else {})
    return descriptor


def _raster_blend(project, profile, layer, mode: str, warnings: List[str]) -> str:
    """How a raster layer's QGIS blend mode is drawn in the browser (which has
    none): "multiply" / "screen" are converted to an equivalent normal image
    (raster_tiles.blend_to_alpha). A multiply layer with nothing published
    below it on a white map background is the same as normal."""
    if mode == "normal":
        return "normal"
    if mode in ("multiply", "screen"):
        if mode == "multiply" and not _published_below(project, profile, layer) \
                and project.backgroundColor().name().lower() == "#ffffff":
            return "normal"
        return mode
    warnings.append(f'Raster layer "{layer.name()}": the {mode} blend mode has no web equivalent; '
                    "it is drawn as normal.")
    return "normal"


def _published_below(project, profile, layer) -> bool:
    """Whether a published layer (or a basemap) is drawn below ``layer``."""
    if profile.basemap.kind != "none" or profile.basemap.xyz:
        return True
    included = {c.layer_id for c in profile.layers if c.included}
    order = [l.id() for l in project.layerTreeRoot().layerOrder()]  # top first
    if layer.id() not in order:
        return True
    return any(lid in included for lid in order[order.index(layer.id()) + 1:])


def _vector_blend_warnings(project, profile) -> List[str]:
    """Vector layers drawn with a blend mode: the browser draws them normally."""
    from qgis.core import QgsVectorLayer  # pylint: disable=import-outside-toplevel
    from .raster_tiles import blend_name  # pylint: disable=import-outside-toplevel
    out = []
    for config in profile.layers:
        layer = project.mapLayer(config.layer_id)
        if not config.included or not isinstance(layer, QgsVectorLayer):
            continue
        modes = {blend_name(layer)}
        try:
            feature_mode = int(getattr(layer.featureBlendMode(), "value", layer.featureBlendMode()))
            modes.add("normal" if feature_mode == 0 else f"feature {feature_mode}")
        except (AttributeError, TypeError, ValueError):
            pass
        modes.discard("normal")
        if modes:
            out.append(f'Layer "{layer.name()}": its blend mode ({", ".join(sorted(modes))}) has no web '
                       "equivalent; it is drawn as normal (opacity is kept).")
    return out


def _street_area(project, profile, extent_3857):
    """The street search area in EPSG:4326: the extent layer's polygons
    (merged), or the export extent."""
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,  # pylint: disable=import-outside-toplevel
                           QgsRectangle, QgsVectorLayer, QgsWkbTypes)
    wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
    layer = project.mapLayer(profile.view.extent_layer) if profile.view.extent_layer else None
    if isinstance(layer, QgsVectorLayer) and layer.geometryType() == QgsWkbTypes.PolygonGeometry:
        to_wgs84 = QgsCoordinateTransform(layer.crs(), wgs84, project)
        parts = []
        for feature in layer.getFeatures():
            geometry = QgsGeometry(feature.geometry())
            if geometry.isEmpty():
                continue
            geometry.transform(to_wgs84)
            parts.append(geometry.makeValid())
        if parts:
            return QgsGeometry.unaryUnion(parts), layer.name()
    box = extent_3857 if hasattr(extent_3857, "xMinimum") else QgsRectangle(*extent_3857)
    if isinstance(layer, QgsVectorLayer):  # lines / points: the layer's extent
        box = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:3857"),
                                     project).transformBoundingBox(layer.extent())
    geometry = QgsGeometry.fromRect(box)
    geometry.transform(QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:3857"), wgs84, project))
    return geometry, ""


def _render_terrain(project, profile, extent_3857, work_dir, progress, warnings) -> Optional[dict]:
    """The DEM layer as terrain-RGB tiles (terrain.py); None without one. A
    failure is a warning: the map is published without terrain."""
    from qgis.core import QgsRasterLayer, QgsRectangle  # pylint: disable=import-outside-toplevel
    from . import terrain  # pylint: disable=import-outside-toplevel
    config = profile.terrain
    if not config.layer_id:
        return None
    layer = project.mapLayer(config.layer_id)
    if not isinstance(layer, QgsRasterLayer):
        warnings.append("Terrain: the elevation layer is not in the project; the map is published without it.")
        return None
    box = extent_3857 if isinstance(extent_3857, QgsRectangle) else QgsRectangle(*extent_3857)
    area = terrain.grow((box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()))
    low, high = terrain.terrain_zooms(layer, profile.view.min_zoom, terrain.latitude_of(area))
    output = os.path.join(work_dir, "terrain.pmtiles")
    if os.path.exists(output):
        os.remove(output)
    progress.info(f'Terrain: "{layer.name()}", zooms {low}-{high}')
    try:
        descriptor = terrain.render_terrain(layer, area, low, high, output, progress)
    except Cancelled:
        raise
    except PublishingError as error:
        warnings.append(f"{error}; the map is published without terrain.")
        return None
    except (RuntimeError, MemoryError, OSError) as error:  # GDAL (with exceptions on), memory
        warnings.append(f'Terrain: "{layer.name()}" could not be read ({error}); '
                        "the map is published without terrain.")
        return None
    if descriptor is None:
        warnings.append(f'Terrain: "{layer.name()}" has no heights inside the extent.')
        return None
    progress.info(f"Terrain: {descriptor.addressed_tiles} tiles, {descriptor.size_bytes / 1e6:.1f} MB")
    return {"path": output, "descriptor": descriptor, "hillshade": bool(config.hillshade),
            "exaggeration": float(config.exaggeration)}


def _address_records(project, profile, street_area, pieces, progress, warnings) -> List[dict]:
    """House numbers of the address layer inside the street search area, as
    search records "street number" (the street: its field, else the nearest
    named OpenStreetMap street)."""
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,  # pylint: disable=import-outside-toplevel
                           QgsVectorLayer)
    from . import basemap  # pylint: disable=import-outside-toplevel
    config = profile.interaction
    layer = project.mapLayer(config.address_layer_id)
    if not isinstance(layer, QgsVectorLayer) or layer.fields().indexOf(config.address_number_field) < 0:
        warnings.append("House number search: the address layer or its number field is missing; "
                        "the map is published without house numbers.")
        return []
    street_field = config.address_street_field
    if street_field and layer.fields().indexOf(street_field) < 0:
        street_field = ""
    area, _name = street_area
    engine = QgsGeometry.createGeometryEngine(area.constGet()) if not area.isEmpty() else None
    if engine:
        engine.prepareGeometry()
    to_wgs84 = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:4326"), project)
    points = []
    for feature in layer.getFeatures():
        geometry = QgsGeometry(feature.geometry())
        if geometry.isEmpty():
            continue
        geometry.transform(to_wgs84)
        point = geometry if geometry.type() == 0 and not geometry.isMultipart() else geometry.pointOnSurface()
        if engine and not engine.intersects(point.constGet()):
            continue
        xy = point.asPoint()

        def value(name):
            raw = feature[name] if name else None
            return None if raw is None or (hasattr(raw, "isNull") and raw.isNull()) else raw
        points.append((xy.x(), xy.y(), value(config.address_number_field), value(street_field)))
    index = basemap.StreetIndex(pieces) if pieces and not street_field else None
    records, missing = basemap.address_records(points, index)
    progress.info(f"House numbers: {len(records)} addresses for the search")
    if missing:
        warnings.append(f"House number search: {missing} house numbers have no named street within "
                        f"{int(basemap.ADDRESS_STREET_M)} m and are left out.")
    return records


def _street_records(street_area, profile, prepared_basemap, progress, warnings,
                    pieces: Optional[Dict[str, list]] = None) -> List[dict]:
    """Named OpenStreetMap streets inside the extent layer for the search
    (from the bundled basemap, or read from its source when there is none).
    A failure is a warning: the map is published without street search."""
    from qgis.core import QgsGeometry, QgsPointXY  # pylint: disable=import-outside-toplevel
    from . import basemap  # pylint: disable=import-outside-toplevel
    area, area_name = street_area
    if area.isEmpty():
        return []
    engine = QgsGeometry.createGeometryEngine(area.constGet())
    engine.prepareGeometry()

    def clip(line):
        geometry = QgsGeometry.fromPolylineXY([QgsPointXY(x, y) for x, y in line])
        if engine.contains(geometry.constGet()):
            return [line]
        if not engine.intersects(geometry.constGet()):
            return []
        inside = geometry.intersection(area)
        if inside.isEmpty():
            return []
        parts = inside.asMultiPolyline() if inside.isMultipart() else [inside.asPolyline()]
        return [[(p.x(), p.y()) for p in part] for part in parts if len(part) >= 2]

    box = area.boundingBox()
    bbox = (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum())
    source = prepared_basemap["archive"] if prepared_basemap else profile.basemap.source
    progress.info("Street names: reading " + ("the basemap" if prepared_basemap else
                                              (source or "the latest Protomaps build")) +
                  (f' inside "{area_name}"' if area_name else " in the export extent") + " ...")
    try:
        reader = basemap.LocalReader(source) if prepared_basemap else basemap.open_source(source)
        try:
            records = basemap.street_records(reader, bbox, clip, profile.locale, progress.sub(0.0, 1.0),
                                             pieces_out=pieces)
        finally:
            reader.close()
    except (PublishingError, OSError, ValueError) as error:
        warnings.append(f"Street search: the street names could not be read ({error}); "
                        "the map is published without them.")
        return []
    progress.info(f"Street names: {len(records)} streets for the search")
    if not records:
        warnings.append("Street search: no named street was found in the area.")
    return records


def _prepare_basemap(profile, extent_3857, work_dir, progress) -> dict:
    """Extract the basemap area, generate its glyphs, prepare its flavors."""
    from . import basemap  # pylint: disable=import-outside-toplevel
    config = profile.basemap
    folder = os.path.join(work_dir, "basemap")
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)
    docs = {flavor: basemap.load_flavor(flavor, profile.locale) for flavor in config.flavors}
    keys = set()
    for doc in docs.values():
        keys |= basemap.text_keys(doc["layers"])
    extent = (extent_3857.xMinimum(), extent_3857.yMinimum(), extent_3857.xMaximum(), extent_3857.yMaximum()) \
        if hasattr(extent_3857, "xMinimum") else tuple(extent_3857)
    detail, overview = basemap.areas(extent, config.padding, config.overview_km)
    progress.info("Basemap: reading " + (config.source or "the latest Protomaps build") + " ...")
    reader = basemap.open_source(config.source)
    try:
        result = basemap.extract(reader, os.path.join(folder, "basemap.pmtiles"), detail, overview,
                                 config.max_zoom, config.overview_zoom, keys, progress.sub(0.0, 0.85))
    finally:
        reader.close()
    progress.info(f"Basemap: {result.descriptor.addressed_tiles} tiles, "
                  f"{result.descriptor.size_bytes / 1e6:.1f} MB ({result.requests} requests)")
    fonts = basemap.generate_glyphs(result.characters, os.path.join(folder, "glyphs"))
    flavors = {flavor: {"layers": basemap.prepare_flavor(doc, basemap.font_map()), "colors": doc["colors"]}
               for flavor, doc in docs.items()}
    return {"archive": result.descriptor.path, "descriptor": result.descriptor,
            "glyphs_dir": os.path.join(folder, "glyphs"), "flavors": flavors,
            "initial": config.initial, "fonts": fonts}
