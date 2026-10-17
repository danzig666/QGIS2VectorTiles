"""
Shape mismatch measured along the drawn lines (the gallery's "along" score).

Markers and dashes along a line may sit a little further along the line in
the browser than in QGIS (MapLibre computes interval positions per step of
a zoom, see materializer INTERVAL_BAND_STEPS) without the map looking any
different. The strict shape score counts every such shift as missing ink;
this one forgives it: the ink within ``half_width`` px of each line (or
polygon ring) is compared after sliding the browser image along the line,
by the best shift of each stretch of ``window`` px (at most ``max_shift``
px). Within a stretch everything slides together, so dashes against
markers, the spacing, and anything missing or extra still count.

Inside a polygon a fill pattern may sit at another origin: that ink is
compared after the best 2D shift of each ``FILL_WINDOW`` px square (at most
``FILL_MAX_SHIFT`` px each way): a pattern of another spacing, size or
density still counts. A ring's ink inside the polygon
matches either way (along the ring or with the fill's shift).

Kept strict (compared in place): ink further from the line and outside a
polygon, within ``end_margin`` px of an open line's ends (first and last
vertex markers, the first interval marker), within ``bend_margin`` px of a
sharp vertex (where "along the line" is ambiguous), and points.

Pure numpy; paths in image pixels (x right, y down).
"""

import math

import numpy as np

LINE_HALF_WIDTH = 32    # px either side of a line
RING_HALF_WIDTH = 16    # px either side of a polygon ring (fills lie beyond)
MAX_SHIFT = 24          # px along the line, either way
WINDOW = 128            # px of line sharing one shift
END_MARGIN = 12         # px at each end of an open line, compared in place
BEND_MARGIN = 8         # px around a sharp vertex, compared in place
SHARP_DEG = 20.0        # a vertex turning more than this is sharp
FILL_WINDOW = 96        # px square of a fill sharing one 2D shift
FILL_MAX_SHIFT = 12     # px, either way, x and y


def grow(mask):
    """3x3 dilation (the strict score's 1 px tolerance)."""
    padded = np.pad(mask, 1)
    h, w = mask.shape
    out = np.zeros_like(mask)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            out |= padded[dy:dy + h, dx:dx + w]
    return out


class _Path:
    def __init__(self, points, closed):
        pts = np.asarray(points, dtype=float)
        if closed and len(pts) > 1 and np.allclose(pts[0], pts[-1]):
            pts = pts[:-1]
        if closed:
            pts = np.vstack([pts, pts[:1]])
        keep = np.ones(len(pts), dtype=bool)
        keep[1:] = np.hypot(*(pts[1:] - pts[:-1]).T) > 1e-6
        pts = pts[keep]
        self.closed = closed
        self.a = pts[:-1]
        self.d = pts[1:] - pts[:-1]
        self.seg_len = np.hypot(self.d[:, 0], self.d[:, 1])
        self.dir = self.d / self.seg_len[:, None]
        self.normal = np.stack([-self.dir[:, 1], self.dir[:, 0]], axis=1)
        self.start = np.concatenate([[0.0], np.cumsum(self.seg_len)[:-1]])
        self.length = float(self.seg_len.sum())
        # Arc lengths of sharp vertices.
        sharp = []
        n = len(self.dir)
        for i in range(1, n + (1 if closed else 0)):
            u, v = self.dir[i - 1], self.dir[i % n]
            turn = math.degrees(math.acos(max(-1.0, min(1.0, float(u @ v)))))
            if turn > SHARP_DEG:
                sharp.append(self.start[i % n] if i < n else 0.0)
        self.sharp = np.asarray(sharp)

    def at(self, s, t):
        """Image points at arc length ``s`` and offset ``t`` (beyond an open
        line's ends, along its end segments)."""
        if self.closed:
            s = np.mod(s, self.length)
        k = np.clip(np.searchsorted(self.start, s, side="right") - 1, 0, len(self.start) - 1)
        along = s - self.start[k]
        return (self.a[k] + self.dir[k] * along[:, None] + self.normal[k] * t[:, None])


def _project(px, paths, half_width):
    """Nearest path, arc length and offset of each pixel centre ``px``
    (N x 2); path -1 where the pixel is compared in place."""
    n = len(px)
    best = np.full(n, np.inf)
    which = np.full(n, -1)
    s_out = np.zeros(n)
    t_out = np.zeros(n)
    inside = np.zeros(n, dtype=bool)
    for p, path in enumerate(paths):
        for k in range(len(path.a)):
            rel = px - path.a[k]
            u = rel @ path.dir[k]
            t = rel @ path.normal[k]
            clamped = np.clip(u, 0.0, path.seg_len[k])
            dist = np.hypot(u - clamped, t)
            closer = dist < best
            best[closer] = dist[closer]
            which[closer] = p
            s_out[closer] = path.start[k] + clamped[closer]
            t_out[closer] = t[closer]
            inside[closer] = (u[closer] > 0) & (u[closer] < path.seg_len[k])
    ok = inside & (best <= half_width)
    for p, path in enumerate(paths):
        mine = ok & (which == p)
        if not path.closed:
            mine &= (s_out > END_MARGIN) & (s_out < path.length - END_MARGIN)
        if len(path.sharp):
            gap = np.abs(s_out[:, None] - path.sharp[None, :])
            if path.closed:
                gap = np.minimum(gap, path.length - gap)
            mine &= gap.min(axis=1) > BEND_MARGIN
        ok[which == p] = mine[which == p]
    which[~ok] = -1
    return which, s_out, t_out


def polygon_mask(shape, rings):
    """Pixels inside the polygons whose rings (closed paths, image pixels)
    are given: even-odd, so holes stay out."""
    from PIL import Image, ImageDraw  # pylint: disable=import-outside-toplevel
    h, w = shape
    mask = np.zeros(shape, dtype=bool)
    for points, closed in rings:
        if not closed or len(points) < 3:
            continue
        image = Image.new("1", (w, h), 0)
        ImageDraw.Draw(image).polygon([(x - 0.5, y - 0.5) for x, y in points], fill=1)
        mask ^= np.asarray(image, dtype=bool)
    return mask


def _shifted(mask, dx, dy):
    """``mask`` moved by (dx, dy) pixels (x right, y down), False shifted in:
    out[y, x] = mask[y - dy, x - dx]."""
    h, w = mask.shape
    out = np.zeros_like(mask)
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = mask[ys, xs]
    return out


def fill_misses(ink_a, ink_b, grow_a, grow_b, region, window=FILL_WINDOW,
                max_shift=FILL_MAX_SHIFT):
    """Ink of each image within ``region`` (a fill's inside) unmatched
    (image masks: QGIS's, the browser's) when the browser image is moved by
    the best shift of each ``window`` px square (at most ``max_shift`` px
    each way): a pattern laid out from another origin matches, one of
    another spacing, size or density does not."""
    a, b = ink_a & region, ink_b & region
    h, w = region.shape
    if not a.any() and not b.any():
        return np.zeros_like(a), np.zeros_like(b)
    rows, cols = -(-h // window), -(-w // window)

    def window_sums(mask):
        padded = np.zeros((rows * window, cols * window), dtype=np.int32)
        padded[:h, :w] = mask
        return padded.reshape(rows, window, cols, window).sum(axis=(1, 3))

    def misses(dx, dy):
        # QGIS ink looks for browser ink (dx, dy) away; browser ink back.
        return a & ~_shifted(grow_b, -dx, -dy), b & ~_shifted(grow_a, dx, dy)
    shifts = [(0, 0)] + [(dx, dy) for dy in range(-max_shift, max_shift + 1)
                         for dx in range(-max_shift, max_shift + 1) if dx or dy]
    best = np.full((rows, cols), np.iinfo(np.int32).max, dtype=np.int64)
    choice = np.zeros((rows, cols), dtype=int)
    for index, (dx, dy) in enumerate(shifts):
        miss_a, miss_b = misses(dx, dy)
        sums = window_sums(miss_a) + window_sums(miss_b)
        better = sums < best
        best[better] = sums[better]
        choice[better] = index
    out_a, out_b = np.zeros_like(a), np.zeros_like(b)
    for index in np.unique(choice):
        miss_a, miss_b = misses(*shifts[index])
        cells = np.kron(choice == index, np.ones((window, window), dtype=bool))[:h, :w]
        out_a |= miss_a & cells
        out_b |= miss_b & cells
    return out_a, out_b


def unmatched_along(ink_a, ink_b, paths, half_width, max_shift=MAX_SHIFT, window=WINDOW,
                    fill=None):
    """Unmatched ink pixels of both images (int) and their total, with the
    ink near ``paths`` [(points, closed), ...] compared along the line and
    the ink in ``fill`` (a polygon's inside, image mask) with the pattern's
    best 2D shift (fill_misses); a ring's ink inside the fill matches
    either way."""
    grow_a, grow_b = grow(ink_a), grow(ink_b)
    total = int(ink_a.sum() + ink_b.sum())
    paths = [_Path(pts, closed) for pts, closed in paths if len(pts) >= 2]
    paths = [p for p in paths if p.length > 0]
    ya, xa = np.nonzero(ink_a)
    yb, xb = np.nonzero(ink_b)
    centres = np.concatenate([np.stack([xa, ya], 1), np.stack([xb, yb], 1)]).astype(float) + 0.5
    side = np.concatenate([np.zeros(len(xa), dtype=bool), np.ones(len(xb), dtype=bool)])
    if not len(centres):
        return 0, total
    miss = ~np.concatenate([grow_b[ya, xa], grow_a[yb, xb]])  # strict
    if paths:
        which, s, t = _project(centres, paths, half_width)
    else:
        which, s, t = np.full(len(centres), -1), None, None
    h, w = ink_a.shape
    shifts = np.arange(-max_shift, max_shift + 1)
    shifts = shifts[np.argsort(np.abs(shifts), kind="stable")]  # ties: the smallest shift
    for p, path in enumerate(paths):
        mine = np.nonzero(which == p)[0]
        if not len(mine):
            continue
        count = max(1, round(path.length / window))
        win = np.minimum((s[mine] / path.length * count).astype(int), count - 1)
        sm, tm, bm = s[mine], t[mine], side[mine]
        misses = np.empty((len(shifts), len(sm)), dtype=bool)
        for i, delta in enumerate(shifts):
            # QGIS ink looks for browser ink delta further on; browser ink back.
            target = path.at(sm + np.where(bm, -delta, delta), tm)
            x = np.floor(target[:, 0]).astype(int)
            y = np.floor(target[:, 1]).astype(int)
            valid = (x >= 0) & (x < w) & (y >= 0) & (y < h)
            xc, yc = np.clip(x, 0, w - 1), np.clip(y, 0, h - 1)
            hit = np.where(bm, grow_a[yc, xc], grow_b[yc, xc]) & valid
            misses[i] = ~hit
        for k in range(count):
            in_win = win == k
            if in_win.any():
                best = misses[:, in_win].sum(axis=1).argmin()
                miss[mine[in_win]] = misses[best, in_win]
    if fill is not None:
        xi, yi = centres[:, 0].astype(int), centres[:, 1].astype(int)
        in_fill = fill[yi, xi]
        region = np.zeros_like(fill)
        region[yi[in_fill], xi[in_fill]] = True
        fill_a, fill_b = fill_misses(ink_a, ink_b, grow_a, grow_b, region)
        by_fill = np.where(side, fill_b[yi, xi], fill_a[yi, xi])
        along_line = which >= 0
        miss = np.where(in_fill & along_line, miss & by_fill,
                        np.where(in_fill, by_fill, miss))
    return int(miss.sum()), total


def along_score(ink_a, ink_b, paths, geometry):
    """Share of ink unmatched along the lines of a ``geometry`` style
    ("LineString", "Polygon"); None for points."""
    if geometry not in ("LineString", "Polygon"):
        return None
    half = LINE_HALF_WIDTH if geometry == "LineString" else RING_HALF_WIDTH
    fill = polygon_mask(ink_a.shape, paths) if geometry == "Polygon" else None
    unmatched, total = unmatched_along(ink_a, ink_b, paths, half, fill=fill)
    return round(unmatched / total, 4) if total else 0.0


def view_paths(map_paths, center, zoom, size, earth):
    """Map-unit paths [(points, closed)] as image pixels of the view of
    ``size`` px centred on ``center`` at ``zoom`` (512 px tiles)."""
    resolution = earth / (512 * 2 ** zoom)
    out = []
    for points, closed in map_paths:
        out.append(([((x - center[0]) / resolution + size / 2,
                      (center[1] - y) / resolution + size / 2) for x, y in points], closed))
    return out
