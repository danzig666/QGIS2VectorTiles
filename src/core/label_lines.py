"""
label_lines.py

Repeated curved line labels laid out at export time, one zoom at a time, the
way QGIS lays them out, so that MapLibre only centres each label on a short
line made for it ("line-center").

MapLibre's own line placement tries fixed anchors only (half a label + 2 em
from the line start, then every symbol-spacing), drops an anchor where the
vertex turns within 0.6 em add up to more than text-max-angle, and cuts the
line at every tile edge: a wiggly river got no label at all at low zooms
where QGIS draws several. QGIS (pal) cuts each line into parts at the repeat
distance (``Layer::chopFeaturesAtRepeatDistance``), tries a candidate every
few millimetres in each part (``FeaturePart::createCurvedCandidatesAlongLine``),
lays the characters along the line one chord each (``nextCharPosition``),
rejects a candidate where two characters turn more than the curved angle
limits, and keeps the cheapest: the straightest, closest to the middle.

Here every part gets QGIS's best candidate that MapLibre can draw: inside
one tile of the zoom (MapLibre cuts lines at tile edges) and accepted by an
exact port of MapLibre's angle check (``check_max_angle``) on the
coordinates the tile will hold. The window returned is the candidate's
character chord points, its ends pushed out by a margin along the first and
last chord (the glyph advances MapLibre uses can be a little longer).

Pure Python, no QGIS: lengths are CSS pixels of the tile zoom (512-pixel
tiles, y down).
"""

import bisect
import math
from typing import List, Optional, Sequence, Tuple

Point = Tuple[float, float]

TILE_PX = 512.0        # MapLibre's tile size in CSS pixels
TILE_UNITS = 8192      # MapLibre's tile coordinates (EXTENT)
MVT_UNITS = 4096       # GDAL's MVT extent: what the tiles hold, in 1/8 px


def repeat_parts(length: float, label_width: float, repeat: float) -> List[Tuple[float, float]]:
    """(start, end) along a line of the parts QGIS labels one by one
    (pal Layer::chopFeaturesAtRepeatDistance): a line longer than the repeat
    distance is cut into as many equal parts as fit a whole multiple of the
    repeat distance at least as long as the label; otherwise the whole line."""
    if repeat > 0 and length > repeat:
        interval = repeat * max(1, math.ceil(label_width / repeat))
        count = int(math.floor(length / interval))
        if count > 1:
            size = length / count
            return [(k * size, (k + 1) * size) for k in range(count)]
    return [(0.0, length)]


def cumulative_lengths(xs: Sequence[float], ys: Sequence[float]) -> List[float]:
    out = [0.0]
    for i in range(1, len(xs)):
        out.append(out[-1] + math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1]))
    return out


def chord_points(xs: Sequence[float], ys: Sequence[float], cumulative: Sequence[float],
                 start: float, advances: Sequence[float]) -> Optional[Tuple[List[Point], float]]:
    """The character boundaries of a label laid along the line from
    ``start`` (distance along it), as QGIS lays them
    (QgsTextRendererUtils::nextCharPosition): each character ends at the
    first point of the line, after its start, that is its advance away in a
    straight line. Returns the points and the distance along the line of the
    last one; None when the line ends first."""
    count = len(xs)
    k = bisect.bisect_left(cumulative, start, 1) - 1  # segment k: vertex k -> k + 1
    if k < 0 or k >= count - 1:
        return None
    segment = cumulative[k + 1] - cumulative[k]
    if segment <= 0:
        return None
    along = start - cumulative[k]
    x = xs[k] + (xs[k + 1] - xs[k]) * along / segment
    y = ys[k] + (ys[k + 1] - ys[k]) * along / segment
    points = [(x, y)]
    for advance in advances:
        segment = cumulative[k + 1] - cumulative[k]
        if segment - along >= advance:
            along += advance
            x = xs[k] + (xs[k + 1] - xs[k]) * along / segment
            y = ys[k] + (ys[k + 1] - ys[k]) * along / segment
        else:
            sx, sy = x, y
            k += 1
            while True:  # the first segment ending an advance away or further
                if k >= count - 1:
                    return None
                if math.hypot(xs[k + 1] - sx, ys[k + 1] - sy) >= advance:
                    break
                k += 1
            # QgsTextRendererUtils::findLineCircleIntersection: where the
            # segment leaves the circle of the advance around the start.
            x1, y1 = xs[k] - sx, ys[k] - sy
            dx, dy = xs[k + 1] - xs[k], ys[k + 1] - ys[k]
            a = dx * dx + dy * dy
            b = 2 * (dx * x1 + dy * y1)
            c = x1 * x1 + y1 * y1 - advance * advance
            t = (-b + math.sqrt(max(0.0, b * b - 4 * a * c))) / (2 * a)
            x, y = sx + x1 + t * dx, sy + y1 + t * dy
            along = math.hypot(x - xs[k], y - ys[k])
        points.append((x, y))
    return points, cumulative[k] + along


def _direction(a: Point, b: Point) -> float:
    return math.atan2(b[1] - a[1], b[0] - a[0])


def char_turns(points: Sequence[Point]) -> List[float]:
    """Turn from each character's chord to the next, in radians: positive
    to the left as seen on screen (QGIS's sign in map coordinates, y up)."""
    turns = []
    for i in range(1, len(points) - 1):
        turn = _direction(points[i - 1], points[i]) - _direction(points[i], points[i + 1])
        while turn > math.pi:
            turn -= 2 * math.pi
        while turn < -math.pi:
            turn += 2 * math.pi
        turns.append(turn)
    return turns


def char_turns_ok(turns: Sequence[float], max_in: float, max_out: float) -> bool:
    """QGIS's curved label limits (maxCurvedCharAngleIn / Out, degrees; the
    outside one negative): a turn to the left more than ``max_in``, or to
    the right more than ``-max_out``, rejects the placement; a limit of 0
    (or of the wrong sign) does not apply."""
    inside, outside = math.radians(max_in), math.radians(max_out)
    for turn in turns:
        if (inside > 0 and turn > inside) or (outside < 0 and turn < outside):
            return False
    return True


def check_max_angle(line: Sequence[Point], anchor: Tuple[float, float, int],
                    label_length: float, window: float, max_angle: float) -> bool:
    """MapLibre's checkMaxAngle (symbol/check_max_angle.ts), line for line:
    False when the turns at the line's vertices within any ``window``
    stretch of the label (``label_length`` centred on the anchor) add up to
    more than ``max_angle`` (radians), or when the label runs off the line."""
    x, y, segment = anchor
    if segment is None or label_length == 0:
        return True
    current = (x, y)
    index = segment + 1
    distance = 0.0
    while distance > -label_length / 2:  # back to the label's start
        index -= 1
        if index < 0:
            return False
        distance -= math.sqrt(_dist2(line[index], current))
        current = line[index]
    distance += math.sqrt(_dist2(line[index], line[index + 1]))
    index += 1
    recent: List[Tuple[float, float]] = []
    total = 0.0
    while distance < label_length / 2:
        if index + 1 >= len(line):
            return False
        prev, here, nxt = line[index - 1], line[index], line[index + 1]
        delta = _angle_to(prev, here) - _angle_to(here, nxt)
        delta = abs((delta + 3 * math.pi) % (math.pi * 2) - math.pi)
        recent.append((distance, delta))
        total += delta
        while distance - recent[0][0] > window:
            total -= recent.pop(0)[1]
        if total > max_angle:
            return False
        index += 1
        distance += math.sqrt(_dist2(here, nxt))
    return True


def _dist2(a: Point, b: Point) -> float:
    dx, dy = b[0] - a[0], b[1] - a[1]
    return dx * dx + dy * dy


def _angle_to(a: Point, b: Point) -> float:
    """Point.angleTo"""
    return math.atan2(a[1] - b[1], a[0] - b[0])


def _js_round(value: float) -> float:
    """Math.round"""
    return math.floor(value + 0.5)


def center_anchor(line: Sequence[Point], label_length: float, window: float,
                  max_angle: float) -> Optional[Tuple[float, float, int]]:
    """MapLibre's getCenterAnchor ("line-center" placement): the anchor at
    half the line's length, rounded to tile units, if the label fits there
    (check_max_angle); None otherwise."""
    half = sum(math.sqrt(_dist2(line[i], line[i + 1])) for i in range(len(line) - 1)) / 2
    travelled = 0.0
    for i in range(len(line) - 1):
        a, b = line[i], line[i + 1]
        segment = math.sqrt(_dist2(a, b))
        if travelled + segment > half:
            t = (half - travelled) / segment
            anchor = (_js_round(a[0] + t * (b[0] - a[0])), _js_round(a[1] + t * (b[1] - a[1])), i)
            if not window or check_max_angle(line, anchor, label_length, window, max_angle):
                return anchor
            return None
        travelled += segment
    return None


def tile_line(points: Sequence[Point], edge: float = 1.0) -> Optional[List[Tuple[int, int]]]:
    """The window as MapLibre reads it from its tile: rounded to the MVT
    grid (1/8 px), doubled to MapLibre's 8192 units, repeated points
    dropped; None unless every point lies in the tile of the first one, at
    least ``edge`` px inside its edges."""
    tx, ty = math.floor(points[0][0] / TILE_PX), math.floor(points[0][1] / TILE_PX)
    grid, scale = MVT_UNITS / TILE_PX, TILE_UNITS // MVT_UNITS
    low, high = edge * TILE_UNITS / TILE_PX, TILE_UNITS - edge * TILE_UNITS / TILE_PX
    out: List[Tuple[int, int]] = []
    for x, y in points:
        u = int(_js_round((x - tx * TILE_PX) * grid)) * scale
        v = int(_js_round((y - ty * TILE_PX) * grid)) * scale
        if not (low <= u <= high and low <= v <= high):
            return None
        if not out or out[-1] != (u, v):
            out.append((u, v))
    return out if len(out) >= 2 else None


def extended(points: Sequence[Point], margin: float) -> List[Point]:
    """``points`` with the first and last pushed ``margin`` further out
    along their chords (no new vertex, so no new turn)."""
    def pushed(end: Point, inner: Point) -> Point:
        length = math.hypot(end[0] - inner[0], end[1] - inner[1])
        if length <= 0:
            return end
        return (end[0] + (end[0] - inner[0]) / length * margin,
                end[1] + (end[1] - inner[1]) / length * margin)
    out = list(points)
    out[0] = pushed(points[0], points[1])
    out[-1] = pushed(points[-1], points[-2])
    return out


def label_windows(xs: Sequence[float], ys: Sequence[float], advances: Sequence[float],
                  repeat: float, step: float, *, chop_width: Optional[float] = None,
                  max_in: float = 25.0, max_out: float = -25.0, max_angle: float = 25.0,
                  angle_window: float = 0.0, anchor: float = 0.5, margin: float = 2.0,
                  fine_step: float = 1.0) -> List[List[Point]]:
    """One window per label QGIS draws along the line (pixels of the tile
    zoom): per repeat part (``repeat``; ``chop_width`` the label width the
    cut is made with, default the label's), QGIS's cheapest candidate on its
    own grid (every ``step`` px from the part's start) that MapLibre draws:
    inside one tile and passing check_max_angle (``max_angle`` degrees over
    ``angle_window`` px, MapLibre's 0.6 em). A part where that grid finds
    nothing is searched again every ``fine_step`` px; a part where no
    placement keeps QGIS's angles between characters has no label, as in
    QGIS.

    ``advances``: each character's advance in px (MapLibre's glyph advances
    and letter spacing, which QGIS measures the same way); ``anchor``: the
    line anchor position (QGIS's line settings, 0.5 = the middle)."""
    xs, ys = list(xs), list(ys)
    keep = [i for i in range(len(xs)) if i == 0 or (xs[i], ys[i]) != (xs[i - 1], ys[i - 1])]
    xs, ys = [xs[i] for i in keep], [ys[i] for i in keep]
    advances = [a for a in advances if a > 0]  # QGIS skips zero-width characters
    if len(xs) < 2 or not advances:
        return []
    cumulative = cumulative_lengths(xs, ys)
    width = sum(advances)
    if cumulative[-1] < width:
        return []
    layout = (xs, ys, cumulative, advances, width, max_in, max_out, anchor)
    check = (width, angle_window, math.radians(max_angle), margin)
    windows = []
    for start, end in repeat_parts(cumulative[-1], width if chop_width is None else chop_width,
                                   repeat):
        window = _drawable(_candidates(layout, start, end, max(step, 1e-3)), check)
        if window is None and fine_step < step:
            # QGIS's grid is one sample of the offsets it tries at the other
            # scales of the zoom: a part where it finds nothing MapLibre
            # draws is searched again, finer.
            window = _drawable(_candidates(layout, start, end, fine_step), check)
        if window is not None:
            windows.append(window)
    return windows


def _candidates(layout, start: float, end: float, step: float):
    """(cost, offset, chord points) of QGIS's valid candidates in a part:
    every ``step`` from its start; cost as pal's (the mean turn between
    characters, then the distance of the label's middle from the anchor
    point, relative to the part's length)."""
    xs, ys, cumulative, advances, width, max_in, max_out, anchor = layout
    length = end - start
    # pal: a line anchor between 10 % and 90 % only hints, so it weighs less
    divisor = 100.0 if 0.1 < anchor < 0.9 else 10.0
    out = []
    k = 0
    while start + k * step <= end - width + 1e-9:
        offset = start + k * step
        k += 1
        placed = chord_points(xs, ys, cumulative, offset, advances)
        if placed is None or placed[1] > end + 1e-6:  # off the line or the part
            continue
        points = placed[0]
        turns = char_turns(points)
        if not char_turns_ok(turns, max_in, max_out):
            continue
        cost = sum(abs(t) for t in turns) / max(1, len(advances) - 1) / 100.0
        cost += abs(anchor * length - (offset - start + width / 2)) / length / divisor
        out.append((cost, offset, points))
    return out


def _drawable(candidates, check) -> Optional[List[Point]]:
    """The cheapest candidate MapLibre draws, extended by the margin."""
    width, angle_window, max_angle, margin = check
    units = TILE_UNITS / TILE_PX
    for _, _, points in sorted(candidates, key=lambda c: (c[0], c[1])):
        window = extended(points, margin)
        line = tile_line(window)
        if line is None:
            continue
        if center_anchor(line, width * units, angle_window * units, max_angle) is None:
            continue
        return window
    return None
