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

import json
import os
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .disclosure import assert_disclosure
from .errors import PublishingError
from .feature_index import build_feature_index
from .models import PublicationProfile, ReleaseState
from .progress import Progress
from .search_index import build_search_index
from .web_builder import ReleaseResult, build_release, write_zip

STAGES = ["PLAN", "EXPORT_MVT", "RECORDS", "LEGEND", "BUILD_RELEASE", "UPLOAD",
          "VERIFY_PUBLIC", "ACTIVATE", "COMPLETE"]


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


def publication_dirs(profile: PublicationProfile, base: Optional[str] = None):
    base = base or profile.output.local_directory
    if not base:
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID", "Choose a local output folder.")
    base = os.path.abspath(base)
    return os.path.join(base, profile.slug), os.path.join(base, ".q2vt-work", profile.slug)


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


def export_local(project, profile: PublicationProfile, extent_3857, feedback=None,
                 activate: bool = True, canaries=(), base_dir: Optional[str] = None,
                 stage_callback=None) -> LocalResult:
    """Run PLAN .. BUILD_RELEASE into ``<local folder>/<slug>/`` (main thread)."""
    from qgis.core import QgsProcessingFeedback  # pylint: disable=import-outside-toplevel
    from ..qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-outside-toplevel
    from . import qgis_model  # pylint: disable=import-outside-toplevel

    progress = feedback if isinstance(feedback, Progress) else Progress(feedback)
    processing_feedback = feedback if hasattr(feedback, "pushInfo") and hasattr(feedback, "setProgress") \
        else QgsProcessingFeedback()

    def stage(name):
        if stage_callback:
            stage_callback(name)
        progress.info(f"[{name}]")

    stage("PLAN")
    problems = qgis_model.check_profile_against_project(profile, project)
    if problems:
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID", " ".join(problems[:6]),
                              detail="\n".join(problems))
    if not profile.included_layer_ids():
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID", "Select at least one layer to publish.")
    publication_dir, work_dir = publication_dirs(profile, base_dir)
    os.makedirs(work_dir, exist_ok=True)
    keys, _ = qgis_model.feature_keys(profile)

    stage("EXPORT_MVT")
    exporter = QGIS2VectorTiles(
        min_zoom=profile.view.min_zoom, max_zoom=profile.view.max_zoom, extent=extent_3857,
        output_dir=work_dir, include_required_fields_only=1 if profile.output.include_all_fields else 0,
        cpu_percent=profile.output.cpu_percent, cent_source=profile.output.polygon_labels_base,
        background_type=2,  # vector-only: never the OSM / Blue Marble raster backgrounds
        viewer=0, feedback=processing_feedback, fidelity_mode=profile.output.fidelity_mode,
        overzoom=profile.output.overzoom, serve=False,
        static_package=profile.output.xyz_package, parallel=False,
        layer_ids=profile.included_layer_ids(), archive_format="mbtiles",
        add_result_layer=False, feature_keys=keys,
        extra_tile_fields=qgis_model.tile_fields(profile))
    progress.check()
    if not exporter.convert_project_to_vector_tiles():
        raise PublishingError("Q2VT_PUB_BUNDLE_INVALID",
                              "The vector tile export produced no tiles (see the export log).")
    bundle = exporter.export_bundle(profile.publication_id)
    progress.check()

    stage("RECORDS")
    records = qgis_model.collect_records(
        project, profile, exporter.rules, extent_3857,
        os.path.join(exporter.output_path, "q2vt_records.jsonl"), progress,
        max_zoom=profile.view.max_view_zoom)
    qgis_model.raise_identity_problems(records)
    progress.check()

    stage("LEGEND")
    legend_dir = os.path.join(exporter.output_path, "legend")
    swatches = qgis_model.render_swatches(project, profile, legend_dir)
    model = qgis_model.logical_model(project, profile, exporter.rules, bundle.style, swatches)
    bundle.groups, bundle.layers = model["groups"], model["layers"]
    bundle.rules, bundle.components = model["rules"], model["components"]
    _filter_fields(bundle.layers, profile, records.filter_domains)
    bundle.feature_count = dict(records.counts)
    extra_files = {path: os.path.join(legend_dir, os.path.basename(path))
                   for path in set(swatches.values())}

    from .provenance import layer_logical_id  # pylint: disable=import-outside-toplevel
    searchable = {layer_logical_id(c.layer_id): list(c.search_fields)
                  for c in profile.layers if c.included and c.search_fields}
    lookup = {layer_logical_id(c.layer_id): {"popup": [p.field for p in c.popup_fields]}
              for c in profile.layers if c.included and (c.popup_fields or c.deep_links)}

    def indexes(staging: str, manifest: dict) -> None:
        search = build_search_index(_iter_records(records.path), searchable,
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
                       for c in profile.layers if c.included}
    for component in bundle.components:
        names = filter_by_layer.get(component["layerId"], set())
        for source_layer in [component.get("sourceLayer")] + component["dependsOnSourceLayers"]:
            if source_layer:
                approved.setdefault(source_layer, set()).update(names)

    def disclosure(staging: str, manifest: dict) -> None:
        archive = os.path.join(staging, "data", "map.pmtiles")
        assert_disclosure(archive, approved, allow_all=profile.output.include_all_fields)

    stage("BUILD_RELEASE")
    release = build_release(
        bundle, profile, publication_dir, transport="pmtiles", activate=activate,
        feedback=progress, canaries=canaries, extra_files=extra_files, extra_builders=[indexes],
        extra_validators=[disclosure])
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
