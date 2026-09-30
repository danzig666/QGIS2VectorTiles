"""
patterns.py

Periodic hatch textures for ``QgsLinePatternFillSymbolLayer``.

A browser ``fill-pattern`` repeats a rectangular image. Parallel lines with
unit normal ``n``, spacing ``d`` and phase ``p`` — the set ``n·x = p + k·d`` —
repeat on a square ``N × N`` pixel cell only if both ``N·n_x`` and ``N·n_y``
are integer multiples of ``d``. We therefore search integers ``(a, b)`` with::

    n' = (a, b) / sqrt(a² + b²)            # realised normal direction
    N  = round(d · sqrt(a² + b²))          # cell size in pixels
    d' = N / sqrt(a² + b²)                 # realised spacing

and accept the smallest cell whose angle and spacing errors are within the
export tolerance. The cell is then rendered analytically (super-sampled
coverage of the wrapped distance to the nearest line), so it is seamless by
construction — no cropping of a symbol preview, no resize-to-fit.

Angles follow QGIS: degrees counter-clockwise from the horizontal line
direction; image y points down.
"""

import math
from dataclasses import dataclass
from typing import Optional, Tuple

from .model import ExportProfile


@dataclass(frozen=True)
class LinePatternSpec:
    """Plain description of a line hatch in CSS pixels."""

    angle_deg: float
    spacing_px: float
    line_width_px: float
    color_rgba: Tuple[int, int, int, int]
    offset_px: float = 0.0


@dataclass(frozen=True)
class PeriodicCell:
    size: int                 # N (logical CSS px)
    normal: Tuple[float, float]
    spacing_px: float         # realised spacing d'
    angle_deg: float          # realised line angle
    angle_error_deg: float
    spacing_error_px: float
    within_tolerance: bool


def _angle_diff(a: float, b: float) -> float:
    """Smallest difference between two line angles (period 180°)."""
    diff = (a - b) % 180.0
    return min(diff, 180.0 - diff)


def solve_periodic_cell(spec: LinePatternSpec, profile: ExportProfile,
                        max_component: int = 64) -> Optional[PeriodicCell]:
    """Find the smallest seamless square cell for ``spec`` (best effort)."""
    if spec.spacing_px <= 0 or not math.isfinite(spec.spacing_px):
        return None
    theta = math.radians(spec.angle_deg)
    # Line direction (image coordinates, y down) and its normal.
    nx, ny = math.sin(theta), math.cos(theta)
    sx = -1 if nx < 0 else 1
    sy = -1 if ny < 0 else 1
    best: Optional[PeriodicCell] = None
    best_score = float("inf")
    for a in range(0, max_component + 1):
        for b in range(0, max_component + 1):
            if a == 0 and b == 0:
                continue
            norm = math.hypot(a, b)
            size = round(spec.spacing_px * norm)
            if size < 1 or size > profile.max_pattern_cell_px:
                continue
            spacing = size / norm
            normal = (sx * a / norm, sy * b / norm)
            realised_angle = math.degrees(math.atan2(normal[0], normal[1]))
            angle_error = _angle_diff(realised_angle, spec.angle_deg)
            spacing_error = abs(spacing - spec.spacing_px)
            ok = (angle_error <= profile.tolerance_angle_deg
                  and profile.within_tolerance(spec.spacing_px, spacing))
            candidate = PeriodicCell(size, normal, spacing, realised_angle % 180.0,
                                     angle_error, spacing_error, ok)
            # Prefer tolerant candidates, then small cells, then small errors.
            score = (0 if ok else 1e6) + size + 10 * angle_error + spacing_error
            if score < best_score:
                best, best_score = candidate, score
    return best


def render_line_pattern(spec: LinePatternSpec, cell: PeriodicCell,
                        pixel_ratio: int = 1, supersample: int = 4):
    """Render one repeat cell as a Pillow RGBA image at ``pixel_ratio``."""
    from PIL import Image  # pylint: disable=import-outside-toplevel

    size = cell.size * pixel_ratio
    spacing = cell.spacing_px * pixel_ratio
    half_width = max(spec.line_width_px * pixel_ratio, 1e-6) / 2.0
    phase = spec.offset_px * pixel_ratio
    nx, ny = cell.normal
    red, green, blue, alpha = spec.color_rgba
    img = Image.new("RGBA", (size, size), (red, green, blue, 0))
    pixels = img.load()
    step = 1.0 / supersample
    samples = supersample * supersample
    for j in range(size):
        for i in range(size):
            hits = 0
            for sj in range(supersample):
                y = j + (sj + 0.5) * step
                for si in range(supersample):
                    x = i + (si + 0.5) * step
                    t = (nx * x + ny * y - phase) % spacing
                    if min(t, spacing - t) <= half_width:
                        hits += 1
            if hits:
                pixels[i, j] = (red, green, blue, round(alpha * hits / samples))
    return img


def tile_markers(marker, cell_w: int, cell_h: int, positions):
    """Paint ``marker`` (RGBA, centred on its origin) at ``positions`` in a
    ``cell_w × cell_h`` repeat cell, wrapping across edges so the cell tiles
    seamlessly."""
    from PIL import Image  # pylint: disable=import-outside-toplevel

    cell = Image.new("RGBA", (max(1, cell_w), max(1, cell_h)), (0, 0, 0, 0))
    half_w, half_h = marker.width / 2.0, marker.height / 2.0
    for x, y in positions:
        for wx in (-cell_w, 0, cell_w):
            for wy in (-cell_h, 0, cell_h):
                left = int(round(x + wx - half_w))
                top = int(round(y + wy - half_h))
                if left >= cell_w or top >= cell_h or left + marker.width <= 0 \
                        or top + marker.height <= 0:
                    continue
                layer = Image.new("RGBA", cell.size, (0, 0, 0, 0))
                layer.paste(marker, (left, top))
                cell = Image.alpha_composite(cell, layer)
    return cell


def point_pattern_cell(dx: float, dy: float, disp_x: float, disp_y: float):
    """Repeat cell size and marker positions (CSS px) of a point pattern.

    Displacement of alternate rows/columns doubles the cell in that
    direction. Returns ``(width, height, positions, max_relative_error)``
    with integer cell sizes; the error measures the spacing change caused by
    rounding to whole pixels.
    """
    rows = 2 if disp_x else 1
    cols = 2 if disp_y else 1
    width = max(1, round(dx * cols))
    height = max(1, round(dy * rows))
    real_dx, real_dy = width / cols, height / rows
    error = max(abs(real_dx - dx) / dx, abs(real_dy - dy) / dy)
    positions = []
    for j in range(rows):
        for i in range(cols):
            x = i * real_dx + (disp_x if j % 2 else 0.0)
            # Image y points down; QGIS displacement Y moves columns up.
            y = height - (j * real_dy + (disp_y if i % 2 else 0.0))
            positions.append((x % width, y % height))
    return width, height, positions, error
