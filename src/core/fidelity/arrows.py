"""
QGIS arrow symbol layer polygons (``QgsArrowSymbolLayer``, QGIS 3.34).

A port of ``straightArrow``, ``curvedArrow`` and the vertex pairing of
``renderPolyline``: the arrow is a polygon built in painter pixels (y down),
filled with the arrow's fill symbol. The curved arrows use Qt's own
``QPainterPath`` arcs, flattened as QGIS flattens them.
"""

import math
from typing import List, Sequence, Tuple

Point = Tuple[float, float]

HEAD_SINGLE, HEAD_REVERSED, HEAD_DOUBLE = 0, 1, 2
ARROW_PLAIN, ARROW_LEFT_HALF, ARROW_RIGHT_HALF = 0, 1, 2


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _add(a: Point, b: Point, k: float = 1.0) -> Point:
    return (a[0] + b[0] * k, a[1] + b[1] * k)


def straight_arrow(po: Point, pd: Point, start_width: float, width: float, head_width: float,
                   head_height: float, head_type: int, arrow_type: int, offset: float) -> List[Point]:
    """``straightArrow``: ``head_width`` is the head length along the line,
    ``head_height`` the half width of the head."""
    length = _dist(po, pd)
    if length <= 0:
        return []
    if head_type == HEAD_SINGLE and length < head_width:
        po = _add(pd, ((pd[0] - po[0]) / length, (pd[1] - po[1]) / length), -head_width)
        length = head_width
    elif head_type == HEAD_REVERSED and length < head_width:
        pd = _add(po, ((pd[0] - po[0]) / length, (pd[1] - po[1]) / length), head_width)
        length = head_width
    elif head_type == HEAD_DOUBLE and length < 2 * head_width:
        v = ((pd[0] - po[0]) / length * head_width, (pd[1] - po[1]) / length * head_width)
        mid = ((po[0] + pd[0]) / 2.0, (po[1] + pd[1]) / 2.0)
        po, pd = _add(mid, v, -1.0), _add(mid, v)
        length = 2 * head_width
    body = length - head_width
    unit = ((pd[0] - po[0]) / length, (pd[1] - po[1]) / length)
    perp = (-unit[1], unit[0])
    po, pd = _add(po, perp, offset), _add(pd, perp, offset)

    def at(along: float, across: float) -> Point:
        return (po[0] + unit[0] * along + perp[0] * across, po[1] + unit[1] * along + perp[1] * across)
    right = arrow_type in (ARROW_PLAIN, ARROW_RIGHT_HALF)
    left = arrow_type in (ARROW_PLAIN, ARROW_LEFT_HALF)
    poly: List[Point] = []
    if head_type == HEAD_DOUBLE:
        poly.append(po)
        if right:
            poly += [at(head_width, head_height), at(head_width, width * 0.5),
                     at(body, width * 0.5), at(body, head_height)]
        poly.append(pd)
        if left:
            poly += [at(body, -head_height), at(body, -width * 0.5),
                     at(head_width, -width * 0.5), at(head_width, -head_height)]
    elif head_type == HEAD_SINGLE:
        if right:
            poly += [at(0, start_width * 0.5), at(body, width * 0.5), at(body, head_height)]
        else:
            poly.append(po)
        poly.append(pd)
        if left:
            poly += [at(body, -head_height), at(body, -width * 0.5), at(0, -start_width * 0.5)]
        else:
            poly.append(po)
    elif head_type == HEAD_REVERSED:
        poly.append(po)
        if right:
            poly += [at(head_width, head_height), at(head_width, width * 0.5),
                     _add(pd, perp, start_width * 0.5)]
        else:
            poly.append(pd)
        if left:
            poly += [_add(pd, perp, -start_width * 0.5), at(head_width, -width * 0.5),
                     at(head_width, -head_height)]
        else:
            poly.append(pd)
    if poly:
        poly.append(poly[0])
    return poly


def _clamp_angle(a: float) -> float:
    if a > 2 * math.pi:
        return a - 2 * math.pi
    if a < 0.0:
        return a + 2 * math.pi
    return a


def points_to_circle(a: Point, b: Point, c: Point):
    """(centre, radius) of the circle through three points, None when they
    are (nearly) aligned (QGIS's empirical threshold of 0.001)."""
    ab = (b[0] - a[0], b[1] - a[1])
    bc = (c[0] - b[0], c[1] - b[1])
    ab2 = ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
    bc2 = ((b[0] + c[0]) / 2.0, (b[1] + c[1]) / 2.0)
    if abs(ab[0] * bc[1] - ab[1] * bc[0]) < 0.001:
        return None
    if ab[1] == 0:
        cx = ab2[0]
        cy = bc2[1] - (cx - bc2[0]) * bc[0] / bc[1]
    elif bc[1] == 0:
        cx = bc2[0]
        cy = ab2[1] - (cx - ab2[0]) * ab[0] / ab[1]
    else:
        cx = (bc2[1] - ab2[1] + bc[0] * bc2[0] / bc[1] - ab[0] * ab2[0] / ab[1]) / \
            (bc[0] / bc[1] - ab[0] / ab[1])
        cy = bc2[1] - (cx - bc2[0]) * bc[0] / bc[1]
    return (cx, cy), math.hypot(a[0] - cx, a[1] - cy)


def _circle_point(center: Point, radius: float, angle: float) -> Point:
    # Y is oriented downward
    return (math.cos(-angle) * radius + center[0], math.sin(-angle) * radius + center[1])


def _qpoint(p: Point):
    from qgis.PyQt.QtCore import QPointF  # pylint: disable=import-outside-toplevel
    return QPointF(p[0], p[1])


def _path_arc_to(path, center: Point, radius: float, angle_o: float, angle_d: float,
                 direction: int) -> None:
    from qgis.PyQt.QtCore import QRectF  # pylint: disable=import-outside-toplevel
    rect = QRectF(center[0] - radius, center[1] - radius, 2 * radius, 2 * radius)
    deg = 180.0 / math.pi
    if direction == 1:
        if angle_o < angle_d:
            path.arcTo(rect, angle_o * deg, (angle_d - angle_o) * deg)
        else:
            path.arcTo(rect, angle_o * deg, 360.0 - (angle_o - angle_d) * deg)
    else:
        if angle_o < angle_d:
            path.arcTo(rect, angle_o * deg, -(360.0 - (angle_d - angle_o) * deg))
        else:
            path.arcTo(rect, angle_o * deg, (angle_d - angle_o) * deg)


def _spiral_arc_to(path, center: Point, start_angle: float, start_radius: float,
                   end_angle: float, end_radius: float, direction: int) -> None:
    a = _circle_point(center, start_radius, start_angle)
    b = _circle_point(center, end_radius, end_angle)
    delta = end_angle - start_angle
    if direction * delta < 0.0:
        delta += direction * 2 * math.pi
    i1 = _circle_point(center, 0.75 * start_radius + 0.25 * end_radius, start_angle + 0.25 * delta)
    i2 = _circle_point(center, 0.50 * start_radius + 0.50 * end_radius, start_angle + 0.50 * delta)
    i3 = _circle_point(center, 0.25 * start_radius + 0.75 * end_radius, start_angle + 0.75 * delta)
    for p, q, r in ((a, i1, i2), (i2, i3, b)):
        circle = points_to_circle(p, q, r)
        if circle is None:
            path.lineTo(_qpoint(r))
            continue
        (cx, cy), radius = circle
        a1 = math.atan2(cy - p[1], p[0] - cx)
        a2 = math.atan2(cy - r[1], r[0] - cx)
        _path_arc_to(path, (cx, cy), radius, a1, a2, direction)


def curved_arrow(po: Point, pm: Point, pd: Point, start_width: float, width: float,
                 head_width: float, head_height: float, head_type: int, arrow_type: int,
                 offset: float) -> List[Point]:
    """``curvedArrow``: the arrow along the circle through three points."""
    from qgis.PyQt.QtGui import QPainterPath  # pylint: disable=import-outside-toplevel
    circle = points_to_circle(po, pm, pd)
    if circle is None:
        return straight_arrow(po, pd, start_width, width, head_width, head_height,
                              head_type, arrow_type, offset)
    center, radius = circle
    cx, cy = center
    angle_o = _clamp_angle(math.atan2(cy - po[1], po[0] - cx))
    angle_m = _clamp_angle(math.atan2(cy - pm[1], pm[0] - cx))
    angle_d = _clamp_angle(math.atan2(cy - pd[1], pd[0] - cx))
    direction = 1 if _clamp_angle(angle_m - angle_o) < _clamp_angle(angle_m - angle_d) else -1
    a_type = 0
    if arrow_type == ARROW_RIGHT_HALF:
        a_type = direction
    elif arrow_type == ARROW_LEFT_HALF:
        a_type = -direction
    delta = angle_d - angle_o
    if direction * delta < 0.0:
        delta += direction * 2 * math.pi
    length = _dist(po, pd)
    if abs(delta) < math.pi and (
            (head_type in (HEAD_SINGLE, HEAD_REVERSED) and length < head_width)
            or (head_type == HEAD_DOUBLE and length < 2 * head_width)):
        return straight_arrow(po, pd, start_width, width, head_width, head_height,
                              head_type, arrow_type, offset)
    radius += offset
    po = _circle_point(center, radius, angle_o)
    pd = _circle_point(center, radius, angle_d)
    head_angle = direction * math.atan(head_width / radius)
    cp = _circle_point
    path = QPainterPath()

    def line_to(p):
        path.lineTo(_qpoint(p))
    if head_type == HEAD_DOUBLE:
        path.moveTo(_qpoint(po))
        if a_type <= 0:
            line_to(cp(center, radius + direction * head_height, angle_o + head_angle))
            _path_arc_to(path, center, radius + direction * width / 2, angle_o + head_angle,
                         angle_d - head_angle, direction)
            line_to(cp(center, radius + direction * head_height, angle_d - head_angle))
            line_to(pd)
        else:
            _path_arc_to(path, center, radius, angle_o, angle_d, direction)
        if a_type >= 0:
            line_to(cp(center, radius - direction * head_height, angle_d - head_angle))
            _path_arc_to(path, center, radius - direction * width / 2, angle_d - head_angle,
                         angle_o + head_angle, -direction)
            line_to(cp(center, radius - direction * head_height, angle_o + head_angle))
            line_to(po)
        else:
            _path_arc_to(path, center, radius, angle_d, angle_o, -direction)
    elif head_type == HEAD_SINGLE:
        if a_type <= 0:
            path.moveTo(_qpoint(cp(center, radius + direction * start_width / 2, angle_o)))
            _spiral_arc_to(path, center, angle_o, radius + direction * start_width / 2,
                           angle_d - head_angle, radius + direction * width / 2, direction)
            line_to(cp(center, radius + direction * head_height, angle_d - head_angle))
            line_to(pd)
        else:
            path.moveTo(_qpoint(po))
            _path_arc_to(path, center, radius, angle_o, angle_d, direction)
        if a_type >= 0:
            line_to(cp(center, radius - direction * head_height, angle_d - head_angle))
            _spiral_arc_to(path, center, angle_d - head_angle, radius - direction * width / 2,
                           angle_o, radius - direction * start_width / 2, -direction)
            line_to(cp(center, radius + direction * start_width / 2, angle_o))
        else:
            _path_arc_to(path, center, radius, angle_d, angle_o, -direction)
            line_to(cp(center, radius + direction * start_width / 2, angle_o))
    elif head_type == HEAD_REVERSED:
        path.moveTo(_qpoint(po))
        if a_type <= 0:
            line_to(cp(center, radius + direction * head_height, angle_o + head_angle))
            line_to(cp(center, radius + direction * width / 2, angle_o + head_angle))
            _spiral_arc_to(path, center, angle_o + head_angle, radius + direction * width / 2,
                           angle_d, radius + direction * start_width / 2, direction)
        else:
            _path_arc_to(path, center, radius, angle_o, angle_d, direction)
        if a_type >= 0:
            line_to(cp(center, radius - direction * start_width / 2, angle_d))
            _spiral_arc_to(path, center, angle_d, radius - direction * start_width / 2,
                           angle_o + head_angle, radius - direction * width / 2, -direction)
            line_to(cp(center, radius - direction * head_height, angle_o + head_angle))
            line_to(po)
        else:
            line_to(pd)
            _path_arc_to(path, center, radius, angle_d, angle_o, -direction)
    polygons = path.toSubpathPolygons()
    if not polygons:
        return []
    return [(p.x(), p.y()) for p in polygons[0]]


def arrow_polygons(points: Sequence[Point], curved: bool, repeated: bool, start_width: float,
                   width: float, head_length: float, head_thickness: float, head_type: int,
                   arrow_type: int, offset: float) -> List[List[Point]]:
    """Every arrow polygon QGIS draws for one line part (``renderPolyline``),
    all sizes in the units of ``points`` (painter pixels, y down)."""
    args = (start_width, width, head_length, head_thickness, head_type, arrow_type, offset)
    points = list(points)
    out: List[List[Point]] = []
    if curved and not repeated:
        if len(points) >= 3:
            out.append(curved_arrow(points[0], points[len(points) // 2], points[-1], *args))
        elif len(points) == 2:
            out.append(straight_arrow(points[0], points[1], *args))
    elif curved:
        for i in range(0, len(points) - 1, 2):
            if len(points) - i >= 3:
                out.append(curved_arrow(points[i], points[i + 1], points[i + 2], *args))
            elif len(points) - i == 2:
                out.append(straight_arrow(points[i], points[i + 1], *args))
    elif not repeated:
        if points:
            out.append(straight_arrow(points[0], points[-1], *args))
    else:
        for i in range(len(points) - 1):
            out.append(straight_arrow(points[i], points[i + 1], *args))
    return [p for p in out if len(p) >= 4]
