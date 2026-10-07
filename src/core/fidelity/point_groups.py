"""
point_groups.py

QGIS point cluster / point displacement renderers (QgsPointDistanceRenderer)
at one map scale, in plain Python.

Grouping (QgsPointDistanceRenderer::renderFeature): points are taken in
drawing order; a point joins the group whose *first* point lies within the
search square (+- tolerance) and whose running centroid is nearest, else it
starts a new group. Groups are drawn at the centroid of their points.

Displacement (QgsPointDisplacementRenderer::calculateSymbolAndLabelPositions):
the members of a group of two or more are placed around the centroid on a
ring, concentric rings or a grid. Painter coordinates have y down; positions
here are map coordinates (y up): a painter shift (dx, dy) is (dx, -dy).
"""

import math
from typing import Dict, List, Sequence, Tuple

Point = Tuple[float, float]

RING, CONCENTRIC_RINGS, GRID = 0, 1, 2   # QgsPointDisplacementRenderer::Placement


def group_points(points: Sequence[Point], tolerance: float) -> List[List[int]]:
    """Indexes of ``points`` per group, groups in creation order."""
    seeds: Dict[Tuple[int, int], List[int]] = {}   # grid cell -> group indexes (by seed)
    seed_at: List[Point] = []
    location: List[Point] = []
    groups: List[List[int]] = []
    cell = tolerance if tolerance > 0 else 1.0
    for index, (x, y) in enumerate(points):
        cx, cy = int(math.floor(x / cell)), int(math.floor(y / cell))
        candidates = []
        if tolerance > 0:
            for gx in (cx - 1, cx, cx + 1):
                for gy in (cy - 1, cy, cy + 1):
                    for group in seeds.get((gx, gy), ()):
                        sx, sy = seed_at[group]
                        if abs(sx - x) <= tolerance and abs(sy - y) <= tolerance:
                            candidates.append(group)
        if not candidates:
            seeds.setdefault((cx, cy), []).append(len(groups))
            seed_at.append((x, y))
            location.append((x, y))
            groups.append([index])
            continue
        best = min(sorted(candidates),
                   key=lambda g: (location[g][0] - x) ** 2 + (location[g][1] - y) ** 2)
        n = len(groups[best])
        lx, ly = location[best]
        location[best] = ((lx * n + x) / (n + 1), (ly * n + y) / (n + 1))
        groups[best].append(index)
    return groups


def centroid(points: Sequence[Point], members: Sequence[int]) -> Point:
    return (sum(points[i][0] for i in members) / len(members),
            sum(points[i][1] for i in members) / len(members))


def displaced(center: Point, count: int, placement: int, symbol_diagonal: float,
              center_diagonal: float, addition: float):
    """(member positions, circle radius or None, grid size or None), map
    units, as QGIS places ``count`` members around ``center``."""
    cx, cy = center
    positions: List[Point] = []
    if placement == RING:
        radius = max(symbol_diagonal / 2, count * symbol_diagonal / (2 * math.pi)) + addition
        step = 2 * math.pi / count
        for k in range(count):
            angle = k * step
            positions.append((cx + radius * math.sin(angle), cy - radius * math.cos(angle)))
        return positions, radius, None
    if placement == CONCENTRIC_RINGS:
        first = center_diagonal / 2 + symbol_diagonal / 2
        remaining, ring, radius = count, 1, 0.0
        while remaining > 0:
            radius = max(first + (ring - 1) * symbol_diagonal + ring * addition, 0.0)
            capacity = max(int(math.floor(2 * math.pi * radius / symbol_diagonal)), 1) \
                if symbol_diagonal > 0 else remaining
            here = min(capacity, remaining)
            step = 2 * math.pi / here
            for k in range(here):
                angle = k * step
                positions.append((cx + radius * math.sin(angle), cy - radius * math.cos(angle)))
            remaining -= here
            ring += 1
        return positions, radius, None
    size = int(math.ceil(math.sqrt(count)))
    if count - (size - 1) ** 2 < size:
        size -= 1
    size = max(size, 1)
    step = ((center_diagonal / 2 + symbol_diagonal / 2) + symbol_diagonal) / 2 + addition
    shift = step * (size - 1) / 2
    row = 0
    while len(positions) < count:
        for column in range(size):
            if len(positions) >= count:
                break
            positions.append((cx + step * column - shift, cy - (step * row - shift)))
        row += 1
    return positions, None, size


def grid_lines(positions: Sequence[Point], size: int) -> List[Tuple[Point, Point]]:
    """The segments QGIS draws between displaced grid members
    (QgsPointDisplacementRenderer::drawGrid): each member to the next one in
    its row and to the one below it."""
    if len(positions) < 2 or size < 1:
        return []
    lines = []
    for i, point in enumerate(positions):
        if i + 1 < len(positions) and (i + 1) % size != 0:
            lines.append((point, positions[i + 1]))
        if i + size < len(positions):
            lines.append((point, positions[i + size]))
    return lines
