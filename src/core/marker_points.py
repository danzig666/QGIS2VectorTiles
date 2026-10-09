"""
marker_points.py

Interval marker positions computed directly, the way the QGIS expression of
``fidelity.materialize.interval_points_expression`` computes them, without
the expression engine: the expression walks the line from its start for
every marker (``line_interpolate_point`` / ``line_interpolate_angle``) and
was most of a big export's time. Every value is computed with the same
floating-point operations, in the same order, as the QGIS C++ functions
(QgsLineString::length / interpolatePoint, QgsGeometryUtils::
verticesAtDistance / lineAngle / averageAngle, the ``azimuth`` function), so
the positions and angles are the same numbers; tests compare them with the
expression on random lines.

Only plain lines are computed here: a feature this module cannot vouch for
(no geometry, multipart, curved, Z/M, zero length, non-finite numbers) is
left to the expression (``None``).
"""

import math
import struct
import sys
from typing import List, Optional, Sequence, Tuple

_EPS = 4 * sys.float_info.epsilon  # qgsDoubleNear's default
_TWO_PI = 2 * math.pi
_HALF_PI = math.pi / 2


def _near(a: float, b: float) -> bool:
    return abs(a - b) <= _EPS


def linestring_coords(wkb: bytes) -> Optional[Tuple[Sequence[float], Sequence[float]]]:
    """x and y of a 2D WKB LineString (either byte order); None otherwise."""
    if len(wkb) < 9:
        return None
    order = "<" if wkb[0] == 1 else ">"
    kind, count = struct.unpack_from(order + "II", wkb, 1)
    if kind != 2 or count < 2 or len(wkb) != 9 + 16 * count:
        return None
    values = struct.unpack_from(f"{order}{2 * count}d", wkb, 9)
    return values[0::2], values[1::2]


class Line:
    """A line's vertices and cumulative lengths (QgsLineString::length order)."""

    def __init__(self, xs: Sequence[float], ys: Sequence[float]):
        self.xs, self.ys = xs, ys
        cumulative = [0.0]
        total = 0.0
        for i in range(1, len(xs)):
            dx = xs[i] - xs[i - 1]
            dy = ys[i] - ys[i - 1]
            total += math.sqrt(dx * dx + dy * dy)
            cumulative.append(total)
        self.cumulative = cumulative
        self.length = total
        # QgsCurve::isClosed (2D)
        self.closed = _near(xs[0], xs[-1]) and _near(ys[0], ys[-1])

    def _first(self, distance: float, start: int) -> int:
        """First index k >= start with cumulative[k] > distance or near it
        (the walk's stop condition; monotone, so a bisection); len when none."""
        cumulative = self.cumulative
        low, high = start, len(cumulative)
        while low < high:
            middle = (low + high) // 2
            value = cumulative[middle]
            if value > distance or _near(value, distance):
                high = middle
            else:
                low = middle + 1
        return low

    def point(self, distance: float) -> Optional[Tuple[float, float]]:
        """QgsLineString::interpolatePoint."""
        if distance < 0:
            return None
        xs, ys = self.xs, self.ys
        if _near(distance, 0.0):
            return xs[0], ys[0]
        i = self._first(distance, 1)
        if i >= len(xs):
            return None
        traversed = self.cumulative[i - 1]
        x1, y1, x2, y2 = xs[i - 1], ys[i - 1], xs[i], ys[i]
        segment = math.sqrt((x2 - x1) * (x2 - x1) + (y2 - y1) * (y2 - y1))
        to_point = min(distance - traversed, segment)
        # QgsGeometryUtils::pointOnLineWithDistance
        dx, dy = x2 - x1, y2 - y1
        length = math.sqrt(dx * dx + dy * dy)
        if _near(length, 0.0):
            return x1, y1
        factor = to_point / length
        return x1 + dx * factor, y1 + dy * factor

    def angle(self, distance: float) -> float:
        """QgsGeometry::interpolateAngle in degrees (line_interpolate_angle)."""
        k = self._first(distance, 0)
        if k >= len(self.xs):
            return 0.0  # past the end: QGIS returns 0
        xs, ys = self.xs, self.ys
        if _near(self.cumulative[k], distance):  # exactly on a vertex
            last = len(xs) - 1
            if 0 < k < last:
                radians = _average_angle(_line_angle(xs[k - 1], ys[k - 1], xs[k], ys[k]),
                                         _line_angle(xs[k], ys[k], xs[k + 1], ys[k + 1]))
            elif k < last:
                radians = _line_angle(xs[k], ys[k], xs[k + 1], ys[k + 1])
            else:
                radians = _line_angle(xs[k - 1], ys[k - 1], xs[k], ys[k])
        else:
            radians = _line_angle(xs[k - 1], ys[k - 1], xs[k], ys[k])
        return radians * 180.0 / math.pi


def _normalized(angle: float) -> float:
    """QgsGeometryUtils::normalizedAngle"""
    if angle >= _TWO_PI or angle <= -_TWO_PI:
        angle = math.fmod(angle, _TWO_PI)
    if angle < 0.0:
        angle += _TWO_PI
    return angle


def _line_angle(x1: float, y1: float, x2: float, y2: float) -> float:
    """QgsGeometryUtils::lineAngle"""
    return _normalized(-math.atan2(y2 - y1, x2 - x1) + _HALF_PI)


def _average_angle(a1: float, a2: float) -> float:
    """QgsGeometryUtils::averageAngle"""
    a1, a2 = _normalized(a1), _normalized(a2)
    clockwise = a2 - a1 if a2 >= a1 else a2 + (_TWO_PI - a1)
    counter = _TWO_PI - clockwise
    if clockwise <= counter:
        result = a1 + clockwise / 2.0
    else:
        result = a1 - counter / 2.0
    return _normalized(result)


def _azimuth(x1: float, y1: float, x2: float, y2: float) -> float:
    """The expression function ``azimuth`` (radians; 0 for one point)."""
    if _near(x1, x2):
        return 0.0 if y1 < y2 else (math.pi if y1 > y2 else 0.0)
    if _near(y1, y2):
        return _HALF_PI if x1 < x2 else (math.pi + _HALF_PI if x1 > x2 else 0.0)
    if x1 < x2:
        if y1 < y2:
            return math.atan(abs(x1 - x2) / abs(y1 - y2))
        return math.atan(abs(y1 - y2) / abs(x1 - x2)) + _HALF_PI
    if y1 > y2:
        return math.atan(abs(x1 - x2) / abs(y1 - y2)) + math.pi
    return math.atan(abs(y1 - y2) / abs(x1 - x2)) + (math.pi + _HALF_PI)


def interval_positions(line: Line, interval: float, along: float,
                       average: float) -> Optional[List[Tuple[float, float, float]]]:
    """(x, y, angle in degrees) of the interval markers on ``line``, as the
    expression gives them; None when the expression would give no points (an
    offset outside the line) or this module cannot vouch for the numbers."""
    length = line.length
    if not (length > 0 and math.isfinite(length)) or not interval > 0:
        return None
    closed = line.closed
    if closed and along < 0:
        offset = length - math.fmod(-along, length)
    elif closed:
        offset = math.fmod(along, length)
    else:
        offset = along
    if offset > length or offset < 0 or not math.isfinite(offset):
        return None
    last = math.floor((length - offset) / interval + 1e-9)
    if last > 10_000_000:
        return None  # the expression's integer series; not worth emulating
    half = average / 2.0
    out = []
    for element in range(int(last) + 1):
        position = offset + element * interval
        if closed and element > 0 and abs(position - length) < 1e-6:
            continue
        distance = min(position, length)
        point = line.point(distance)
        if point is None:
            return None
        if average > 0:
            angle = _averaged_angle(line, distance, half)
        else:
            angle = line.angle(distance)
        out.append((point[0], point[1], angle))
    return out


def _averaged_angle(line: Line, distance: float, half: float) -> float:
    """coalesce(degrees(azimuth(at(d - half), at(d + half))), line_interpolate_angle(d))."""
    length = line.length

    def at(value):
        if line.closed:
            value = math.fmod(value + length, length)
        else:
            value = max(0, min(length, value))
        return line.point(value)
    start, end = at(distance - half), at(distance + half)
    if start is None or end is None:  # a NULL point: azimuth is NULL, coalesce goes on
        return line.angle(distance)
    return _azimuth(start[0], start[1], end[0], end[1]) * 180 / math.pi


def interval_points(source, recipe, export_crs: str, expression_context, expression: str):
    """(fields, batches): the interval markers of the lines of ``source`` (a
    QgsVectorLayer in ``export_crs``) as point features with the source
    fields and ``ANGLE_FIELD``, one per marker, in the order and with the
    values of the Processing chain they replace (geometrybyexpression with
    ``expression`` → multiparttosingleparts → angle = z → dropmzvalues).
    ``batches`` yields lists of QgsFeature. A feature this module does not
    compute is given to ``expression``."""
    # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtCore import QVariant
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                           QgsCoordinateTransformContext, QgsCsException, QgsExpression,
                           QgsExpressionContext, QgsFeature, QgsField, QgsFields, QgsGeometry,
                           QgsPoint, QgsProcessingException, QgsWkbTypes)
    from .fidelity.materialize import ANGLE_FIELD

    interval = float(recipe.param("interval"))
    along = float(recipe.param("along", 0.0))
    average = float(recipe.param("average", 0.0) or 0.0)
    crs = recipe.param("crs") or export_crs

    fields = QgsFields(source.fields())
    fields.append(QgsField(ANGLE_FIELD, QVariant.Double, "double", 0, 0))
    forward = backward = None
    if crs != export_crs:
        context = expression_context.variable("_project_transform_context")
        if not isinstance(context, QgsCoordinateTransformContext):
            context = QgsCoordinateTransformContext()
        forward = QgsCoordinateTransform(QgsCoordinateReferenceSystem.fromOgcWmsCrs(export_crs),
                                         QgsCoordinateReferenceSystem.fromOgcWmsCrs(crs), context)
        backward = QgsCoordinateTransform(QgsCoordinateReferenceSystem.fromOgcWmsCrs(crs),
                                          QgsCoordinateReferenceSystem.fromOgcWmsCrs(export_crs), context)
    linestring = QgsWkbTypes.LineString

    def markers(feature):
        """[(x, y, angle)] or None (the expression decides)."""
        geometry = feature.geometry()
        if geometry.isNull() or geometry.wkbType() != linestring:
            return None
        if forward is not None:
            geometry = QgsGeometry(geometry)
            try:
                geometry.transform(forward)
            except QgsCsException:
                return None
        coords = linestring_coords(bytes(geometry.asWkb()))
        if coords is None or not all(math.isfinite(v) for v in coords[0] + coords[1]):
            return None
        positions = interval_positions(Line(*coords), interval, along, average)
        if positions is None or backward is None:
            return positions
        moved = []
        for x, y, angle in positions:  # one by one, as transform() moves a multipoint
            point = QgsPoint(x, y, angle)
            try:
                point.transform(backward)
            except QgsCsException:
                return None
            moved.append((point.x(), point.y(), angle))
        return moved

    def batches():
        evaluator, context, batch = None, None, []
        for feature in source.getFeatures():
            attributes = feature.attributes()
            positions = markers(feature)
            if positions is not None:
                for x, y, angle in positions:
                    point = QgsFeature(fields)
                    point.setAttributes(attributes + [angle])
                    point.setGeometry(QgsGeometry(QgsPoint(x, y)))
                    batch.append(point)
            else:
                if evaluator is None:
                    evaluator = QgsExpression(expression)
                    context = QgsExpressionContext(expression_context)
                    evaluator.prepare(context)
                context.setFeature(feature)
                value = evaluator.evaluate(context)
                if evaluator.hasEvalError():
                    raise QgsProcessingException(f"Evaluation error: {evaluator.evalErrorString()}")
                if not isinstance(value, QgsGeometry) or value.isNull():
                    empty = QgsFeature(fields)
                    empty.setAttributes(attributes + [QVariant()])  # as z(NULL) gives it
                    batch.append(empty)
                else:
                    shape = value.constGet()
                    parts = [shape.geometryN(i) for i in range(shape.numGeometries())] \
                        if value.isMultipart() else [shape]
                    for part in parts:
                        point = QgsFeature(fields)
                        point.setAttributes(attributes + [part.z()])
                        point.setGeometry(QgsGeometry(QgsPoint(part.x(), part.y())))
                        batch.append(point)
            if len(batch) >= 5000:
                yield batch
                batch = []
        if batch:
            yield batch
    return fields, batches()


def interval_points_layer(source, recipe, export_crs: str, expression_context, expression: str):
    """interval_points as a memory point layer."""
    # pylint: disable=import-outside-toplevel
    from qgis.core import QgsMemoryProviderUtils, QgsWkbTypes
    fields, batches = interval_points(source, recipe, export_crs, expression_context, expression)
    out = QgsMemoryProviderUtils.createMemoryLayer("points", fields, QgsWkbTypes.Point, source.crs())
    for batch in batches:
        out.dataProvider().addFeatures(batch)
    return out
