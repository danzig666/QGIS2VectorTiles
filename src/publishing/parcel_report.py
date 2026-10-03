"""
Parcel report (telekinformáció), computed at export time in QGIS from the
exact geometry; the viewer only shows the result.

For every parcel inside the export extent:

* its full area (planimetric, in the parcel layer's projected CRS, e.g. EOV);
* its parts: the parcel intersected with the zoning polygons, split further by
  the configured cut lines (regulation line, zone boundary); per part the zone
  code, area, share, the zone's legend symbol, the chosen zone values, the cut
  lines bounding it inside the parcel and an anchor point (for a numbered
  marker on the map);
* the restrictions touching it: polygon overlap (or the protection distance of
  lines and points) with area and share, the legend symbol(s) of the features
  actually involved, and their names (only an approved field).

Slivers below ``min_area`` (digitising noise along shared boundaries) and
restriction overlaps below ``min_share`` are ignored. Records are sharded by
FNV-1a of the parcel key like the feature lookup; no geometry is written.
"""

import dataclasses
import hashlib
import json
import math
import os
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsExpression, QgsExpressionContext,
                       QgsExpressionContextUtils, QgsFeatureRequest, QgsGeometry, QgsRectangle,
                       QgsRenderContext, QgsSpatialIndex, QgsSymbolLayerUtils, QgsWkbTypes)
from qgis.PyQt.QtCore import QSize

from .errors import PublishingError
from .feature_index import shard_key
from .identifiers import key_expression
from .models import ParcelInfoConfig, PublicationProfile
from .progress import Progress
from .provenance import layer_logical_id

TARGET_SHARD_RECORDS = 300
EDGE_TOLERANCE = 0.1        # m: a cut line counts along a part boundary within this distance
MIN_CUT_LENGTH = 0.5        # m: shorter shared stretches are noise
MAX_NAMES = 12


def _json_value(value):
    from .qgis_model import json_value  # pylint: disable=import-outside-toplevel
    return json_value(value)


def _geometry_kind(layer) -> int:
    kind = layer.geometryType()
    return int(getattr(kind, "value", kind))


def working_crs(layer) -> QgsCoordinateReferenceSystem:
    """The parcel layer's CRS when projected (EOV etc.), else a UTM zone."""
    crs = layer.crs()
    if crs.isValid() and not crs.isGeographic():
        return crs
    center = layer.extent().center()  # geographic CRS: x = longitude, y = latitude
    zone = int((center.x() + 180) // 6) + 1
    return QgsCoordinateReferenceSystem(f"EPSG:{32600 + zone if center.y() >= 0 else 32700 + zone}")


class _Legend:
    """The legend graphic of each feature: its symbols as QGIS draws them
    (rule, category and data-defined colours included), rendered to a small
    PNG and deduplicated by content; labelled with the most specific active
    legend entry it falls in."""

    # Rules may only apply in a scale range: evaluate at several scales.
    SCALES = (0, 1000, 5000, 25000, 100000, 500000)
    SIZE = 32

    def __init__(self, layer, out_dir: str, title: str = ""):
        self.layer = layer
        self.out_dir = out_dir
        self.title = title or layer.name()
        self.items = {}
        self.files: Dict[str, str] = {}     # image digest -> release path
        self.started = []                    # (renderer, context) per scale
        renderer = layer.renderer()
        if renderer is None or type(renderer).__name__ == "QgsNullSymbolRenderer":
            return
        try:
            for item in renderer.legendSymbolItems():
                if item.symbol() is not None:
                    self.items[item.ruleKey()] = item
        except RuntimeError:
            self.items = {}
        # Rule-based renderers pick their scale-dependent rules in startRender().
        for scale in self.SCALES:
            clone = renderer.clone()
            context = QgsRenderContext()
            context.setRendererScale(scale)
            context.setExpressionContext(QgsExpressionContext(
                QgsExpressionContextUtils.globalProjectLayerScopes(layer)))
            clone.startRender(context, layer.fields())
            self.started.append((clone, context))

    def label(self, feature) -> str:
        """Most specific labelled legend entry of ``feature`` ("" if none)."""
        found: List[str] = []
        for renderer, context in self.started:
            context.expressionContext().setFeature(feature)
            try:
                keys = renderer.legendKeysForFeature(feature, context)
            except (RuntimeError, TypeError):
                continue
            found.extend(key for key in keys if key in self.items and key not in found)
        labelled = [key for key in found if (self.items[key].label() or "").strip()]
        if not labelled:
            return ""
        deepest = max(labelled, key=lambda key: self.items[key].level())
        return self.items[deepest].label().strip()

    def describe(self, feature) -> Optional[Tuple[str, str]]:
        """(label, "legend/pr-….png") of ``feature``, or None (no symbol)."""
        from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtGui import QColor, QImage, QPainter  # pylint: disable=import-outside-toplevel
        symbols = []
        for renderer, context in self.started:
            context.expressionContext().setFeature(feature)
            try:
                symbols = renderer.symbolsForFeature(feature, context)
            except (RuntimeError, TypeError):
                symbols = []
            if symbols:
                context_used = context
                break
        if not symbols:
            return None
        image = QImage(self.SIZE * 2, self.SIZE * 2, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        render = QgsRenderContext.fromQPainter(painter)
        render.setExpressionContext(context_used.expressionContext())
        for symbol in symbols:
            symbol.drawPreviewIcon(painter, image.size(), render, False, context_used.expressionContext())
        painter.end()
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.scaled(self.SIZE, self.SIZE).save(buffer, "PNG")
        digest = hashlib.sha1(bytes(data)).hexdigest()[:14]
        if digest not in self.files:
            name = f"pr-{digest}.png"
            with open(os.path.join(self.out_dir, name), "wb") as handle:
                handle.write(bytes(data))
            self.files[digest] = f"legend/{name}"
        return self.label(feature), self.files[digest]

    @property
    def swatches(self) -> Dict[str, str]:
        return {path: path for path in self.files.values()}

    def close(self):
        for renderer, context in self.started:
            renderer.stopRender(context)
        self.started = []


@dataclass
class _Feature:
    geometry: QgsGeometry
    attributes: dict
    keys: List[str]
    engine: object = None


class _Index:
    """Features of one layer in the working CRS with a spatial index;
    lines/points optionally buffered (protection distance)."""

    def __init__(self, project, layer, crs, fields=(), legend: Optional[_Legend] = None, buffer_m: float = 0.0,
                 area: Optional[QgsRectangle] = None):
        self.layer = layer
        self.items: Dict[int, _Feature] = {}
        self.index = QgsSpatialIndex()
        transform = QgsCoordinateTransform(layer.crs(), crs, project.transformContext())
        request = QgsFeatureRequest()
        if area is not None:
            back = QgsCoordinateTransform(crs, layer.crs(), project.transformContext())
            try:
                request.setFilterRect(back.transformBoundingBox(area))
            except Exception:  # noqa: BLE001 - outside the transform's area: read everything
                pass
        for feature in layer.getFeatures(request):
            geometry = QgsGeometry(feature.geometry())
            if geometry.isNull() or geometry.isEmpty():
                continue
            try:
                geometry.transform(transform)
            except Exception:  # noqa: BLE001
                continue
            if not geometry.isGeosValid():
                geometry = geometry.makeValid()
            if buffer_m > 0:
                geometry = geometry.buffer(buffer_m, 8)
            description = legend.describe(feature) if legend is not None else None
            item = _Feature(geometry, {name: _json_value(feature[name]) for name in fields if name},
                            [description] if description else [])
            self.items[feature.id()] = item
            self.index.addFeature(feature.id(), geometry.boundingBox())

    def near(self, geometry: QgsGeometry) -> List[_Feature]:
        return [self.items[i] for i in self.index.intersects(geometry.boundingBox())]

    @staticmethod
    def engine(item: _Feature):
        if item.engine is None:
            item.engine = QgsGeometry.createGeometryEngine(item.geometry.constGet())
            item.engine.prepareGeometry()
        return item.engine


def split_by_lines(polygon: QgsGeometry, lines: List[QgsGeometry]) -> List[QgsGeometry]:
    """Faces of ``polygon`` cut by ``lines`` (lines outside it are ignored)."""
    if not lines:
        return [polygon]
    boundary = QgsGeometry(polygon.constGet().boundary())
    clip = polygon.buffer(0.01, 2)
    parts = [boundary] + [line.intersection(clip) for line in lines]
    parts = [p for p in parts if p is not None and not p.isEmpty()]
    noded = QgsGeometry.unaryUnion(parts)
    faces = QgsGeometry.polygonize([noded])
    out = []
    for face in faces.asGeometryCollection() if not faces.isEmpty() else []:
        if face.isEmpty() or not polygon.contains(face.pointOnSurface()):
            continue
        face = face.intersection(polygon)
        if not face.isEmpty():
            out.append(face)
    return out or [polygon]


@dataclass
class ParcelReportResult:
    manifest: dict
    catalog: dict
    records: int = 0
    swatches: Dict[str, str] = field(default_factory=dict)   # release path -> local file
    warnings: List[str] = field(default_factory=list)


ID_NAMES = {"fid", "id", "ogc_fid", "objectid", "object_id", "gid", "pk", "rowid", "oid", "feature_id"}
CODE_NAMES = ("szab_ov", "szab_kod", "ovezet", "övezet", "ovezetkod", "övezetkód", "kod", "kód",
              "zone", "zone_code", "zonecode", "zoning", "code")


def looks_like_id(layer, name: str) -> bool:
    """A feature id field (fid, id, objectid, the primary key): numbers that
    mean nothing to a reader."""
    if not name:
        return True
    index = layer.fields().indexOf(name)
    if index < 0:
        return True
    try:
        if index in layer.primaryKeyAttributes():
            return True
    except (AttributeError, TypeError):
        pass
    return name.lower() in ID_NAMES


def guess_zone_field(layer) -> str:
    """The zone code field of a zone layer, from how QGIS uses the layer: the
    categorized renderer's field, the field its rules / labels test, a usual
    code name; never a feature id. "" if none."""
    from qgis.core import QgsExpression  # pylint: disable=import-outside-toplevel
    names = [f.name() for f in layer.fields()]
    score = {name: 0.0 for name in names if not looks_like_id(layer, name)}
    renderer = layer.renderer()
    attribute = getattr(renderer, "classAttribute", lambda: "")() if renderer is not None else ""
    if attribute in score:
        score[attribute] += 100
    expressions = []
    root = getattr(renderer, "rootRule", lambda: None)() if renderer is not None else None
    if root is not None:
        expressions += [rule.filterExpression() for rule in root.descendants() if rule.filterExpression()]
    labeling = layer.labeling() if layer.labelsEnabled() else None
    if labeling is not None:
        try:
            settings = labeling.settings()
            expressions.append(settings.fieldName if settings.isExpression else f'"{settings.fieldName}"')
        except (AttributeError, TypeError):
            pass
    for text in expressions:
        for column in QgsExpression(text).referencedColumns():
            if column in score:
                score[column] += 10
    for name in score:
        if name.lower() in CODE_NAMES:
            score[name] += 5 + (len(CODE_NAMES) - CODE_NAMES.index(name.lower())) / 100
    best = max(score.items(), key=lambda item: item[1], default=("", 0))
    return best[0] if best[1] > 0 else ""


def build_parcel_report(project, profile: PublicationProfile, extent_3857: QgsRectangle, out_dir: str,
                        legend_dir: str, progress: Optional[Progress] = None) -> ParcelReportResult:
    """Write ``parcels/`` (catalog, manifest, shards) into ``out_dir``."""
    progress = progress or Progress()
    info: ParcelInfoConfig = profile.parcel_info
    parcels = project.mapLayer(info.parcel_layer_id)
    zoning = project.mapLayer(info.zoning_layer_id)
    if parcels is None or zoning is None:
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID", "Parcel report: choose the parcel and zone layers.")
    warnings: List[str] = []
    # The zone code names the parts; a feature id there showed meaningless
    # numbers ("241") with the real code only in a row below.
    code_field = info.zoning_code_field
    if looks_like_id(zoning, code_field):
        guessed = guess_zone_field(zoning)
        if guessed:
            warnings.append(f"Parcel report: zone code field {code_field or '(none)'} is a feature id; "
                            f"using {guessed}.")
            code_field = guessed
            info = dataclasses.replace(info, zoning_code_field=guessed)
    if parcels.fields().indexOf(info.key_field) < 0 or zoning.fields().indexOf(code_field) < 0:
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID",
                              "Parcel report: the parcel id or the zone code field does not exist.")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(legend_dir, exist_ok=True)
    crs = working_crs(parcels)
    web = QgsCoordinateReferenceSystem("EPSG:3857")
    wgs = QgsCoordinateReferenceSystem("EPSG:4326")
    area = QgsCoordinateTransform(web, crs, project.transformContext()).transformBoundingBox(extent_3857)
    to_wgs = QgsCoordinateTransform(crs, wgs, project.transformContext())

    zone_legend = _Legend(zoning, legend_dir)
    zones = _Index(project, zoning, crs, [code_field] + [f.field for f in info.zoning_fields],
                   zone_legend, area=area)
    cuts = []
    for cut in info.cut_lines:
        layer = project.mapLayer(cut.layer_id)
        if layer is None:
            warnings.append(f"Parcel report: cut line layer {cut.layer_id} is missing.")
            continue
        cuts.append((cut.title or layer.name(), _Index(project, layer, crs, area=area)))
    restrictions = []
    for index, config in enumerate(info.restrictions):
        layer = project.mapLayer(config.layer_id)
        if layer is None:
            warnings.append(f"Parcel report: restriction layer {config.layer_id} is missing.")
            continue
        kind = _geometry_kind(layer)
        legend = _Legend(layer, legend_dir, config.title)
        grow = area.buffered(config.buffer_m + 1)
        data = _Index(project, layer, crs, [config.name_field] if config.name_field else [], legend,
                      buffer_m=config.buffer_m if kind in (0, 1) else 0.0, area=grow)
        restrictions.append((index, config, kind, legend, data))
    regulations = _regulations(project, info)

    records: Dict[str, dict] = {}
    request = QgsFeatureRequest()
    back = QgsCoordinateTransform(crs, parcels.crs(), project.transformContext())
    request.setFilterRect(back.transformBoundingBox(area))
    to_work = QgsCoordinateTransform(parcels.crs(), crs, project.transformContext())
    total = max(1, parcels.featureCount())
    duplicates = 0
    # The same expression as the tiles' q2vt_feature_key: identical keys.
    key_expr = QgsExpression(key_expression([info.key_field])[0])
    key_context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(parcels))
    key_expr.prepare(key_context)
    for done, feature in enumerate(parcels.getFeatures(request)):
        if done % 50 == 0:
            progress.check()
            progress.update(min(0.95, done / total), f"Parcel report: {done} parcels")
        key_context.setFeature(feature)
        key = key_expr.evaluate(key_context)
        if key is None or str(key) in ("", "NULL"):
            continue
        key = str(key)
        geometry = QgsGeometry(feature.geometry())
        if geometry.isEmpty():
            continue
        geometry.transform(to_work)
        if not geometry.isGeosValid():
            geometry = geometry.makeValid()
        if key in records:
            duplicates += 1
            continue
        records[key] = _parcel(key, feature, geometry, info, zones, zone_legend, cuts, restrictions,
                               to_wgs)
    if duplicates:
        warnings.append(f"Parcel report: {duplicates} parcels share an id with another parcel; only the "
                        "first one is reported. Choose a unique parcel id field.")
    zone_legend.close()
    for _, _, _, legend, _ in restrictions:
        legend.close()

    manifest = _write_shards(records, layer_logical_id(parcels.id()), out_dir)
    catalog = {
        "schemaVersion": 1, "layerId": layer_logical_id(parcels.id()),
        "title": parcels.name(), "keyField": info.key_field,
        "fields": [{"field": f.field, "title": f.alias or f.field} for f in info.fields],
        "zoneFields": [{"field": f.field, "title": f.alias or f.field} for f in info.zoning_fields],
        "cutLines": [title for title, _ in cuts],
        "restrictions": [{"i": index, "title": config.title or project.mapLayer(config.layer_id).name(),
                          "note": config.note, "reference": config.reference,
                          "distance": config.buffer_m if kind in (0, 1) else 0}
                         for index, config, kind, _, _ in restrictions],
        "regulationFields": [{"field": f.field, "title": f.alias or f.field} for f in info.regulation_fields],
        "regulations": regulations, "disclaimer": info.disclaimer,
        "units": {"area": "m2", "crs": crs.authid()},
    }
    with open(os.path.join(out_dir, "catalog.json"), "w", encoding="utf-8") as handle:
        json.dump(catalog, handle, ensure_ascii=False, separators=(",", ":"))
    swatches = {}
    for legend in [zone_legend] + [r[3] for r in restrictions]:
        for path in legend.files.values():
            swatches[path] = os.path.join(legend_dir, os.path.basename(path))
    progress.update(1.0, f"Parcel report: {len(records)} parcels", force=True)
    return ParcelReportResult(manifest, catalog, len(records), swatches, warnings)


def _area(geometry: QgsGeometry) -> float:
    return abs(geometry.area())


def _anchor(geometry: QgsGeometry, to_wgs) -> Optional[list]:
    point = geometry.pointOnSurface()
    if point is None or point.isEmpty():
        return None
    point = QgsGeometry(point)
    try:
        point.transform(to_wgs)
    except Exception:  # noqa: BLE001
        return None
    p = point.asPoint()
    return [round(p.x(), 7), round(p.y(), 7)]


def _parcel(key, feature, geometry, info, zones, zone_legend, cuts, restrictions, to_wgs) -> dict:
    total = _area(geometry)
    record = {"k": key, "a": round(total, 2),
              "f": {f.field: _json_value(feature[f.field]) for f in info.fields
                    if feature.fields().indexOf(f.field) >= 0}}
    # Parts: parcel x zone polygons, split by the cut lines.
    outer = QgsGeometry(geometry.constGet().boundary()).buffer(EDGE_TOLERANCE / 2, 2)
    parts = []
    for zone in zones.near(geometry):
        if not zone.geometry.intersects(geometry):
            continue
        piece = zone.geometry.intersection(geometry)
        if piece.isEmpty() or _area(piece) < info.min_area:
            continue
        lines = [line.geometry for _, index in cuts for line in index.near(piece)]
        for face in split_by_lines(piece, lines):
            size = _area(face)
            if size < info.min_area:
                continue
            inner = QgsGeometry(face.constGet().boundary()).difference(outer)
            bounded = []
            for title, index in cuts:
                for line in index.near(face):
                    near = inner.intersection(line.geometry.buffer(EDGE_TOLERANCE, 2))
                    if not near.isEmpty() and near.length() >= MIN_CUT_LENGTH:
                        bounded.append(title)
                        break
            swatch = zone.keys[0] if zone.keys else None
            parts.append({"c": zone.attributes.get(info.zoning_code_field),
                          "a": round(size, 2), "s": round(100.0 * size / total, 2) if total else 0,
                          "z": {f.field: zone.attributes.get(f.field) for f in info.zoning_fields},
                          "sw": swatch[1] if swatch else None, "zl": swatch[0] if swatch else None,
                          "b": bounded, "x": _anchor(face, to_wgs)})
    parts.sort(key=lambda p: -p["a"])
    for number, part in enumerate(parts, 1):
        part["n"] = number
    record["p"] = parts
    # Restrictions touching the parcel.
    hits = []
    engine = QgsGeometry.createGeometryEngine(geometry.constGet())
    engine.prepareGeometry()
    for index, config, kind, legend, data in restrictions:
        polygonal = kind == 2 or config.buffer_m > 0
        involved, names, keys = [], [], []
        for item in data.near(geometry):
            if not engine.intersects(item.geometry.constGet()):
                continue
            if polygonal:
                if _Index.engine(item).contains(geometry.constGet()):
                    overlap = geometry
                else:
                    overlap = item.geometry.intersection(geometry)
                if overlap.isEmpty() or _area(overlap) < info.min_area:
                    continue
                involved.append(overlap)
            elif kind == 1:
                inside = item.geometry.intersection(geometry)
                if inside.isEmpty() or inside.length() < MIN_CUT_LENGTH:
                    continue
            for legend_key in item.keys:
                if legend_key not in keys:
                    keys.append(legend_key)
            if config.name_field:
                name = item.attributes.get(config.name_field)
                if name not in (None, "") and str(name) not in names and len(names) < MAX_NAMES:
                    names.append(str(name))
            if not polygonal:
                involved.append(None)
        if not involved:
            continue
        hit = {"i": index, "n": names, "k": [[label or config.title or legend.title, path]
                                               for label, path in keys]}
        if polygonal:
            union = QgsGeometry.unaryUnion(involved).intersection(geometry)
            size = _area(union)
            share = 100.0 * size / total if total else 0
            if share < info.min_share:
                continue
            hit["a"], hit["s"] = round(size, 2), round(share, 2)
        hits.append(hit)
    record["r"] = hits
    return record


def _regulations(project, info: ParcelInfoConfig) -> Dict[str, dict]:
    """Zone code -> chosen fields of the optional regulation table."""
    if not info.regulation_layer_id:
        return {}
    layer = project.mapLayer(info.regulation_layer_id)
    if layer is None or layer.fields().indexOf(info.regulation_code_field) < 0:
        return {}
    out = {}
    for feature in layer.getFeatures():
        code = feature[info.regulation_code_field]
        if code in (None, "") or str(code) in out:
            continue
        out[str(code)] = {f.field: _json_value(feature[f.field]) for f in info.regulation_fields
                          if layer.fields().indexOf(f.field) >= 0}
    return out


def _write_shards(records: Dict[str, dict], layer_id: str, out_dir: str) -> dict:
    count = len(records)
    length = 0
    while count / (16 ** length) > TARGET_SHARD_RECORDS and length < 4:
        length += 1
    shards: Dict[str, list] = {}
    for key in sorted(records):
        shards.setdefault(shard_key(layer_id, key, length), []).append(records[key])
    manifest = {"schemaVersion": 1, "kind": "parcels", "layerId": layer_id, "prefixLength": length,
                "records": count, "hash": "fnv1a32(layerId + U+0000 + key)", "catalog": "catalog.json",
                "shards": []}
    for skey in sorted(shards):
        name = f"p-{skey or 'all'}.json"
        data = json.dumps(shards[skey], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with open(os.path.join(out_dir, name), "wb") as handle:
            handle.write(data)
        manifest["shards"].append({"key": skey, "path": name, "records": len(shards[skey]),
                                   "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, separators=(",", ":"))
    return manifest


def _report_key(project, profile: PublicationProfile, extent_3857: QgsRectangle) -> Optional[str]:
    """Cache key of a parcel report: its settings, the extent, the code that
    computes it and every layer it reads (source files and style, since the
    legend graphics come from the symbols). None: some layer is not file based."""
    from ..core import export_cache  # pylint: disable=import-outside-toplevel
    info = profile.parcel_info
    layer_ids = [info.parcel_layer_id, info.zoning_layer_id, info.regulation_layer_id] + \
        [cut.layer_id for cut in info.cut_lines] + [item.layer_id for item in info.restrictions]
    states = {}
    for layer_id in filter(None, layer_ids):
        layer = project.mapLayer(layer_id)
        state = export_cache.layer_state(layer) if layer is not None else {"missing": True}
        if state is None:
            return None
        states[layer_id] = state
    code = hashlib.sha256()
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("parcel_report.py", "feature_index.py", "identifiers.py"):
        with open(os.path.join(here, name), "rb") as handle:
            code.update(handle.read())
    return export_cache.make_key(
        "parcels", code.hexdigest(), profile.to_dict()["parcelInfo"], profile.locale,
        [extent_3857.xMinimum(), extent_3857.yMinimum(), extent_3857.xMaximum(), extent_3857.yMaximum()],
        states, project.ellipsoid(), project.crs().toWkt())


def cached_parcel_report(cache, project, profile: PublicationProfile, extent_3857: QgsRectangle,
                         out_dir: str, legend_dir: str,
                         progress: Optional[Progress] = None) -> ParcelReportResult:
    """build_parcel_report, reused from the export cache while its layers,
    settings and extent are unchanged."""
    key = _report_key(project, profile, extent_3857) if cache is not None else None
    if key:
        hit = cache.get_bundle("parcels", key)
        if hit is not None:
            meta, folder = hit
            os.makedirs(out_dir, exist_ok=True)
            os.makedirs(legend_dir, exist_ok=True)
            for name in meta["_files"]:
                kind, _, rest = name.partition("/")
                target = os.path.join(out_dir if kind == "parcels" else legend_dir, rest)
                shutil.copyfile(os.path.join(folder, *name.split("/")), target)
            if progress is not None:
                progress.info("Parcel report: unchanged, reused from the export cache")
            return ParcelReportResult(
                meta["manifest"], meta["catalog"], meta["records"],
                {path: os.path.join(legend_dir, name) for path, name in meta["swatches"].items()},
                list(meta["warnings"]))
    result = build_parcel_report(project, profile, extent_3857, out_dir, legend_dir, progress)
    if key:
        files = {f"parcels/{name}": os.path.join(out_dir, name) for name in os.listdir(out_dir)}
        files.update({f"legend/{os.path.basename(local)}": local for local in result.swatches.values()})
        cache.put_bundle("parcels", key, {
            "manifest": result.manifest, "catalog": result.catalog, "records": result.records,
            "warnings": result.warnings,
            "swatches": {path: os.path.basename(local) for path, local in result.swatches.items()},
        }, files)
    return result
