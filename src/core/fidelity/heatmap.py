"""
heatmap.py

QGIS heatmap renderer -> MapLibre ``heatmap`` layer.

QGIS (QgsHeatmapRenderer) adds ``weight * (1 - (d / r)^2)^2`` (quartic kernel,
radius ``r``) per point and colours ``min(density / max, 1)`` on its ramp,
``max`` being the highest density in the current view (or a fixed value).
Every pixel is coloured, zero density with the ramp's first colour.

MapLibre adds ``weight * intensity * GAUSS * exp(-4.5 * (d / R)^2)`` and
colours the clamped sum with ``heatmap-color``. A Gaussian with
``R = RADIUS_FACTOR * r`` fits the quartic kernel best where it is visible
(least squares over d < 0.85 r, where QGIS shows more than the lowest
colours), and ``intensity = 1 / (GAUSS * max)`` maps QGIS' maximum to the
top of the ramp. The automatic maximum depends on the view; it is estimated
per zoom from the densest point of the exported data.
"""

import math
from typing import Dict, List, Optional, Sequence, Tuple

GAUSS = 0.3989422804014327   # MapLibre's heatmap kernel coefficient
RADIUS_FACTOR = 1.33         # Gaussian radius fitting the quartic kernel where it shows
EARTH = 40075016.68557849    # Web Mercator circumference (m)
MAX_SAMPLES = 3000           # points the maximum density is evaluated at


def quartic_density(points: Sequence[Tuple[float, float, float]], at: Tuple[float, float],
                    radius: float, index=None) -> float:
    """QGIS' density at ``at``: sum of weight * (1 - (d / r)^2)^2 within r.
    ``points``: (x, y, weight); ``index``: optional {cell: [point indexes]}
    grid with cell size ``radius``."""
    x, y = at
    r2 = radius * radius
    total = 0.0
    if index is None:
        candidates = range(len(points))
    else:
        cx, cy = int(math.floor(x / radius)), int(math.floor(y / radius))
        candidates = [i for gx in (cx - 1, cx, cx + 1) for gy in (cy - 1, cy, cy + 1)
                      for i in index.get((gx, gy), ())]
    for i in candidates:
        px, py, weight = points[i]
        d2 = (px - x) ** 2 + (py - y) ** 2
        if d2 < r2:
            total += weight * (1.0 - d2 / r2) ** 2
    return total


class _Cell:
    """Points of a grid cell: their indexes, bounding box, once asked for
    the sums that give their summed kernel at any position within the
    radius of all of them (``_KernelGrid._moments``), and smaller cells."""

    __slots__ = ("members", "box", "sums", "parts")

    def __init__(self):
        self.members: List[int] = []
        self.box = [math.inf, math.inf, -math.inf, -math.inf]
        self.sums = None
        self.parts = None


class _KernelGrid:
    """QGIS' quartic density at many positions, exactly, in a time that does
    not grow with the points within the radius: a cell whose points all lie
    within the radius adds ``sum w (1 - d^2/r^2)^2`` from ten weighted sums
    (the kernel is a polynomial of the position), cells out of reach are
    skipped, and only cells across the radius are split (``SPLIT`` ×
    ``SPLIT`` smaller cells, again and again) down to a few points summed
    point by point. Zoomed out, a screen radius holds the whole layer: point
    by point, every position visited every point (minutes per zoom for
    200 000 points)."""

    SPLIT = 4
    DEPTH = 5   # cells down to radius / 4^5
    FEW = 8     # cells of this many points are summed point by point

    def __init__(self, points: Sequence[Tuple[float, float, float]], radius: float):
        self.points = points
        self.radius = radius
        self.r2 = radius * radius
        self.cells = self._grid(range(len(points)), radius)

    def _grid(self, members, size: float) -> Dict[Tuple[int, int], _Cell]:
        cells: Dict[Tuple[int, int], _Cell] = {}
        points = self.points
        for i in members:
            x, y = points[i][0], points[i][1]
            key = (int(math.floor(x / size)), int(math.floor(y / size)))
            cell = cells.get(key)
            if cell is None:
                cell = cells[key] = _Cell()
            cell.members.append(i)
            box = cell.box
            if x < box[0]:
                box[0] = x
            if y < box[1]:
                box[1] = y
            if x > box[2]:
                box[2] = x
            if y > box[3]:
                box[3] = y
        return cells

    def _moments(self, cell: _Cell):
        """(origin x, origin y, W, U, V, A, A2, AU, AV, UU, UV, VV): sums of
        w, w·u, w·v, w·a, w·a², w·a·u, w·a·v, w·u², w·u·v, w·v² over the
        cell's points, (u, v) their offsets from the box centre in radii and
        a = u² + v²."""
        if cell.sums is None:
            box = cell.box
            ox, oy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
            sums = [0.0] * 10
            for i in cell.members:
                x, y, w = self.points[i]
                u, v = (x - ox) / self.radius, (y - oy) / self.radius
                a = u * u + v * v
                sums[0] += w
                sums[1] += w * u
                sums[2] += w * v
                sums[3] += w * a
                sums[4] += w * a * a
                sums[5] += w * a * u
                sums[6] += w * a * v
                sums[7] += w * u * u
                sums[8] += w * u * v
                sums[9] += w * v * v
            cell.sums = (ox, oy, *sums)
        return cell.sums

    def _add(self, cell: _Cell, x: float, y: float, size: float, depth: int) -> float:
        if len(cell.members) <= self.FEW:
            return self._sum_points(cell.members, x, y)
        box = cell.box
        near_x = max(box[0] - x, 0.0, x - box[2])
        near_y = max(box[1] - y, 0.0, y - box[3])
        if near_x * near_x + near_y * near_y >= self.r2:
            return 0.0
        far_x = max(x - box[0], box[2] - x)
        far_y = max(y - box[1], box[3] - y)
        if far_x * far_x + far_y * far_y < self.r2:
            ox, oy, w, su, sv, sa, sa2, sau, sav, suu, suv, svv = self._moments(cell)
            tx, ty = (x - ox) / self.radius, (y - oy) / self.radius
            b = tx * tx + ty * ty
            tp = tx * su + ty * sv
            # sum w (1 - D)^2 with D = |p - t|^2 = a - 2 p·t + b (in radii)
            d1 = sa - 2.0 * tp + b * w
            d2 = (sa2 + 2.0 * b * sa + b * b * w - 4.0 * (tx * sau + ty * sav)
                  - 4.0 * b * tp + 4.0 * (tx * tx * suu + 2.0 * tx * ty * suv + ty * ty * svv))
            return w - 2.0 * d1 + d2
        if depth < self.DEPTH:
            if cell.parts is None:
                cell.parts = list(self._grid(cell.members, size / self.SPLIT).values())
            size /= self.SPLIT
            return sum(self._add(part, x, y, size, depth + 1) for part in cell.parts)
        return self._sum_points(cell.members, x, y)

    def _sum_points(self, members, x: float, y: float) -> float:
        total = 0.0
        r2 = self.r2
        for i in members:
            px, py, weight = self.points[i]
            d2 = (px - x) ** 2 + (py - y) ** 2
            if d2 < r2:
                total += weight * (1.0 - d2 / r2) ** 2
        return total

    def density(self, at: Tuple[float, float]) -> float:
        x, y = at
        cx, cy = int(math.floor(x / self.radius)), int(math.floor(y / self.radius))
        total = 0.0
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                cell = self.cells.get((gx, gy))
                if cell is not None:
                    total += self._add(cell, x, y, self.radius, 0)
        return total


def max_density(points: Sequence[Tuple[float, float, float]], radius: float,
                samples: int = MAX_SAMPLES) -> float:
    """Highest QGIS density: evaluated at data points and on a grid of
    quarter-radius steps inside every cell (radius wide) holding points, as
    the peak of overlapping kernels can lie between the points."""
    if not points or radius <= 0:
        return 0.0
    grid = _KernelGrid(points, radius)
    index = grid.cells
    step = max(1, len(points) // samples)
    at = [points[i][:2] for i in range(0, len(points), step)]
    cells = sorted(index, key=lambda cell: -len(index[cell].members))[:max(1, samples // 16)]
    for cx, cy in cells:  # the densest cells first
        at.extend(((cx + (i + 0.5) / 4) * radius, (cy + (j + 0.5) / 4) * radius)
                  for i in range(4) for j in range(4))
    return max(grid.density(xy) for xy in at)


def color_stops(ramp, stops: int = 32) -> List:
    """``heatmap-color`` from a QGIS colour ramp (zero density included)."""
    expression: List = ["interpolate", ["linear"], ["heatmap-density"]]
    for i in range(stops + 1):
        t = i / stops
        color = ramp.color(t)
        expression += [t, f"rgba({color.red()}, {color.green()}, {color.blue()}, "
                          f"{round(color.alphaF(), 3)})"]
    return expression


def css_px_per_mm() -> float:
    return 96.0 / 25.4


def map_units_per_css_px(zoom: float, latitude: float = 0.0, mercator: bool = True) -> float:
    """Ground size of a MapLibre CSS pixel (512 px tiles) at ``zoom``: Web
    Mercator units, or true metres at ``latitude``."""
    size = EARTH / (512.0 * 2 ** zoom)
    return size if mercator else size * math.cos(math.radians(latitude))


def heatmap_paint(spec: Dict, weight) -> Dict:
    """The ``paint`` of the MapLibre heatmap layer for an exported ``spec``
    (see ``build_spec``) and a weight (number or expression)."""
    if spec.get("radius_px") is not None:
        radius = round(spec["radius_px"] * RADIUS_FACTOR, 3)
    else:
        stops = spec["radius_stops"]
        radius = ["interpolate", ["exponential", 2], ["zoom"]]
        for zoom, px in stops:
            radius += [zoom, round(px * RADIUS_FACTOR, 4)]
    intensities = spec["intensity_stops"]
    if len(intensities) == 1:
        intensity = intensities[0][1]
    else:
        intensity = ["interpolate", ["linear"], ["zoom"]]
        for zoom, value in intensities:
            intensity += [zoom, value]
    return {"heatmap-weight": weight, "heatmap-radius": radius,
            "heatmap-intensity": intensity, "heatmap-color": spec["colors"],
            "heatmap-opacity": spec.get("opacity", 1.0)}


def build_spec(ramp, radius: float, radius_unit: str, explicit_max: float,
               points: Sequence[Tuple[float, float, float]], zooms: Sequence[int],
               latitude: float = 0.0, mercator: bool = True, mm_per_unit: Optional[float] = None,
               opacity: float = 1.0) -> Dict:
    """Heatmap spec for the style. ``radius_unit``: "map" (project CRS units,
    ``points`` in the same CRS) or "screen" (``mm_per_unit`` mm per unit);
    ``zooms``: the integer zooms the layer is shown at."""
    spec: Dict = {"colors": color_stops(ramp), "opacity": opacity}
    radius_at = {}
    if radius_unit == "map":
        spec["radius_px"] = None
        spec["radius_stops"] = [(z, radius / map_units_per_css_px(z, latitude, mercator))
                                for z in (0, 24)]
        for zoom in zooms:
            radius_at[zoom] = radius
    else:
        px = radius * (mm_per_unit or 1.0) * css_px_per_mm()
        spec["radius_px"] = px
        for zoom in zooms:  # the map size of the radius at that zoom
            radius_at[zoom] = px * map_units_per_css_px(zoom, latitude, mercator)
    stops = []
    tops: Dict[float, float] = {}  # a map radius is the same at every zoom
    for zoom in zooms:  # exact at whole zooms, interpolated between them
        if explicit_max > 0:
            top = explicit_max
        else:
            if radius_at[zoom] not in tops:
                tops[radius_at[zoom]] = max_density(points, radius_at[zoom])
            top = tops[radius_at[zoom]]
        if top > 0:
            stops.append((zoom, round(1.0 / (GAUSS * top), 6)))
    spec["intensity_stops"] = stops or [(0, round(1.0 / GAUSS, 6))]
    return spec
