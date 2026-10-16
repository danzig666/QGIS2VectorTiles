"""
tile_budget.py

Where the tiles of an export can end without losing detail.

A vector tile holds coordinates on a 4096-step grid: tiles of zoom z place
a vertex within ``WORLD / 2**z / 4096`` metres, and the web map zooms in past
the last tile zoom (overzoom) without new tiles. Coarse data over a large
area - the country borders of a continent, earthquakes a kilometre apart -
has all its detail in tiles of zoom 10 or so; tiles down to zoom 16 for it
were tens of millions of filled squares (hours of tile writing; the export
seemed to hang near its end).

The tiles end earlier only when both hold:
- the export would make more than ``budget`` tiles (a town's parcels, its
  buildings, any small or medium export, stay as asked), and
- the data has no finer detail than the zoom chosen: every layer's shortest
  segments (polygons, lines) or point spacing span ``DETAIL_STEPS`` grid
  steps of it (vertices move by at most 1/32 of the shortest segment).

Pure functions and a small QGIS sampler; the zoom is that of the tile
archive (512-pixel MapLibre zoom = the tile pyramid's level).
"""

import math
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple

WORLD = 40075016.68557849   # Web Mercator width, metres
GRID = 4096                 # MVT extent: grid steps per tile side
DETAIL_STEPS = 16           # grid steps the shortest detail must span
BUDGET = 200_000            # tiles an export makes before it is capped
SAMPLE = 20_000             # features read per layer for the estimate


@dataclass
class LayerDetail:
    """What a layer adds to the tiles: its area (m², Mercator, inside the
    extent), length (m), point count, features, and its finest detail (m:
    the 1st percentile of segment lengths or point spacings; None unknown)."""
    name: str = ""
    area: float = 0.0
    length: float = 0.0
    points: int = 0
    features: int = 0
    detail: Optional[float] = None
    samples: List[float] = field(default_factory=list, repr=False)


def tile_size(zoom: int) -> float:
    return WORLD / (2 ** zoom)


def tiles_at(layers: Iterable[LayerDetail], zoom: int, extent_tiles: int) -> float:
    """Tiles the layers make at ``zoom``: the squares their areas cover, the
    tiles their lines and outlines cross, one per point (at most the
    extent's ``extent_tiles``)."""
    size = tile_size(zoom)
    total = 0.0
    for layer in layers:
        total += layer.area / (size * size) + layer.length / size + layer.points + layer.features
    return min(total, float(extent_tiles))


def detail_zoom(layers: Iterable[LayerDetail], min_zoom: int, max_zoom: int) -> int:
    """The lowest zoom whose grid resolves every layer's finest detail."""
    zoom = min_zoom
    for layer in layers:
        if (layer.detail is None or layer.detail <= 0) and not (layer.features or layer.points):
            continue  # nothing in the extent: no say in the zoom
        if layer.detail is None or layer.detail <= 0:
            return max_zoom  # unknown: keep every zoom
        step = layer.detail / DETAIL_STEPS
        zoom = max(zoom, math.ceil(math.log2(WORLD / GRID / step)))
    return min(max_zoom, zoom)


def capped_max_zoom(layers: List[LayerDetail], min_zoom: int, max_zoom: int,
                    extent_tiles_at, budget: int = BUDGET) -> Tuple[int, float]:
    """(last tile zoom, tiles up to it): ``max_zoom`` unless the tiles up to
    it exceed ``budget``; then the highest zoom within the budget, but never
    below the data's detail zoom. ``extent_tiles_at(z)``: tiles of the
    export extent at z."""
    total, within = 0.0, None
    for zoom in range(min_zoom, max_zoom + 1):
        total += tiles_at(layers, zoom, extent_tiles_at(zoom))
        if total <= budget:
            within = zoom
    if total <= budget:
        return max_zoom, total
    cap = max(detail_zoom(layers, min_zoom, max_zoom), within if within is not None else min_zoom)
    cap = min(cap, max_zoom)
    kept = sum(tiles_at(layers, z, extent_tiles_at(z)) for z in range(min_zoom, cap + 1))
    return cap, kept


def percentile(values: List[float], fraction: float) -> Optional[float]:
    values = sorted(v for v in values if v > 0)
    if not values:
        return None
    return values[min(len(values) - 1, int(fraction * (len(values) - 1)))]


def measure_layer(layer, extent_3857, transform_context, sample: int = SAMPLE) -> Optional[LayerDetail]:
    """A vector layer's LayerDetail inside ``extent_3857`` (QgsRectangle,
    EPSG:3857), from up to ``sample`` features (scaled to the layer's
    feature count). None for a layer that cannot be read."""
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,  # pylint: disable=import-outside-toplevel
                           QgsFeatureRequest, QgsGeometry, QgsSpatialIndex, QgsWkbTypes)
    try:
        web = QgsCoordinateReferenceSystem("EPSG:3857")
        to_web = QgsCoordinateTransform(layer.crs(), web, transform_context)
        request = QgsFeatureRequest().setNoAttributes()
        request.setDestinationCrs(web, transform_context)
        request.setFilterRect(extent_3857)
        request.setLimit(sample)
        del to_web
    except Exception:  # pylint: disable=broad-except
        return None
    clip = QgsGeometry.fromRect(extent_3857)
    detail = LayerDetail(name=layer.name())
    kind = QgsWkbTypes.geometryType(layer.wkbType())
    points, segments = [], []
    read = 0
    for feature in layer.getFeatures(request):
        geometry = feature.geometry()
        if geometry is None or geometry.isEmpty():
            continue
        read += 1
        if kind == QgsWkbTypes.PointGeometry:
            for vertex in geometry.vertices():
                points.append((vertex.x(), vertex.y()))
            continue
        inside = geometry if extent_3857.contains(geometry.boundingBox()) else geometry.intersection(clip)
        if inside is None or inside.isEmpty():
            continue
        detail.features += 1
        if kind == QgsWkbTypes.PolygonGeometry:
            detail.area += inside.area()
            detail.length += inside.length()  # the outline crosses tiles too
        else:
            detail.length += inside.length()
        previous = None
        for part in geometry.constParts():
            previous = None
            for vertex in part.vertices():
                if previous is not None:
                    segments.append(math.hypot(vertex.x() - previous[0], vertex.y() - previous[1]))
                previous = (vertex.x(), vertex.y())
    if read == 0:
        return detail
    if kind == QgsWkbTypes.PointGeometry:
        detail.points = len(points)
        detail.detail = _point_spacing(points, QgsSpatialIndex)
    else:
        detail.detail = percentile(segments, 0.01)
    # A sample of a larger layer: scale its amounts to the features in the
    # extent (not the whole layer: a national layer in a town's extent).
    total = read
    if read >= sample:
        try:
            within = QgsFeatureRequest().setNoAttributes().setFlags(QgsFeatureRequest.NoGeometry)
            within.setDestinationCrs(request.destinationCrs(), transform_context)
            within.setFilterRect(extent_3857)
            total = sum(1 for _ in layer.getFeatures(within))
        except Exception:  # pylint: disable=broad-except
            total = layer.featureCount()
    if read >= sample and total > read:
        factor = total / read
        detail.area *= factor
        detail.length *= factor
        detail.points = int(detail.points * factor)
        detail.features = int(detail.features * factor)
    return detail


def _point_spacing(points, spatial_index_type, limit: int = 5000) -> Optional[float]:
    """1st percentile of the distance from a point to its nearest distinct
    neighbour (of up to ``limit`` points)."""
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY  # pylint: disable=import-outside-toplevel
    if len(points) < 2:
        return None
    step = max(1, len(points) // limit)
    chosen = points[::step]
    index = spatial_index_type()
    for number, (x, y) in enumerate(points):
        feature = QgsFeature(number)
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
        index.addFeature(feature)
    distances = []
    for x, y in chosen:
        for neighbour in index.nearestNeighbor(QgsPointXY(x, y), 4):
            nx, ny = points[neighbour]
            distance = math.hypot(nx - x, ny - y)
            if distance > 0:
                distances.append(distance)
                break
    return percentile(distances, 0.01)
