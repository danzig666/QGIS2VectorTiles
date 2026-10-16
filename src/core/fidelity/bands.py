"""Colour band polygons of gradient and shapeburst fills.

One polygon per feature and band (see ``materialize.color_bands_recipe``),
computed with QgsGeometry. The same construction as QGIS draws:

* gradients in the feature's bounding box space (Qt's
  ``QGradient::ObjectBoundingMode``: lines, circles and angles are stretched
  with the box), built in the unit box and scaled back;
* shapeburst bands as inset polygons, from the edge inwards.
"""

import bisect
import math
from typing import Iterable, List, Optional, Tuple

from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,
                       QgsPointXY, QgsProject, QgsRectangle, QgsWkbTypes)
from qgis.PyQt.QtGui import QTransform

from .materialize import Recipe, _band_interval

_CIRCLE_SEGMENTS = 36  # per quarter circle


def _union(parts: Iterable[QgsGeometry]) -> Optional[QgsGeometry]:
    parts = [p for p in parts if p is not None and not p.isEmpty()]
    if not parts:
        return None
    return QgsGeometry.unaryUnion(parts) if len(parts) > 1 else parts[0]


def _polygons(shape: QgsGeometry) -> List[QgsGeometry]:
    """The polygons of ``shape``, one per part: an intersection can also
    give points and lines (a wedge touching the feature at its apex)."""
    out = []
    for part in shape.asGeometryCollection():
        if part.isEmpty() or QgsWkbTypes.geometryType(part.wkbType()) != QgsWkbTypes.PolygonGeometry:
            continue
        out.extend(QgsGeometry.fromPolygonXY(p) for p in
                   (part.asMultiPolygon() if part.isMultipart() else [part.asPolygon()]) if p)
    return out


def _without_holes(shape: QgsGeometry, min_width: float = math.inf) -> List[QgsGeometry]:
    """The polygons of ``shape``, those narrower than ``min_width`` (about
    2 x area / perimeter, a ring's width) cut at each hole. Bands that do not
    overlap are rings narrower than a tile unit at low zooms: there a ring's
    hole collapses onto its outline and the tiles drop it, so the ring would
    cover the whole inside; a part without holes vanishes."""
    parts, todo = [], _polygons(shape)
    while todo:
        part = todo.pop()
        rings = part.asPolygon()
        if len(rings) < 2 or 2 * part.area() >= min_width * part.constGet().perimeter():
            parts.append(part)
            continue
        # Cut across the first hole, through a point inside it.
        x = QgsGeometry.fromPolygonXY([rings[1]]).pointOnSurface().asPoint().x()
        box = part.boundingBox()
        for x0, x1 in ((box.xMinimum() - 1, x), (x, box.xMaximum() + 1)):
            todo.extend(_polygons(part.intersection(QgsGeometry.fromRect(
                QgsRectangle(x0, box.yMinimum() - 1, x1, box.yMaximum() + 1)))))
    return parts


class BandBuilder:
    """Band polygons of one color_bands recipe, per feature geometry in the
    export CRS."""

    # Web Mercator metres per CSS pixel at zoom 0 (512 px world).
    METRES_PER_PX_Z0 = 40075016.68557849 / 512

    def __init__(self, recipe: Recipe, export_crs: str, detail_zoom: Optional[float] = None,
                 coarse_zoom: Optional[float] = None):
        """``detail_zoom``: bands narrower than a pixel at this zoom are merged
        per feature (export CRS in Web Mercator metres); ``coarse_zoom``: the
        lowest zoom whose tiles carry the bands, whose coordinate grid band
        edges keep clear of the feature's vertices."""
        self.bands: Tuple[Recipe, ...] = tuple(recipe.param("bands"))
        self.detail_zoom = detail_zoom
        self.coarse_zoom = coarse_zoom
        crs = (self.bands[0].param("crs") if self.bands else None) or export_crs
        self._to_crs = self._from_crs = None
        if crs != export_crs:
            source = QgsCoordinateReferenceSystem(export_crs)
            target = QgsCoordinateReferenceSystem(crs)
            context = QgsProject.instance().transformContext()
            self._to_crs = QgsCoordinateTransform(source, target, context)
            self._from_crs = QgsCoordinateTransform(target, source, context)

    def build(self, geometry: QgsGeometry) -> List[Tuple[int, QgsGeometry]]:
        """[(band index, polygon in the export CRS)] for one feature."""
        if geometry is None or geometry.isEmpty():
            return []
        local = QgsGeometry(geometry)
        if self._to_crs is not None:
            local.transform(self._to_crs)
        local = self._simplified(local)
        shapes = []
        clearance = 0.0
        # Two tile units (1/4096 of a tile) at the coarsest zoom (export CRS).
        two_units = 2 * self.METRES_PER_PX_Z0 * 512 / 4096 / 2 ** (self.coarse_zoom or 0.0)
        if self.coarse_zoom is not None:
            # In layer units.
            clearance = two_units
            if self._to_crs is not None and self._to_crs.destinationCrs().isGeographic():
                clearance /= 111320.0
        context = _Context(local, clearance)
        for index, band in self._merged(geometry, local):
            if band.kind == "shapeburst_band":
                shape = _shapeburst(local, band, context)
            else:
                shape = _gradient(local, band, context)
            if shape is None or shape.isEmpty():
                continue
            if self._from_crs is not None:
                shape.transform(self._from_crs)
            if shape.isEmpty():
                continue
            if band.param("overlap") is False:
                # Rings over two tile units wide keep their holes. One
                # feature per band (fewer for the tiles to cut and write).
                parts = _without_holes(shape, two_units)
                if parts:
                    shapes.append((index, QgsGeometry.collectGeometry(parts) if len(parts) > 1 else parts[0]))
            else:
                shapes.append((index, shape))
        return shapes


    # Vertices from which a feature is simplified before its bands are cut.
    DENSE_VERTICES = 20000

    def _simplified(self, local: QgsGeometry) -> QgsGeometry:
        """A very dense outline (a smoothed, randomized geometry generator:
        a million vertices) simplified to half a pixel at ``detail_zoom``:
        every band is an inward buffer of it, minutes each at full density,
        and the gradient shows no difference."""
        if self.detail_zoom is None:
            return local
        try:
            count = local.constGet().nCoordinates()
        except AttributeError:
            return local
        if count <= self.DENSE_VERTICES:
            return local
        tolerance = self.METRES_PER_PX_Z0 / 2 ** self.detail_zoom / 2
        if self._to_crs is not None and self._to_crs.destinationCrs().isGeographic():
            tolerance /= 111320.0
        simple = local.simplify(tolerance)
        return simple if simple is not None and not simple.isEmpty() and simple.isGeosValid() else local

    def _merged(self, geometry: QgsGeometry, local: QgsGeometry):
        """[(colour index, band recipe)]: neighbouring bands merged so none is
        narrower than about a pixel at ``detail_zoom`` (the feature's
        bounding box diagonal bounds every band's extent, a shapeburst's
        distance or half the box's short side its bands'); a merged band
        takes the colour of its middle."""
        count = len(self.bands)
        if self.detail_zoom is None or count < 2:
            return list(enumerate(self.bands))
        box, own = geometry.boundingBox(), local.boundingBox()
        extent = math.hypot(box.width(), box.height())
        first = self.bands[0]
        if first.kind == "shapeburst_band":
            reach = min(own.width(), own.height()) / 2
            if not first.param("whole") and first.param("distance") > 0:
                reach = min(reach, first.param("distance"))
            # Layer units to export units.
            extent *= reach / max(math.hypot(own.width(), own.height()), 1e-12)
        pixels = extent / (self.METRES_PER_PX_Z0 / 2 ** self.detail_zoom)
        step = max(1, math.ceil(count / max(pixels, 1.0)))
        if step == 1:
            return list(enumerate(self.bands))
        merged = []
        for group in range(math.ceil(count / step)):
            recipe = self.bands[group * step]
            params = dict(recipe.params)
            params["band"], params["bands"] = group, count / step
            colour = min(count - 1, group * step + step // 2)
            merged.append((colour, Recipe(recipe.kind, recipe.placements, tuple(params.items()))))
        return merged


class _Context:
    """Per-feature values shared by its bands (unit box, shapeburst reach)."""

    def __init__(self, geometry: QgsGeometry, clearance: float = 0.0):
        # Distance (layer units) band edges keep from the feature's vertices.
        self.clearance = clearance
        self._edges = {}
        box = geometry.boundingBox()
        self.ox, self.oy = box.xMinimum(), box.yMaximum()
        self.ow, self.oh = max(box.width(), 1e-9), max(box.height(), 1e-9)
        self.unit = QgsGeometry(geometry)
        # Unit box, y up, top-left corner at the origin (y in [-1, 0]).
        self.unit.transform(QTransform(1 / self.ow, 0, 0, 1 / self.oh,
                                       -self.ox / self.ow, -self.oy / self.oh))
        self.back = QTransform(self.ow, 0, 0, self.oh, self.ox, self.oy)
        self.reach = {}

    def free_edges(self, kind: int, origin, direction, limit: float = math.inf):
        """``t -> t'``: a band edge moved off the feature's vertices.

        A band edge along a polygon edge (e.g. a hole's side parallel to a
        linear gradient's bands) touches it; once the tiles quantize the
        coordinates such a ring falls apart and the hole is filled in the
        browser. Edges within ``clearance`` of a vertex move to that
        distance, at most ``limit`` (in t, a fraction of a colour level): a
        feature only a few pixels wide at the lowest zoom would otherwise
        lose its outer bands at every zoom."""
        if not self.clearance:
            return lambda t: t
        key = (kind, origin, direction)
        if key not in self._edges:
            ox, oy = origin
            dx, dy = direction
            length2 = max(dx * dx + dy * dy, 1e-18)
            if kind == 1:  # radial: t = |p - p1| / |d|
                def t_of(x, y):
                    return math.hypot(x - ox, y - oy) / math.sqrt(length2)
                step = max(1 / self.ow, 1 / self.oh) / math.sqrt(length2)
            else:  # linear: t = (p - p1) . d / |d|^2
                def t_of(x, y):
                    return ((x - ox) * dx + (y - oy) * dy) / length2
                step = (abs(dx) / self.ow + abs(dy) / self.oh) / length2
            ts = sorted({round(t_of(v.x(), v.y()), 12) for v in self.unit.vertices()})
            self._edges[key] = (ts, self.clearance * step)
        ts, eps = self._edges[key]
        eps = min(eps, limit)
        if not ts or eps <= 0:
            return lambda t: t

        def free(t):
            index = bisect.bisect_left(ts, t)
            for near in ts[max(0, index - 1):index + 1]:
                if abs(t - near) < eps:
                    return near + eps if t >= near else near - eps
            return t
        return free


def _gradient(geometry: QgsGeometry, recipe: Recipe, ctx: _Context) -> Optional[QgsGeometry]:
    kind, spread = recipe.param("type"), recipe.param("spread")
    band, bands, overlap = recipe.param("band"), recipe.param("bands"), recipe.param("overlap")
    g = ctx.unit

    def point(fraction, centroid):
        if centroid:
            c = g.centroid().asPoint()
            return c.x(), c.y()
        return fraction[0], -fraction[1]

    p1x, p1y = point(recipe.param("p1"), recipe.param("c1"))
    p2x, p2y = point(recipe.param("p2"), recipe.param("c2"))
    dx, dy = p2x - p1x, p2y - p1y
    length = max(math.hypot(dx, dy), 1e-9)
    # Band edges keep clear of the feature's vertices (see _Context.free_edges).
    free = ctx.free_edges(kind, (p1x, p1y), (dx, dy), 0.25 / bands) if kind in (0, 1) else (lambda t: t)
    reach = math.sqrt(2) + 1  # beyond every corner of the unit box
    start, end = _band_interval(band, bands, overlap)
    start = None if start is None else free(start)
    end = None if end is None else free(end)
    # Grown towards the band drawn next (band 0 also backwards, under the
    # last band of the previous period / turn), which covers the growth.
    grow = (recipe.param("extend") or 0.0) / bands
    a = band / bands - (grow if band == 0 else 0.0)
    b = (band + 1) / bands + (grow if band < bands - 1 else 0.0)

    def periods(count_from, count_to):
        """[(ta, tb)] of this band in each spread period."""
        out = []
        # Nested (overlap): a band reaches to the end of its period, under
        # the bands drawn after it.
        top = 1.0 if overlap else b
        for k in range(count_from, count_to + 1):
            if spread == 1 and k % 2:  # reflect: odd periods run backwards
                out.append((free(k + 1 - top), free(k + 1 - a)))
            else:
                out.append((free(k + a), free(k + top)))
        return out

    if kind == 1:  # radial: t = distance from p1 / |p2 - p1|
        def disc(t):
            return QgsGeometry.fromPointXY(QgsPointXY(p1x, p1y)).buffer(t * length, _CIRCLE_SEGMENTS)
        if spread == 0:
            if end is not None:
                end += grow
            shape = g if end is None else g.intersection(disc(end))
            if start is not None:
                shape = shape.difference(disc(start))
        else:
            rings = [disc(tb).difference(disc(ta)) if ta > 0 else disc(tb)
                     for ta, tb in periods(0, math.ceil(reach / length) + 1)]
            shape = g.intersection(_union(rings))
    elif kind == 2:  # conical: angle counter-clockwise from p1 -> p2, once around
        base = math.atan2(dy, dx)
        a0, a1 = base + 2 * math.pi * a, base + 2 * math.pi * b
        ring = [QgsPointXY(p1x, p1y)]
        steps = max(2, int(math.ceil(abs(a1 - a0) / (math.pi / 32))))
        for i in range(steps + 1):
            angle = a0 + (a1 - a0) * i / steps
            ring.append(QgsPointXY(p1x + 2 * reach * math.cos(angle), p1y + 2 * reach * math.sin(angle)))
        ring.append(QgsPointXY(p1x, p1y))
        shape = g.intersection(QgsGeometry.fromPolygonXY([ring]))
    else:  # linear: t = projection on p1 -> p2 / |p2 - p1|^2
        nx, ny = -dy / length * reach, dx / length * reach
        big = reach / length + 2  # beyond every corner, in t units

        def strip(ta, tb):
            corners = [(ta, -1), (tb, -1), (tb, 1), (ta, 1), (ta, -1)]
            return QgsGeometry.fromPolygonXY([[QgsPointXY(p1x + dx * t + nx * side, p1y + dy * t + ny * side)
                                               for t, side in corners]])
        if spread == 0:
            shape = g.intersection(strip(-big if start is None else start,
                                         big if end is None else end + grow))
        else:
            strips = [strip(ta, tb) for ta, tb in periods(math.floor(-big), math.ceil(big))]
            shape = g.intersection(_union(strips))
    if shape is None or shape.isEmpty():
        return None
    shape = QgsGeometry(shape)
    shape.transform(ctx.back)
    return shape


def _shapeburst(geometry: QgsGeometry, recipe: Recipe, ctx: _Context) -> Optional[QgsGeometry]:
    band, bands = recipe.param("band"), recipe.param("bands")
    # Nested (overlap): every inset reaches to the middle, under the bands
    # drawn after it. Translucent bands are rings that do not overlap.
    overlap = recipe.param("overlap") is not False
    if band == 0 and overlap:
        return QgsGeometry(geometry)
    ignore_rings = recipe.param("ignore_rings")
    key = ("shape", ignore_rings)
    if key not in ctx.reach:
        shape = geometry
        if ignore_rings:
            parts = []
            for polygon in (geometry.asMultiPolygon() if geometry.isMultipart() else [geometry.asPolygon()]):
                if polygon:
                    parts.append(QgsGeometry.fromPolygonXY([polygon[0]]))
            shape = _union(parts) or geometry
        if recipe.param("whole"):
            _pole, reach = shape.poleOfInaccessibility(max(ctx.ow, ctx.oh) / 1000)
        else:
            reach = recipe.param("distance")
        ctx.reach[key] = (shape, reach)
    shape, reach = ctx.reach[key]
    if not reach or reach <= 0:
        return QgsGeometry(geometry) if band == 0 else None

    def inset(index):
        if (key, index, bands) not in ctx.reach:
            ctx.reach[(key, index, bands)] = shape.buffer(-index / bands * reach, 8)
        return ctx.reach[(key, index, bands)]
    outer = geometry if band == 0 else geometry.intersection(inset(band))
    if not overlap and (band + 1) / bands < 1 - 1e-9:
        # The last band keeps the whole inside (QGIS: colour 2 beyond the distance).
        outer = outer.difference(inset(band + 1))
    return outer
