"""Colour band polygons of gradient and shapeburst fills.

One polygon per feature and band (see ``materialize.color_bands_recipe``),
computed with QgsGeometry. The same construction as QGIS draws:

* gradients in the feature's bounding box space (Qt's
  ``QGradient::ObjectBoundingMode``: lines, circles and angles are stretched
  with the box), built in the unit box and scaled back;
* shapeburst bands as inset polygons, from the edge inwards.
"""

import math
from typing import Iterable, List, Optional, Tuple

from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,
                       QgsPointXY, QgsProject)
from qgis.PyQt.QtGui import QTransform

from .materialize import Recipe, _band_interval

_CIRCLE_SEGMENTS = 36  # per quarter circle


def _union(parts: Iterable[QgsGeometry]) -> Optional[QgsGeometry]:
    parts = [p for p in parts if p is not None and not p.isEmpty()]
    if not parts:
        return None
    return QgsGeometry.unaryUnion(parts) if len(parts) > 1 else parts[0]


class BandBuilder:
    """Band polygons of one color_bands recipe, per feature geometry in the
    export CRS."""

    # Web Mercator metres per CSS pixel at zoom 0 (512 px world).
    METRES_PER_PX_Z0 = 40075016.68557849 / 512

    def __init__(self, recipe: Recipe, export_crs: str, detail_zoom: Optional[float] = None):
        """``detail_zoom``: bands narrower than a pixel at this zoom are merged
        per feature (export CRS in Web Mercator metres)."""
        self.bands: Tuple[Recipe, ...] = tuple(recipe.param("bands"))
        self.detail_zoom = detail_zoom
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
        shapes = []
        context = _Context(local)
        for index, band in self._merged(geometry):
            if band.kind == "shapeburst_band":
                shape = _shapeburst(local, band, context)
            else:
                shape = _gradient(local, band, context)
            if shape is None or shape.isEmpty():
                continue
            if self._from_crs is not None:
                shape.transform(self._from_crs)
            if not shape.isEmpty():
                shapes.append((index, shape))
        return shapes


    def _merged(self, geometry: QgsGeometry):
        """[(colour index, band recipe)]: neighbouring bands merged so none is
        narrower than about a pixel at ``detail_zoom`` (the feature's
        bounding box diagonal bounds every band's extent); a merged band takes
        the colour of its middle."""
        count = len(self.bands)
        if self.detail_zoom is None or count < 2:
            return list(enumerate(self.bands))
        box = geometry.boundingBox()
        pixels = math.hypot(box.width(), box.height()) / (
            self.METRES_PER_PX_Z0 / 2 ** self.detail_zoom)
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

    def __init__(self, geometry: QgsGeometry):
        box = geometry.boundingBox()
        self.ox, self.oy = box.xMinimum(), box.yMaximum()
        self.ow, self.oh = max(box.width(), 1e-9), max(box.height(), 1e-9)
        self.unit = QgsGeometry(geometry)
        # Unit box, y up, top-left corner at the origin (y in [-1, 0]).
        self.unit.transform(QTransform(1 / self.ow, 0, 0, 1 / self.oh,
                                       -self.ox / self.ow, -self.oy / self.oh))
        self.back = QTransform(self.ow, 0, 0, self.oh, self.ox, self.oy)
        self.reach = {}


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
    reach = math.sqrt(2) + 1  # beyond every corner of the unit box
    start, end = _band_interval(band, bands, overlap)
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
                out.append((k + 1 - top, k + 1 - a))
            else:
                out.append((k + a, k + top))
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
    if band == 0:
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
        return None
    return geometry.intersection(shape.buffer(-band / bands * reach, 8))
