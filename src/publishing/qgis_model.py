"""
QGIS side of a publication: logical model, identity, membership, records
and legend swatches. Runs on QGIS's main thread (reads the live project).

* Logical model: groups from the layer tree, layers from the profile's
  included layers, rules from the *original* renderers' legend items, and
  components (style layers) mapped through the provenance captured by the
  flattener — never reverse-engineered from generated dataset names.
* Membership: the published features of a layer are those inside the export
  extent that at least one exported rule matches (the union of the
  flattened rules' filters, before cartographic multiplication).
* Records (private JSON lines): one per published feature with its key,
  display label, search terms, inside anchor, WGS84 bounds, approved popup
  attributes and filter values. They feed search/feature-lookup indexes and
  filter domains; they never hold geometry.
"""

import datetime as _dt
import json
import math
import os
from typing import Dict, List, Optional, Tuple

from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsExpression,
                       QgsExpressionContext, QgsExpressionContextUtils, QgsFeatureRequest,
                       QgsLayerTreeGroup, QgsLayerTreeLayer, QgsProject, QgsRectangle,
                       QgsRuleBasedRenderer, QgsSymbolLayerUtils, QgsVectorLayer, QgsWkbTypes)
from qgis.PyQt.QtCore import QSize

from .errors import PublishingError
from .identifiers import EXPORT_SCOPED, key_expression, validate_keys
from .models import PublicationProfile
from .profile import validate as validate_profile
from .provenance import (group_logical_id, layer_logical_id, owning_style_name,
                         rule_logical_id)

WEB_MERCATOR = "EPSG:3857"
GEOMETRY = {0: "point", 1: "line", 2: "polygon"}
MAX_SAFE_INT = 2 ** 53 - 1
FILTER_VALUES_LIMIT = 500


def _enum(value) -> int:
    return int(getattr(value, "value", value))


def json_value(value):
    """Attribute value as JSON (dates ISO, big integers as strings, NULL None)."""
    if value is None:
        return None
    try:
        from qgis.PyQt.QtCore import QDate, QDateTime, QTime, QVariant  # pylint: disable=import-outside-toplevel
        if isinstance(value, QVariant):
            return None if value.isNull() else json_value(value.value())
        if isinstance(value, QDateTime):
            return value.toString("yyyy-MM-ddTHH:mm:ss") if value.isValid() else None
        if isinstance(value, QDate):
            return value.toString("yyyy-MM-dd") if value.isValid() else None
        if isinstance(value, QTime):
            return value.toString("HH:mm:ss") if value.isValid() else None
    except ImportError:
        pass
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) <= MAX_SAFE_INT else str(value)
    if isinstance(value, float):
        return None if math.isnan(value) or math.isinf(value) else value
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return None
    return str(value)


# --- identity / tile fields ---------------------------------------------------------

def feature_keys(profile: PublicationProfile) -> Tuple[Dict[str, str], Dict[str, str]]:
    """({layer id: key expression}, {layer id: identity scope})."""
    expressions, scopes = {}, {}
    for layer in profile.layers:
        if layer.included:
            expressions[layer.layer_id], scopes[layer.layer_id] = key_expression(layer.key_fields)
    return expressions, scopes


def tile_fields(profile: PublicationProfile) -> Dict[str, List[str]]:
    """Approved filter fields written to the tiles of their layer."""
    return {layer.layer_id: [f.field for f in layer.filter_fields]
            for layer in profile.layers if layer.included and layer.filter_fields}


def check_profile_against_project(profile: PublicationProfile, project: QgsProject) -> List[str]:
    """Profile problems that need the project (missing layers/fields)."""
    problems = list(validate_profile(profile))
    for config in profile.layers:
        if not config.included:
            continue
        layer = project.mapLayer(config.layer_id)
        if layer is None or not isinstance(layer, QgsVectorLayer):
            problems.append(f"Layer {config.layer_id} is not a vector layer of this project.")
            continue
        names = {field.name() for field in layer.fields()}
        used = ([p.field for p in config.popup_fields] + config.search_fields + config.key_fields
                + [f.field for f in config.filter_fields])
        for name in used:
            if name not in names:
                problems.append(f'Layer "{layer.name()}": field "{name}" does not exist.')
        if config.display_expression:
            expression = QgsExpression(config.display_expression)
            if expression.hasParserError():
                problems.append(f'Layer "{layer.name()}": display expression: '
                                f"{expression.parserErrorString()}")
    return problems


# --- logical model --------------------------------------------------------------------

def _tree_paths(project: QgsProject) -> Dict[str, Tuple[Tuple[str, ...], int]]:
    """{layer id: (group path, order)} in layer-tree order."""
    paths, order = {}, [0]

    def walk(group: QgsLayerTreeGroup, path):
        for child in group.children():
            if isinstance(child, QgsLayerTreeGroup):
                walk(child, path + (child.name(),))
            elif isinstance(child, QgsLayerTreeLayer) and child.layerId():
                paths[child.layerId()] = (path, order[0])
                order[0] += 1
    walk(project.layerTreeRoot(), ())
    return paths


def _zoom_of_scale(scale: float) -> Optional[float]:
    if not scale:
        return None
    from ..core.fidelity import zoom as fidelity_zoom  # pylint: disable=import-outside-toplevel
    try:
        return round(float(fidelity_zoom.scale_to_zoom(scale)), 3)
    except (AttributeError, ValueError, TypeError):
        return None


def logical_model(project: QgsProject, profile: PublicationProfile, rules, style: dict,
                  swatches: Optional[Dict[str, str]] = None) -> dict:
    """groups / layers / rules / components of the manifest."""
    swatches = swatches or {}
    paths = _tree_paths(project)
    included = [c for c in profile.layers if c.included and project.mapLayer(c.layer_id)]
    included.sort(key=lambda c: paths.get(c.layer_id, ((), 1 << 30))[1])

    groups: Dict[Tuple[str, ...], dict] = {}
    for config in included:
        path = paths.get(config.layer_id, ((), 0))[0]
        for depth in range(1, len(path) + 1):
            sub = path[:depth]
            if sub not in groups:
                groups[sub] = {"id": group_logical_id(sub), "title": sub[-1],
                               "parentId": group_logical_id(sub[:-1]) if depth > 1 else None,
                               "order": len(groups), "expanded": True}

    by_style: Dict[str, object] = {}
    for rule in rules:
        by_style.setdefault(rule.rule.description(), rule)
    names = list(by_style)

    layers, rule_entries, components = [], [], []
    rule_ids_seen = set()
    component_index: Dict[str, dict] = {}
    for config in included:
        layer = project.mapLayer(config.layer_id)
        lid = layer_logical_id(layer.id())
        path = paths.get(layer.id(), ((), 0))[0]
        entry = {
            "id": lid, "groupId": group_logical_id(path) if path else None,
            "title": config.title or layer.name(), "order": paths.get(layer.id(), ((), 0))[1],
            "geometry": GEOMETRY.get(_enum(layer.geometryType()), "unknown"),
            "initialVisibility": bool(config.initially_visible),
            "opacity": float(config.opacity), "legend": bool(config.legend),
            "featureKeyProperty": "q2vt_feature_key",
            "identityScope": key_expression(config.key_fields)[1],
            "popupFields": [{"field": p.field, "title": p.alias or p.field, "type": p.type}
                            for p in config.popup_fields],
            "filterFields": [], "searchable": bool(config.search_fields),
            "deepLinks": bool(config.deep_links), "ruleIds": [], "componentIds": [],
            "swatch": swatches.get(lid),
        }
        if layer.hasScaleBasedVisibility():
            low, high = _zoom_of_scale(layer.minimumScale()), _zoom_of_scale(layer.maximumScale())
            if low is not None:
                entry["minZoom"] = low
            if high is not None:
                entry["maxZoom"] = high
        renderer = layer.renderer()
        try:
            items = renderer.legendSymbolItems() if renderer is not None else []
        except RuntimeError:
            items = []
        for order, item in enumerate(items):
            if item.symbol() is None and not item.ruleKey():
                continue
            rid = rule_logical_id(layer.id(), item.ruleKey() or "single")
            if rid in rule_ids_seen:
                continue
            rule_ids_seen.add(rid)
            parent = item.parentRuleKey()
            is_rule_based = isinstance(renderer, QgsRuleBasedRenderer)
            parent_id = rule_logical_id(layer.id(), parent) \
                if parent and is_rule_based and parent != renderer.rootRule().ruleKey() else None
            rule_entry = {"id": rid, "layerId": lid, "parentId": parent_id,
                          "title": item.label() or entry["title"], "order": order,
                          "swatch": swatches.get(rid), "componentIds": []}
            low, high = _zoom_of_scale(item.scaleMinDenom()), _zoom_of_scale(item.scaleMaxDenom())
            if item.scaleMinDenom() > 0 and low is not None:
                rule_entry["minZoom"] = low  # minimum scale denominator -> max zoom
            if item.scaleMaxDenom() > 0 and high is not None:
                rule_entry["maxZoom"] = high
            if "minZoom" in rule_entry and "maxZoom" in rule_entry:  # denominators -> zooms
                rule_entry["minZoom"], rule_entry["maxZoom"] = rule_entry["maxZoom"], rule_entry["minZoom"]
            elif "minZoom" in rule_entry:
                rule_entry["maxZoom"] = rule_entry.pop("minZoom")
            elif "maxZoom" in rule_entry:
                rule_entry["minZoom"] = rule_entry.pop("maxZoom")
            rule_entries.append(rule_entry)
            entry["ruleIds"].append(rid)
        layers.append(entry)

    layer_by_qgis = {c.layer_id: layer_logical_id(c.layer_id) for c in included}
    rule_set = {r["id"] for r in rule_entries}
    for style_layer in style.get("layers", []):
        owner = owning_style_name(style_layer.get("id", ""), names)
        if owner is None:
            continue
        flat = by_style[owner]
        prov = getattr(flat, "provenance", None)
        qgis_id = prov.layer_id if prov is not None else flat.layer.id()
        lid = layer_by_qgis.get(qgis_id)
        if lid is None:
            continue
        cid = f"c-{owner}"
        component = component_index.get(cid)
        if component is None:
            if prov is not None and prov.kind == "labeling":
                role = "callout" if prov.component == "leader" else "label"
            elif prov is not None and prov.component == "leader":
                role = "callout"
            else:
                role = "geometry" if getattr(flat, "recipe", None) is None else "decoration"
            rule_ids = []
            if prov is not None and prov.kind == "symbology" and prov.rule_key:
                rid = rule_logical_id(qgis_id, prov.rule_key)
                if rid in rule_set:
                    rule_ids.append(rid)
            component = {"id": cid, "role": role, "layerId": lid, "ruleIds": rule_ids,
                         "styleLayerIds": [], "sourceId": style_layer.get("source", ""),
                         "sourceLayer": style_layer.get("source-layer"),
                         "dependsOnSourceLayers": [], "interactive": role in ("geometry", "decoration")}
            component_index[cid] = component
            components.append(component)
            next(l for l in layers if l["id"] == lid)["componentIds"].append(cid)
            for rid in rule_ids:
                next(r for r in rule_entries if r["id"] == rid)["componentIds"].append(cid)
        component["styleLayerIds"].append(style_layer["id"])
        helper = (style_layer.get("metadata") or {}).get("q2vt:visible-polygons")
        if helper and helper not in component["dependsOnSourceLayers"]:
            component["dependsOnSourceLayers"].append(helper)
    return {"groups": list(groups.values()), "layers": layers, "rules": rule_entries,
            "components": components}


# --- membership and records ------------------------------------------------------------

def membership_expression(rules, layer_id: str) -> str:
    """OR of the exported rules' filters of one layer ("" = every feature)."""
    filters = []
    for rule in rules:
        if rule.layer.id() != layer_id:
            continue
        expression = rule.rule.filterExpression() or ""
        if not expression.strip() or expression.strip().upper() == "ELSE":
            return ""
        filters.append(f"({expression})")
    return " OR ".join(sorted(set(filters)))


def _anchor_and_bounds(geometry, to_wgs) -> Tuple[Optional[list], Optional[list]]:
    if geometry is None or geometry.isEmpty():
        return None, None
    try:
        geometry = QgsGeometryCopy(geometry)
        geometry.transform(to_wgs)
    except Exception:  # noqa: BLE001 - outside the transform's area
        return None, None
    box = geometry.boundingBox()
    kind = _enum(geometry.type())
    if kind == 2:
        point = geometry.pointOnSurface()
    elif kind == 1:
        point = geometry.interpolate(geometry.length() / 2) if geometry.length() > 0 else geometry.centroid()
    else:
        point = geometry.centroid()
    anchor = None
    if point is not None and not point.isEmpty():
        p = point.asPoint()
        anchor = [round(p.x(), 7), round(p.y(), 7)]
    return anchor, [round(box.xMinimum(), 7), round(box.yMinimum(), 7),
                    round(box.xMaximum(), 7), round(box.yMaximum(), 7)]


def QgsGeometryCopy(geometry):  # noqa: N802 - mirrors the QGIS copy constructor
    from qgis.core import QgsGeometry  # pylint: disable=import-outside-toplevel
    return QgsGeometry(geometry)


def suggested_zoom(bounds: Optional[list], max_zoom: float = 18.0) -> float:
    if not bounds:
        return max_zoom
    width = max(bounds[2] - bounds[0], 1e-9)
    height = max(bounds[3] - bounds[1], 1e-9)
    lat = math.radians((bounds[1] + bounds[3]) / 2)
    span = max(width, height / max(0.2, math.cos(lat)))
    zoom = math.log2(360.0 / span) + 0.5  # roughly fits a 512 px view with margin
    return round(max(0.0, min(max_zoom, zoom)), 2)


class RecordsResult:
    def __init__(self):
        self.path = ""
        self.counts: Dict[str, int] = {}
        self.filter_domains: Dict[str, Dict[str, dict]] = {}
        self.problems: List[str] = []
        self.scopes: Dict[str, str] = {}


def collect_records(project: QgsProject, profile: PublicationProfile, rules, extent_3857: QgsRectangle,
                    out_path: str, progress=None, max_zoom: float = 18.0) -> RecordsResult:
    """Write the private record file (JSON lines) of every published feature."""
    result = RecordsResult()
    result.path = out_path
    wgs = QgsCoordinateReferenceSystem("EPSG:4326")
    web = QgsCoordinateReferenceSystem(WEB_MERCATOR)
    with open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        for config in profile.layers:
            if not config.included:
                continue
            layer = project.mapLayer(config.layer_id)
            if layer is None:
                continue
            lid = layer_logical_id(layer.id())
            key_expr, scope = key_expression(config.key_fields)
            result.scopes[lid] = scope
            context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
            key = QgsExpression(key_expr)
            key.prepare(context)
            display = QgsExpression(config.display_expression) if config.display_expression else None
            if display is not None:
                display.prepare(context)
            to_layer = QgsCoordinateTransform(web, layer.crs(), project.transformContext())
            to_wgs = QgsCoordinateTransform(layer.crs(), wgs, project.transformContext())
            request = QgsFeatureRequest()
            try:
                request.setFilterRect(to_layer.transformBoundingBox(extent_3857))
            except Exception:  # noqa: BLE001
                pass
            membership = membership_expression(rules, layer.id())
            if membership:
                request.setFilterExpression(membership)
                request.setExpressionContext(context)
            keys, count = [], 0
            domains: Dict[str, dict] = {f.field: {"kind": f.kind, "values": {}, "min": None, "max": None,
                                                  "nulls": 0} for f in config.filter_fields}
            popup = [p.field for p in config.popup_fields]
            for feature in layer.getFeatures(request):
                context.setFeature(feature)
                value = key.evaluate(context)
                value = None if value is None or (hasattr(value, "isNull") and value.isNull()) \
                    else str(value)
                if scope != EXPORT_SCOPED:
                    keys.append(value)
                label = None
                if display is not None:
                    label = json_value(display.evaluate(context))
                attributes = {name: json_value(feature[name]) for name in popup}
                terms = []
                for name in config.search_fields:
                    term = json_value(feature[name])
                    if term not in (None, ""):
                        terms.append(str(term))
                if label in (None, ""):
                    label = terms[0] if terms else (value or "")
                for name, domain in domains.items():
                    raw = json_value(feature[name])
                    if raw is None:
                        domain["nulls"] += 1
                    elif domain["kind"] == "range" and isinstance(raw, (int, float)):
                        domain["min"] = raw if domain["min"] is None else min(domain["min"], raw)
                        domain["max"] = raw if domain["max"] is None else max(domain["max"], raw)
                    elif domain["kind"] == "values" and len(domain["values"]) <= FILTER_VALUES_LIMIT:
                        domain["values"][str(raw)] = domain["values"].get(str(raw), 0) + 1
                anchor, bounds = _anchor_and_bounds(feature.geometry(), to_wgs)
                record = {"layerId": lid, "featureKey": value, "fid": feature.id(),
                          "label": str(label), "terms": terms, "anchor": anchor, "bounds": bounds,
                          "suggestedZoom": suggested_zoom(bounds, max_zoom), "attributes": attributes,
                          "filters": {name: json_value(feature[name]) for name in domains}}
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1
                if progress is not None and count % 2000 == 0:
                    progress.check()
            result.counts[lid] = count
            result.filter_domains[lid] = domains
            if scope != EXPORT_SCOPED:
                issues = validate_keys(keys)
                if issues["null"]:
                    result.problems.append(f'Layer "{layer.name()}": {issues["null"][0]} feature(s) '
                                           f'have no key ({", ".join(config.key_fields)} is NULL).')
                if issues["duplicate"]:
                    result.problems.append(f'Layer "{layer.name()}": key not unique, e.g. '
                                           f'{", ".join(issues["duplicate"][:3])}.')
    return result


def raise_identity_problems(result: RecordsResult) -> None:
    if result.problems:
        raise PublishingError("Q2VT_PUB_IDENTITY", " ".join(result.problems[:5]))


# --- legend swatches ---------------------------------------------------------------------

def render_swatches(project: QgsProject, profile: PublicationProfile, out_dir: str,
                    size: int = 32) -> Dict[str, str]:
    """PNG swatch of every legend item (QGIS renders the original symbol):
    {rule or layer logical id: "legend/<id>.png"}. UI assets, not map tiles."""
    os.makedirs(out_dir, exist_ok=True)
    swatches = {}
    for config in profile.layers:
        if not config.included:
            continue
        layer = project.mapLayer(config.layer_id)
        renderer = layer.renderer() if layer is not None else None
        if renderer is None:
            continue
        try:
            items = renderer.legendSymbolItems()
        except RuntimeError:
            continue
        first = None
        for item in items:
            symbol = item.symbol()
            if symbol is None:
                continue
            rid = rule_logical_id(layer.id(), item.ruleKey() or "single")
            name = f"{rid}.png"
            pixmap = QgsSymbolLayerUtils.symbolPreviewPixmap(symbol, QSize(size, size), 2)
            if pixmap.isNull() or not pixmap.save(os.path.join(out_dir, name), "PNG"):
                continue
            swatches[rid] = f"legend/{name}"
            first = first or swatches[rid]
        if first:
            swatches[layer_logical_id(layer.id())] = first
    return swatches


def _wkb_name(layer) -> str:
    return QgsWkbTypes.displayString(layer.wkbType())
