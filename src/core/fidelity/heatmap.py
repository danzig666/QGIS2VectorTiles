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


def max_density(points: Sequence[Tuple[float, float, float]], radius: float,
                samples: int = MAX_SAMPLES) -> float:
    """Highest QGIS density: evaluated at data points and on a grid of
    quarter-radius steps inside every cell (radius wide) holding points, as
    the peak of overlapping kernels can lie between the points."""
    if not points or radius <= 0:
        return 0.0
    index: Dict[Tuple[int, int], List[int]] = {}
    for i, (x, y, _) in enumerate(points):
        index.setdefault((int(math.floor(x / radius)), int(math.floor(y / radius))), []).append(i)
    step = max(1, len(points) // samples)
    at = [points[i][:2] for i in range(0, len(points), step)]
    cells = sorted(index, key=lambda cell: -len(index[cell]))[:max(1, samples // 16)]
    for cx, cy in cells:  # the densest cells first
        at.extend(((cx + (i + 0.5) / 4) * radius, (cy + (j + 0.5) / 4) * radius)
                  for i in range(4) for j in range(4))
    return max(quartic_density(points, xy, radius, index) for xy in at)


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
    for zoom in zooms:  # exact at whole zooms, interpolated between them
        top = explicit_max if explicit_max > 0 else max_density(points, radius_at[zoom])
        if top > 0:
            stops.append((zoom, round(1.0 / (GAUSS * top), 6)))
    spec["intensity_stops"] = stops or [(0, round(1.0 / GAUSS, 6))]
    return spec
