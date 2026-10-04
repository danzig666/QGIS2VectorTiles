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

import itertools
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

STAGES = ["PLAN", "EXPORT_MVT", "RECORDS", "LEGEND", "PARCELS", "RASTER", "BASEMAP", "STREETS", "BUILD_RELEASE",
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


def publication_dirs(profile: PublicationProfile, base: Optional[str] = None):
    base = base or profile.output.local_directory
    if not base:
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID", "Choose a local output folder.")
    base = os.path.abspath(base)
    return os.path.join(base, profile.slug), os.path.join(base, ".q2vt-work", profile.slug)


def cache_dir(profile: PublicationProfile, base: Optional[str] = None) -> str:
    """The export cache of a local output folder (shared by its publications)."""
    base = base or profile.output.local_directory
    if not base:
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID", "Choose a local output folder.")
    return os.path.join(os.path.abspath(base), ".q2vt-cache")


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
    from .crs import project_crs_info  # pylint: disable=import-outside-toplevel
    crs_info = project_crs_info(project)
    # Before the tile export (it drops layers that are not in the layer tree).
    street_area = _street_area(project, profile, extent_3857) \
        if profile.interaction.search and profile.interaction.street_search else None
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
    # Vector layers: MVT compiler; QGIS raster layers: their own raster archives.
    vector_profile, raster_configs = qgis_model.split_profile(profile, project)
    if not vector_profile.included_layer_ids():
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID",
                              "Select at least one vector layer to publish (raster layers and the "
                              "basemap are drawn under the vector map).")
    publication_dir, work_dir = publication_dirs(profile, base_dir)
    os.makedirs(work_dir, exist_ok=True)
    raster_plans = _plan_rasters(project, profile, raster_configs, extent_3857)
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
                      if c.included and (c.min_scale or c.max_scale)})
    progress.check()
    if not exporter.convert_project_to_vector_tiles():
        raise PublishingError("Q2VT_PUB_BUNDLE_INVALID",
                              "The vector tile export produced no tiles (see the export log).")
    bundle = exporter.export_bundle(profile.publication_id)
    bundle.crs = crs_info
    if cache is not None:
        cache.prune()
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

    streets: List[dict] = []  # street name records (filled in the STREETS stage)

    def indexes(staging: str, manifest: dict) -> None:
        from .basemap import STREETS_LAYER  # pylint: disable=import-outside-toplevel
        wanted = dict(searchable, **({STREETS_LAYER: ["name"]} if streets else {}))
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
                           "layerId": report.manifest["layerId"], "records": report.records}
        progress.check()

    stage("RASTER")
    bundle.raster_archives = _render_rasters(project, profile, raster_configs, raster_plans,
                                             work_dir, progress, bundle.warnings)
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

    if profile.interaction.search and profile.interaction.street_search:
        stage("STREETS")
        streets.extend(_street_records(street_area, profile, bundle.basemap, progress, bundle.warnings))
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


def _render_rasters(project, profile, configs, plans, work_dir, progress, warnings) -> List[dict]:
    """Render each raster layer into ``<work>/rasters/<id>.pmtiles``."""
    from .provenance import layer_logical_id  # pylint: disable=import-outside-toplevel
    from .raster_tiles import raster_source_id, raster_style_layer_id, render_layer  # pylint: disable=import-outside-toplevel
    out = []
    folder = os.path.join(work_dir, "rasters")
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(folder)
    total = max(1, sum(plans[c.layer_id].tiles for c in configs))
    done = 0
    for config in configs:
        layer = project.mapLayer(config.layer_id)
        plan = plans[config.layer_id]
        warnings.extend(plan.warnings)
        lid = layer_logical_id(layer.id())
        start = done / total
        done += plan.tiles
        descriptor = render_layer(project, layer, config, plan,
                                  os.path.join(folder, f"{lid}.pmtiles"),
                                  progress.sub(start, done / total), title=config.title or layer.name())
        if descriptor is None:
            warnings.append(f'Raster layer "{layer.name()}" draws nothing in the export extent; '
                            "it is not published.")
            continue
        progress.info(f'Raster layer "{layer.name()}": {descriptor.addressed_tiles} tiles, '
                      f"{descriptor.size_bytes / 1e6:.1f} MB ({config.raster_format})")
        out.append({"layerId": lid, "sourceId": raster_source_id(lid),
                    "styleLayerId": raster_style_layer_id(lid), "path": descriptor.path,
                    "descriptor": descriptor, "tileSize": 256})
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


def _street_records(street_area, profile, prepared_basemap, progress, warnings) -> List[dict]:
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
            records = basemap.street_records(reader, bbox, clip, profile.locale, progress.sub(0.0, 1.0))
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
