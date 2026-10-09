"""
zoom_load.py

How heavy the published layers are when the web map is zoomed out, and
from which zoom their features, and separately their labels, are worth
drawing (Publish window, Map tab: *Zoomed-out load…*).

A layer drawn at every scale is in the tiles of every zoom; zoomed out, one
tile holds all of it (a city's parcels: several MB the browser must load and
draw, into specks smaller than a pixel). Labels weigh even more: every label
of the tile is laid out, and only the few that do not collide can show.

The estimate reads a random sample of each layer's features inside the
export extent (sizes, vertex counts, label texts, where they are) and
counts its features there; per zoom it estimates the busiest tile (features
and bytes) and how many labels could show in it. Suggestions:

* features from the first zoom where the typical feature is at least
  ``VISIBLE_PX`` pixels across (points: where they no longer cover the tile
  as a solid mass), when the layer's busiest tile is heavy below it;
* labels from the first zoom where at least half the labels of the busiest
  tile can be placed (polygon labels also: where the typical polygon is about
  as wide as its label), when fewer than half of them fit where they start
  now.
"""

import math
import random
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsExpression,
                       QgsExpressionContext, QgsExpressionContextUtils, QgsFeatureRequest,
                       QgsRectangle, QgsUnitTypes, QgsVectorLayer)

from ..core.fidelity import zoom as fidelity_zoom
from ..core.fidelity.units import MapUnitContext

EARTH = 40075016.68557849          # Web Mercator x-extent
TILE_PX = 512                      # MapLibre draws a tile of zoom z at 512 px at map zoom z
SAMPLE = 2000                      # features read per layer
VISIBLE_PX = 2.0                   # a feature narrower than this is a speck
HEAVY_BYTES = 400 * 1024           # a layer's part of one tile that slows the map down
HEAVY_FEATURES = 15000             # features of one layer in one tile
POINT_CELL_PX = 6                  # points denser than one per 6×6 px are a solid mass


@dataclass
class LayerLoad:
    """One layer's zoomed-out load and the suggested limits (zooms)."""

    layer_id: str
    name: str
    kind: str                       # "point" | "line" | "polygon"
    features: int
    shown_from: int                 # first zoom the layer is shown at now
    tile_features: Dict[int, int] = field(default_factory=dict)   # busiest tile, per zoom
    tile_bytes: Dict[int, int] = field(default_factory=dict)
    feature_px: float = 0.0         # typical feature size in px at zoom 0 (× 2**z)
    draws_features: bool = True     # False: labels only (no symbol)
    suggested_from: Optional[int] = None
    labels: bool = False
    labels_from: int = 0            # first zoom its labels are shown at now
    label_capacity: float = 0.0     # labels that fit in one tile
    label_share: float = 1.0        # features that have a label text
    label_bytes: Dict[int, int] = field(default_factory=dict)
    label_width_px: float = 0.0     # a typical label's width
    suggested_labels_from: Optional[int] = None

    def heavy(self, zoom: int) -> bool:
        return self.tile_bytes.get(zoom, 0) >= HEAVY_BYTES or self.tile_features.get(zoom, 0) >= HEAVY_FEATURES

    def labels_shown(self, zoom: int) -> float:
        """Share of the busiest tile's labels that can be placed."""
        count = self.tile_features.get(zoom, 0) * self.label_share
        return 1.0 if count <= 0 else min(1.0, self.label_capacity / count)


def pixel_size(zoom: float) -> float:
    """Web Mercator units per screen pixel at a MapLibre zoom."""
    return EARTH / (TILE_PX * 2.0 ** zoom)


def zoom_scale_factor(project, extent_3857: QgsRectangle) -> float:
    """QGIS map scale of zoom 0 for this project (as the export converts):
    scale(z) = factor / 2**z."""
    crs = project.crs()
    to_wgs = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:3857"),
                                    QgsCoordinateReferenceSystem("EPSG:4326"), project)
    try:
        latitude = to_wgs.transform(extent_3857.center()).y()
    except Exception:  # noqa: BLE001 - a broken extent: the equator
        latitude = 0.0
    units = "degrees" if crs.isGeographic() else getattr(crs.mapUnits(), "name", "meters")
    context = MapUnitContext.for_project(crs.authid() in ("EPSG:3857", "EPSG:900913"), str(units),
                                         latitude)
    return fidelity_zoom.WEB_MERCATOR_TOP_SCALE / context.mercator_per_map_unit


def scale_for_zoom(factor: float, zoom: int) -> float:
    """The web-only "hidden when zoomed out beyond" scale that shows a layer
    from ``zoom`` on (rounded down to a round number QGIS-style)."""
    exact = factor / (2.0 ** zoom)
    step = 10 ** max(0, int(math.log10(exact)) - 1)
    return float(math.floor(exact / step) * step)


def zoom_for_scale(factor: float, scale: float) -> float:
    return math.log2(factor / scale) if scale and scale > 0 else 0.0


def _kind(layer: QgsVectorLayer) -> str:
    kind = layer.geometryType()
    return {0: "point", 1: "line"}.get(int(getattr(kind, "value", kind)), "polygon")


def _label_settings(layer: QgsVectorLayer):
    """The layer's (first) label settings, or None."""
    if not layer.labelsEnabled() or layer.labeling() is None:
        return None
    labeling = layer.labeling()
    if labeling.type() == "simple":
        return labeling.settings()
    root = labeling.rootRule() if hasattr(labeling, "rootRule") else None
    for rule in (root.descendants() if root is not None else []):
        if rule.settings() is not None and rule.active():
            return rule.settings()
    return None


def _font_px(settings) -> float:
    text = settings.format()
    size, unit = float(text.size()), QgsUnitTypes.encodeUnit(text.sizeUnit())
    per_unit = {"Point": 96.0 / 72.0, "MM": 96.0 / 25.4, "Pixel": 1.0, "Inch": 96.0}.get(unit)
    return size * per_unit if per_unit else 12.0  # map units and others: a typical size


def _visible_from(layer: QgsVectorLayer, config, factor: float, min_zoom: int,
                  labels: bool = False) -> int:
    """First zoom the layer (or its labels) is shown at now."""
    from .qgis_model import layer_scale_range  # pylint: disable=import-outside-toplevel
    low, _high = layer_scale_range(layer, config)
    if labels and getattr(config, "labels_min_scale", 0):
        low = min(low, config.labels_min_scale) if low else config.labels_min_scale
    return max(min_zoom, int(math.floor(zoom_for_scale(factor, low) + 1e-6))) if low else min_zoom


def analyse_layer(layer: QgsVectorLayer, config, extent_3857: QgsRectangle, zooms: Tuple[int, int],
                  factor: float, project, seed: int = 7) -> Optional[LayerLoad]:
    """The zoomed-out load of one vector layer inside the extent, or None
    when no feature of it is there."""
    min_zoom, max_zoom = zooms
    to_layer = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:3857"), layer.crs(), project)
    to_web = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:3857"), project)
    try:
        rect = to_layer.transformBoundingBox(extent_3857)
    except Exception:  # noqa: BLE001 - outside the layer CRS's area
        return None
    request = QgsFeatureRequest().setFilterRect(rect).setNoAttributes() \
        .setFlags(QgsFeatureRequest.Flag.NoGeometry)
    ids = [feature.id() for feature in layer.getFeatures(request)]
    if not ids:
        return None
    rng = random.Random(seed)
    sample_ids = ids if len(ids) <= SAMPLE else rng.sample(ids, SAMPLE)
    settings = _label_settings(layer)
    label_expression = None
    context = QgsExpressionContext()
    context.appendScopes(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    if settings is not None:
        text = settings.getLabelExpression().expression() if settings.isExpression else \
            QgsExpression.quotedColumnRef(settings.fieldName)
        label_expression = QgsExpression(text)
        label_expression.prepare(context)
    sizes, vertices, centres, label_lengths = [], [], [], []
    for feature in layer.getFeatures(QgsFeatureRequest().setFilterFids(sample_ids)):
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            continue
        try:
            box = to_web.transformBoundingBox(geometry.boundingBox())
        except Exception:  # noqa: BLE001
            continue
        sizes.append(max(box.width(), box.height()))
        vertices.append(max(1, geometry.constGet().nCoordinates()))
        centres.append((box.center().x(), box.center().y()))
        if label_expression is not None:
            context.setFeature(feature)
            value = label_expression.evaluate(context)
            label_lengths.append(len(str(value)) if value not in (None, "") and str(value) != "NULL" else 0)
    if not centres:
        return None
    kind = _kind(layer)
    renderer = layer.renderer()
    load = LayerLoad(layer.id(), layer.name(), kind, len(ids),
                     _visible_from(layer, config, factor, min_zoom),
                     draws_features=renderer is not None and renderer.type() != "nullSymbol")
    scale_up = len(ids) / len(centres)
    typical = statistics.median(sizes) if kind != "point" else 0.0
    load.feature_px = typical / pixel_size(0)
    mean_vertices = statistics.mean(vertices)
    if label_expression is not None:
        load.labels = True
        load.labels_from = _visible_from(layer, config, factor, min_zoom, labels=True)
        if settings.scaleVisibility and settings.minimumScale > 0:  # the labels' own QGIS limit
            load.labels_from = max(load.labels_from,
                                   int(math.floor(zoom_for_scale(factor, settings.minimumScale) + 1e-6)))
        texts = [n for n in label_lengths if n]
        load.label_share = len(texts) / len(label_lengths) if label_lengths else 0.0
        chars = statistics.mean(texts) if texts else 0.0
        font = _font_px(settings)
        width, height = 0.6 * font * chars + 4.0, 1.2 * font + 2.0
        load.label_capacity = (TILE_PX * TILE_PX) / max(1.0, width * height * 1.5)
        load.label_width_px = width
    for zoom in range(min_zoom, max_zoom + 1):
        cell = EARTH / 2.0 ** zoom
        counts: Dict[Tuple[int, int], int] = {}
        for x, y in centres:
            key = (int(math.floor(x / cell)), int(math.floor(y / cell)))
            counts[key] = counts.get(key, 0) + 1
        busiest = max(counts.values())
        # Few sampled features per tile: the sample says little; the mean
        # density of the occupied tiles is steadier.
        estimate = busiest * scale_up if busiest >= 8 else len(ids) / len(counts)
        tile_features = int(min(len(ids), max(1.0, estimate)))
        px = load.feature_px * 2 ** zoom
        if kind == "point":
            per_feature = 12.0
        else:
            per_feature = 12.0 + 3.0 * min(mean_vertices, max(2.0, 2.0 * px))
        load.tile_features[zoom] = tile_features
        load.tile_bytes[zoom] = int(tile_features * per_feature) if load.draws_features else 0
        if load.labels:
            label_bytes = 16.0 + 8.0 + (per_feature if kind == "polygon" else 0.0)  # point, text, polygon
            load.label_bytes[zoom] = int(tile_features * load.label_share * label_bytes)
    _suggest(load, max_zoom)
    return load


def _feature_visible(load: LayerLoad, zoom: int) -> bool:
    if load.kind == "point":
        cells = (TILE_PX / POINT_CELL_PX) ** 2
        return load.tile_features.get(zoom, 0) <= cells
    return load.feature_px * 2 ** zoom >= VISIBLE_PX


def _suggest(load: LayerLoad, max_zoom: int) -> None:
    """Fill the suggestions (None: leave as it is)."""
    start = load.shown_from
    if load.draws_features and load.heavy(start) and not _feature_visible(load, start):
        for zoom in range(start + 1, max_zoom + 1):
            if _feature_visible(load, zoom):
                load.suggested_from = zoom
                break
    if not load.labels:
        return
    begin = max(load.labels_from, load.suggested_from or start)
    if load.labels_shown(begin) >= 0.5:
        return  # most of them fit: a few labels are no load, whatever their size
    width = load.label_width_px

    def placeable(zoom: int) -> bool:
        if load.labels_shown(zoom) < 0.5:
            return False
        # A polygon's label inside it: the polygon about as wide as the label.
        return load.kind != "polygon" or load.feature_px * 2 ** zoom >= 0.6 * width
    if placeable(begin):
        return
    for zoom in range(begin + 1, max_zoom + 1):
        if placeable(zoom):
            load.suggested_labels_from = zoom
            return


def analyse(project, profile, extent_3857: QgsRectangle) -> Tuple[List[LayerLoad], float]:
    """(loads of the published vector layers, busiest first; the zoom-0
    scale factor of this project)."""
    factor = zoom_scale_factor(project, extent_3857)
    zooms = (int(profile.view.min_zoom), int(profile.view.max_zoom))
    loads = []
    for config in profile.layers:
        layer = project.mapLayer(config.layer_id)
        if not config.included or not isinstance(layer, QgsVectorLayer) or not layer.isSpatial():
            continue
        load = analyse_layer(layer, config, extent_3857, zooms, factor, project)
        if load is not None:
            loads.append(load)
    loads.sort(key=lambda l: -(l.tile_bytes.get(l.shown_from, 0) + l.label_bytes.get(l.labels_from, 0)))
    return loads, factor
