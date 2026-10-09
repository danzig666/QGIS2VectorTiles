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


def qgis_image_hatch(angle_deg: float, spacing_px: float) -> Tuple[float, float]:
    """Angle and spacing QGIS really draws a screen hatch with.

    With the default clip mode QGIS paints the lines into a brush image
    whose sides are truncated to whole pixels
    (``QgsLinePatternFillSymbolLayer::applyPattern``): a 7.56 px spacing is
    drawn 7 px apart, and an oblique hatch gets the angle and spacing of the
    truncated ``(spacing / sin, spacing / cos)`` image.
    """
    def near(a: float, b: float) -> bool:
        return abs(a - b) < 1e-8

    angle = angle_deg % 360.0
    if any(near(angle, a) for a in (0.0, 90.0, 180.0, 270.0, 360.0)):
        whole = int(spacing_px)
        return angle_deg, float(whole) if whole >= 1 else spacing_px
    height = int(spacing_px / math.cos(math.radians(angle)))
    width = int(spacing_px / math.sin(math.radians(angle)))
    if height == 0 or width == 0:
        return angle_deg, spacing_px
    real = math.degrees(math.atan2(height, width)) % 360.0
    return real, abs(abs(height) * math.cos(math.radians(real)))


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


SUPERSAMPLE = 4


def apply_pattern_positions(dx: float, dy: float, disp_x: float = 0.0, disp_y: float = 0.0,
                            off_x: float = 0.0, off_y: float = 0.0):
    """QgsPointPatternFillSymbolLayer::applyPattern (QGIS 3.34), the texture
    brush of a point pattern clipped to the shape: ``(width, height,
    positions)`` with the image size truncated to whole pixels, as QImage
    does (two spacings, e.g. 37 px for 18.9 px), and the marker centres in
    QGIS's drawing order (later ones on top; image y down). Markers are
    drawn at the real spacing, also beyond the image, not wrapped: the
    texture repeats every ``width`` pixels."""
    width, height = 2.0 * dx, 2.0 * dy
    wo, ho = math.fmod(off_x, width), math.fmod(off_y, height)
    positions = []

    def steps(start, stop, step):  # QGIS's accumulating float loops
        value = start
        while value <= stop:
            yield value
            value += step
    for x in steps(-width, width * 2.0, width):
        for y in steps(-height, height * 2.0, height):
            positions.append((x + wo, y + ho))
    for x in steps(-width, width * 2.0, width):
        for y in steps(-height / 2.0, height * 2.0, height):
            positions.append((x + wo + disp_x, y + ho))
    for x in steps(-width / 2.0, width * 2.0, width):
        for y in steps(-height, height * 2.0, height / 2.0):
            positions.append((x + wo + (disp_x if math.fmod(y, height) != 0 else 0.0),
                              y + ho - disp_y))
    return int(width), int(height), positions


_TILED: "OrderedDict" = None  # (marker, cell, positions) -> cell image (tile_markers)
_TILED_MAX = 48


def tile_markers(marker, cell_w: int, cell_h: int, positions, wrap: bool = True,
                 snap: bool = False):
    """``_tile_markers``, remembered: the same pattern cell is asked for at
    several zoom levels and by several styles (96 calls, 19 cells in a big
    project), each a few thousand markers composited one by one. ``snap``:
    each marker on whole pixels, as QPainter::drawImage puts QGIS's cached
    simple-marker image (supersampling smeared those dots over a pixel)."""
    global _TILED  # pylint: disable=global-statement
    import hashlib  # pylint: disable=import-outside-toplevel
    from collections import OrderedDict  # pylint: disable=import-outside-toplevel
    if _TILED is None:
        _TILED = OrderedDict()
    key = (hashlib.sha1(marker.tobytes()).hexdigest(), marker.mode, marker.size, int(cell_w), int(cell_h),
           tuple((float(x), float(y)) for x, y in positions), bool(wrap), bool(snap))
    cell = _TILED.get(key)
    if cell is None:
        cell = _TILED[key] = (_paste_markers(marker, cell_w, cell_h, positions, wrap, qt_round=True)
                              if snap else _tile_markers(marker, cell_w, cell_h, positions, wrap))
        while len(_TILED) > _TILED_MAX:
            _TILED.popitem(last=False)
    else:
        _TILED.move_to_end(key)
    return cell.copy()


def _tile_markers(marker, cell_w: int, cell_h: int, positions, wrap: bool = True):
    """Paint ``marker`` (RGBA, centred on its origin) at ``positions`` in a
    ``cell_w × cell_h`` repeat cell, wrapping across edges so the cell tiles
    seamlessly (``wrap``; otherwise markers are clipped at the cell edge).

    Positions off the pixel grid (a dense pattern spaced 2.1 px) are painted
    on a ``SUPERSAMPLE``-times larger cell and averaged down: rounding each
    marker to a whole pixel made gaps of 2 and 3 px, seen as a grid of seams.
    The enlarged marker is the pixel render QGIS would draw, smoothly scaled:
    rendering it finer drew thin outlines lighter than QGIS does.
    """
    from PIL import Image  # pylint: disable=import-outside-toplevel

    half_w, half_h = marker.width / 2.0, marker.height / 2.0
    off_grid = any(abs((x - half_w) - round(x - half_w)) > 0.05
                   or abs((y - half_h) - round(y - half_h)) > 0.05 for x, y in positions)
    if not off_grid:
        return _paste_markers(marker, cell_w, cell_h, positions, wrap)
    big = marker.resize((marker.width * SUPERSAMPLE, marker.height * SUPERSAMPLE), Image.BICUBIC)
    cell = _paste_markers(big, cell_w * SUPERSAMPLE, cell_h * SUPERSAMPLE,
                          [(x * SUPERSAMPLE, y * SUPERSAMPLE) for x, y in positions], wrap)
    return cell.resize((max(1, cell_w), max(1, cell_h)), Image.BOX)


def _paste_markers(marker, cell_w: int, cell_h: int, positions, wrap: bool = True,
                   qt_round: bool = False):
    """``tile_markers`` with every marker rounded to whole pixels; ``qt_round``:
    halves rounded up, like Qt's qRound (Python rounds them to even, which
    alternates on the x.5 positions odd-sized marker images always have)."""
    from PIL import Image  # pylint: disable=import-outside-toplevel

    cell = Image.new("RGBA", (max(1, cell_w), max(1, cell_h)), (0, 0, 0, 0))
    half_w, half_h = marker.width / 2.0, marker.height / 2.0
    shifts_x, shifts_y = ((-cell_w, 0, cell_w), (-cell_h, 0, cell_h)) if wrap else ((0,), (0,))
    for x, y in positions:
        for wx in shifts_x:
            for wy in shifts_y:
                if qt_round:
                    left = math.floor(x + wx - half_w + 0.5)
                    top = math.floor(y + wy - half_h + 0.5)
                else:
                    left = int(round(x + wx - half_w))
                    top = int(round(y + wy - half_h))
                if left >= cell_w or top >= cell_h or left + marker.width <= 0 \
                        or top + marker.height <= 0:
                    continue
                # Composite the part of the marker that falls inside the cell.
                crop = (max(0, -left), max(0, -top), min(marker.width, cell_w - left),
                        min(marker.height, cell_h - top))
                cell.alpha_composite(marker, dest=(max(0, left), max(0, top)), source=crop)
    return cell


def _repeats(period: float, tolerance: float = 0.02, limit: int = 64,
             max_px: float = 256.0) -> int:
    """Smallest number of periods whose total length is (nearly) a whole
    number of pixels, so dense or fractional spacings keep their density."""
    best_error, best = float("inf"), 1
    for count in range(1, limit + 1):
        size = period * count
        if count > 1 and size > max_px:
            break
        error = abs(max(1, round(size)) - size) / size
        if error < best_error - 1e-12:
            best_error, best = error, count
        if error <= tolerance:
            return count
    return best


def point_pattern_cell(dx: float, dy: float, disp_x: float, disp_y: float):
    """Repeat cell size and marker positions (CSS px) of a point pattern.

    Displacement of alternate rows/columns doubles the cell in that
    direction; spacings that are not close to whole pixels (or smaller than
    one) repeat several times inside the cell. Returns ``(width, height,
    positions, max_relative_error)`` with integer cell sizes; the error
    measures the spacing change caused by rounding to whole pixels.
    """
    rows = 2 if disp_x else 1
    cols = 2 if disp_y else 1
    cols *= _repeats(dx * cols)
    rows *= _repeats(dy * rows)
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


def frame_image(shape: str, fill_rgba, stroke_rgba, stroke_px: float, radius_px: float,
                pixel_ratio: float, size_px: int = 0):
    """Label background frame and its stretch metadata (logical pixels).

    ``shape``: "rectangle" (stretchable: corners and border keep their size
    when MapLibre fits the frame around the text) or "ellipse" (the whole
    image is scaled). Returns ``(image, metadata)``.
    """
    from PIL import Image, ImageDraw  # pylint: disable=import-outside-toplevel

    edge = max(stroke_px, 0.0)
    radius = max(radius_px, 0.0)
    logical = size_px or int(max(32, 2 * math.ceil(radius + edge) + 16))
    ss = 4  # supersampling for anti-aliased edges
    scale = pixel_ratio * ss
    big = max(1, round(logical * scale))
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # PIL draws an outline inside the box: the stroke covers [0, edge] from
    # the image edge, inside the fixed (non-stretched) border below. An inset
    # of half the stroke pushed thick map-unit strokes into the stretched
    # middle, which then filled the whole frame.
    box = [0, 0, big - 1, big - 1]
    width = max(0, round(edge * scale))
    fill = tuple(fill_rgba)
    outline = tuple(stroke_rgba) if width else None
    if shape == "ellipse":
        draw.ellipse(box, fill=fill, outline=outline, width=width)
    else:
        draw.rounded_rectangle(box, radius=radius * scale, fill=fill, outline=outline,
                               width=width)
    out = img.resize((max(1, round(logical * pixel_ratio)),) * 2, Image.LANCZOS)
    metadata = {}
    if shape != "ellipse" and not size_px:
        fixed = math.ceil(radius + edge) + 1
        metadata = {"stretchX": [[fixed, logical - fixed]],
                    "stretchY": [[fixed, logical - fixed]],
                    "content": [edge, edge, logical - edge, logical - edge]}
    return out, metadata


def rotated_lattice_cell(tile_w: float, tile_h: float, angle_deg: float,
                         max_cell_px: float = 512.0, max_index: int = 12):
    """A seamless axis-aligned cell for a texture of ``tile_w`` × ``tile_h``
    tiles rotated by ``angle_deg`` (image coordinates, clockwise on screen,
    as ``QTransform.rotate``).

    The cell's sides (W, 0) and (0, H) must be whole lattice steps
    ``i·u + j·v``; with an irrational tangent no small cell is exact, so
    the lattice is adjusted slightly (``u``, ``v`` solved back from the
    whole cell). Returns ``(W, H, u, v, error)``: integer cell size, the
    adjusted tile axes and the largest relative change of a tile axis
    (about angle error in radians), or None.
    """
    a = math.radians(angle_deg)
    u0 = (tile_w * math.cos(a), tile_w * math.sin(a))
    v0 = (-tile_h * math.sin(a), tile_h * math.cos(a))
    rows, cols = [], []
    for i in range(-max_index, max_index + 1):
        for j in range(-max_index, max_index + 1):
            x = i * u0[0] + j * v0[0]
            y = i * u0[1] + j * v0[1]
            if 0.5 <= x <= max_cell_px and abs(y) <= 0.15 * x:
                rows.append((abs(y) / x, i, j, max(1, round(x))))
            if 0.5 <= y <= max_cell_px and abs(x) <= 0.15 * y:
                cols.append((abs(x) / y, i, j, max(1, round(y))))
    rows.sort()
    cols.sort()
    best = None
    for _, i, j, width in rows[:40]:
        for _, k, l, height in cols[:40]:
            det = i * l - j * k
            if det == 0:
                continue
            # [W 0; 0 H] = [i j; k l] [u; v]  =>  [u; v] = M^-1 diag(W, H)
            u = (l * width / det, -j * height / det)
            v = (-k * width / det, i * height / det)
            error = max(math.hypot(u[0] - u0[0], u[1] - u0[1]) / tile_w,
                        math.hypot(v[0] - v0[0], v[1] - v0[1]) / tile_h)
            score = error + width * height / (max_cell_px * max_cell_px) * 0.002
            if best is None or score < best[0]:
                best = (score, width, height, u, v, error)
    return None if best is None else best[1:]
