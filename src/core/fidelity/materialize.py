"""
materialize.py

Geometry recipes: symbol components whose *position or shape* matters are
exported as derived MVT features instead of browser-side approximations.

Recipes are plain, immutable data built on the caller thread and executed
file → file by the rule exporter's workers (QGIS processing algorithms and
geometry expressions). All geometry is derived from complete original
features *before* tiling, so positions do not depend on tile clipping or
simplification.

* ``marker_points`` — exact marker-line positions (vertices, first/last
  vertex, inner vertices, central point, segment centres) with the line
  azimuth at each position in ``ANGLE_FIELD``.
* ``hatch_lines``  — map-unit line hatches: parallel lines ``n·x = p + k·d``
  built in the project CRS from the QGIS anchor and clipped to the polygon
  (holes and multiparts preserved by the intersection).
"""

import math
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

ANGLE_FIELD = "q2vt_mat_angle"
ORDINAL_FIELD = "q2vt_mat_ordinal"
LENGTH_FIELD = "q2vt_mat_length"
COUNT_FIELD = "q2vt_mat_npoints"
BAND_FIELD = "q2vt_mat_band"        # colour band of a gradient / shapeburst fill
COLOR_FIELD = "q2vt_mat_color"      # its colour ("r,g,b,a", QgsSymbolLayerUtils.encodeColor)

VERTEX_PLACEMENTS = frozenset({"Vertex", "InnerVertices", "FirstVertex", "LastVertex",
                               "CurvePoint"})
POINT_PLACEMENTS = VERTEX_PLACEMENTS | {"CentralPoint", "SegmentCenter"}

# Hatch budget: lines per feature before the recipe refuses (reported).
MAX_HATCH_LINES = 20000


@dataclass(frozen=True)
class Recipe:
    kind: str                                   # "marker_points" | "hatch_lines"
    placements: Tuple[str, ...] = ()
    params: Tuple[Tuple[str, object], ...] = field(default_factory=tuple)

    def param(self, name: str, default=None):
        return dict(self.params).get(name, default)


def marker_points(placements, offset: float = 0.0, crs: str = "") -> Recipe:
    """Marker positions; ``offset`` (map units, right of the line) offsets the
    line in ``crs`` before positions are computed, as QGIS does."""
    params = (("offset", float(offset)), ("crs", crs)) if abs(offset) > 1e-9 else ()
    return Recipe("marker_points", tuple(sorted(set(placements) & POINT_PLACEMENTS)), params)


def interval_points(interval: float, along: float = 0.0, offset: float = 0.0,
                    crs: str = "") -> Recipe:
    """Interval marker positions (map units): ``along + k * interval`` from the
    start of every (offset) line, like ``renderPolylineInterval``."""
    params = [("interval", float(interval)), ("along", float(along)), ("crs", crs)]
    if abs(offset) > 1e-9:
        params.append(("offset", float(offset)))
    return Recipe("marker_points", ("Interval",), tuple(params))


def interval_points_expression(recipe: Recipe, export_crs: str = "EPSG:3857") -> str:
    """Multipoint of interval marker positions with the line azimuth as Z.

    Positions are measured in the recipe CRS (the project's map units). A
    closed ring does not repeat the marker at its start point.
    """
    interval = float(recipe.param("interval"))
    along = float(recipe.param("along", 0.0))
    average = float(recipe.param("average", 0.0) or 0.0)
    crs = recipe.param("crs") or export_crs

    def angle(line):
        """Marker azimuth: QGIS averages the direction over ``average``
        (``averageAngleLength``) centred on the marker, i.e. +-average / 2
        along the line, wrapping around closed rings."""
        if average <= 0:
            return f"line_interpolate_angle({line}, @q2vt_d)"
        half = average / 2.0

        def at(distance):
            return (f"line_interpolate_point({line}, if(is_closed({line}), "
                    f"({distance} + @q2vt_len) % @q2vt_len, "
                    f"max(0, min(@q2vt_len, {distance}))))")
        return (f"coalesce(degrees(azimuth({at(f'@q2vt_d - {half!r}')}, "
                f"{at(f'@q2vt_d + {half!r}')})), line_interpolate_angle({line}, @q2vt_d))")

    def body(line):
        return (
            f"with_variable('q2vt_len', length({line}), with_variable('q2vt_off', "
            f"if(is_closed({line}) AND {along!r} < 0, @q2vt_len - (({-along!r}) % @q2vt_len), "
            f"if(is_closed({line}), {along!r} % @q2vt_len, {along!r})), "
            f"if(@q2vt_off > @q2vt_len OR @q2vt_off < 0, NULL, collect_geometries(array_foreach("
            f"array_filter(generate_series(0, floor((@q2vt_len - @q2vt_off) / {interval!r} + 1e-9)), "
            f"NOT (is_closed({line}) AND @element > 0 AND "
            f"abs(@q2vt_off + @element * {interval!r} - @q2vt_len) < 1e-6)), "
            f"with_variable('q2vt_d', min(@q2vt_off + @element * {interval!r}, @q2vt_len), "
            f"with_variable('q2vt_p', line_interpolate_point({line}, @q2vt_d), "
            f"make_point(x(@q2vt_p), y(@q2vt_p), {angle(line)}))))))))")
    if crs == export_crs:
        return body("@geometry")
    # Positions and azimuths in the project CRS (QGIS draws in it); the small
    # grid convergence to Web Mercator north is ignored.
    return (f"with_variable('q2vt_pts', transform(with_variable('q2vt_l', transform(@geometry, "
            f"'{export_crs}', '{crs}'), {body('@q2vt_l')}), '{crs}', '{export_crs}'), "
            f"@q2vt_pts)")


def offset_line_expression(recipe: Recipe, export_crs: str = "EPSG:3857") -> str:
    """Offset a line to the right by the recipe offset, measured in its CRS."""
    crs = recipe.param("crs") or export_crs
    geom = "@geometry" if crs == export_crs else f"transform(@geometry, '{export_crs}', '{crs}')"
    # offset_curve(geom, distance, segments, join=2 (miter), miter_limit=2)
    # mirrors QgsSymbolLayerUtils::offsetLine. Positive distances are to the
    # left. Some GEOS versions
    # reverse the result for right-hand offsets; restore the direction.
    body = (
        f"with_variable('q2vt_src', {geom}, with_variable('q2vt_off', "
        f"offset_curve(@q2vt_src, {-recipe.param('offset', 0.0)!r}, 8, 2, 2), "
        f"if(distance(start_point(@q2vt_off), start_point(@q2vt_src)) > "
        f"distance(end_point(@q2vt_off), start_point(@q2vt_src)), "
        f"reverse(@q2vt_off), @q2vt_off)))"
    )
    return body if crs == export_crs else f"transform({body}, '{crs}', '{export_crs}')"


def vertex_filter(placement: str) -> Optional[str]:
    """Filter over ``native:extractvertices`` output for one placement.

    Requires ``LENGTH_FIELD``/``COUNT_FIELD`` computed on the original line.
    """
    return {
        "Vertex": "TRUE",
        "CurvePoint": "TRUE",
        "FirstVertex": '"vertex_index" = 0',
        "LastVertex": f'"vertex_index" = "{COUNT_FIELD}" - 1',
        "InnerVertices": f'"vertex_index" > 0 AND "vertex_index" < "{COUNT_FIELD}" - 1',
    }.get(placement)


def marker_rotation_expression(base_angle: float) -> str:
    """QGIS marker angle for a line azimuth (clockwise from north).

    A marker at angle 0 on an eastward line (azimuth 90°) is unrotated, so
    the rotation is ``azimuth - 90 + base``. Verified against QGIS marker
    lines in ``tests/integration/test_materialize.py``.
    """
    return f'"{ANGLE_FIELD}" - 90 + {float(base_angle)!r}'


def hatch_recipe(angle_deg: float, spacing: float, offset: float,
                 construction_crs: str, anchor: str) -> Recipe:
    return Recipe("hatch_lines", (), (
        ("angle", float(angle_deg)), ("spacing", float(spacing)), ("offset", float(offset)),
        ("crs", construction_crs), ("anchor", anchor)))


def hatch_expression(recipe: Recipe, export_crs: str = "EPSG:3857") -> str:
    """Geometry expression generating the hatch lines of one polygon.

    In the construction CRS (map y up) the QGIS line angle θ is counter-
    clockwise from east: direction ``u = (cos θ, sin θ)`` and normal
    ``n = (-sin θ, cos θ)``. Lines satisfy ``n·(x - O) = k·d - p``: a
    positive QGIS offset moves lines against the normal, and the origin
    ``O`` is the feature bounding box's lower-left corner (QGIS "Feature"
    coordinate reference, measured for axis-aligned hatches) or the CRS
    origin ("Viewport", which QGIS ties to the screen and is approximated by
    a stable world origin).

    QGIS quantizes rotated map-unit hatches to the pixel grid (measured
    spacing 9.4–9.7 for a declared 10 at 1 px per unit, with a per-feature
    phase), so only angle and spacing — which are reproduced exactly — and
    the phase of axis-aligned hatches are compared against QGIS.
    """
    import math  # pylint: disable=import-outside-toplevel

    theta = math.radians(recipe.param("angle", 0.0))
    ux, uy = math.cos(theta), math.sin(theta)
    nx, ny = -math.sin(theta), math.cos(theta)
    d = recipe.param("spacing")
    p = -recipe.param("offset", 0.0)
    crs = recipe.param("crs")
    if not d or d <= 0:
        raise ValueError("Hatch spacing must be positive")
    feature_anchor = recipe.param("anchor") == "feature"
    origin_x = "x_min(@q2vt_g)" if feature_anchor else "0"
    origin_y = "y_min(@q2vt_g)" if feature_anchor else "0"
    geom = "@geometry" if crs == export_crs else f"transform(@geometry, '{export_crs}', '{crs}')"
    body = (
        f"with_variable('q2vt_g', {geom}, "
        f"with_variable('q2vt_ox', {origin_x}, with_variable('q2vt_oy', {origin_y}, "
        f"with_variable('q2vt_c', centroid(bounds(@q2vt_g)), "
        f"with_variable('q2vt_r', 0.5 * sqrt(bounds_width(@q2vt_g)^2 + bounds_height(@q2vt_g)^2) + {d!r}, "
        f"with_variable('q2vt_s0', {nx!r} * (x(@q2vt_c) - @q2vt_ox) + {ny!r} * (y(@q2vt_c) - @q2vt_oy), "
        f"with_variable('q2vt_k0', floor((@q2vt_s0 - @q2vt_r - {p!r}) / {d!r}), "
        f"with_variable('q2vt_k1', ceil((@q2vt_s0 + @q2vt_r - {p!r}) / {d!r}), "
        f"if(@q2vt_k1 - @q2vt_k0 > {MAX_HATCH_LINES}, NULL, "
        f"intersection(@q2vt_g, collect_geometries(array_foreach("
        f"generate_series(@q2vt_k0, @q2vt_k1), "
        f"with_variable('q2vt_t', {p!r} + @element * {d!r} - @q2vt_s0, "
        f"make_line("
        f"make_point(x(@q2vt_c) + {nx!r} * @q2vt_t - {ux!r} * @q2vt_r, "
        f"y(@q2vt_c) + {ny!r} * @q2vt_t - {uy!r} * @q2vt_r), "
        f"make_point(x(@q2vt_c) + {nx!r} * @q2vt_t + {ux!r} * @q2vt_r, "
        f"y(@q2vt_c) + {ny!r} * @q2vt_t + {uy!r} * @q2vt_r)))))))))))))))"
    )
    if crs == export_crs:
        return body
    return f"transform({body}, '{crs}', '{export_crs}')"


def recipe_to_dict(recipe: Optional[Recipe]) -> Optional[Dict]:
    if recipe is None:
        return None
    return {"kind": recipe.kind, "placements": list(recipe.placements),
            "params": dict(recipe.params)}


# Grid cells one feature (or piece) may generate; the materializer's pattern
# budget keeps exports below it, so a grid is never silently dropped.
MAX_GRID_POINTS = 2000000


def grid_recipe(dx: float, dy: float, disp_x: float, disp_y: float, off_x: float,
                off_y: float, construction_crs: str, anchor: str, inset: float = 0.0,
                rows_from_top: bool = False, segments=(), clip_shape: bool = False,
                clip_mode: str = "", deviation=(0.0, 0.0), seed: int = 0, paths=(),
                fill: bool = False, stroke=None) -> Recipe:
    """Point-pattern grid in map units (see :func:`grid_expression`)."""
    params = [
        ("dx", float(dx)), ("dy", float(dy)), ("disp_x", float(disp_x)),
        ("disp_y", float(disp_y)), ("off_x", float(off_x)), ("off_y", float(off_y)),
        ("crs", construction_crs), ("anchor", anchor), ("inset", float(inset)),
        ("top", bool(rows_from_top))]
    if deviation[0] or deviation[1]:
        params += [("dev_x", float(deviation[0])), ("dev_y", float(deviation[1])),
                   ("seed", int(seed))]
    if paths:
        params += [("paths", tuple(tuple(float(v) for v in path) for path in paths)),
                   ("fill", bool(fill)), ("clip_shape", bool(clip_shape))]
        if clip_mode:
            params.append(("clip_mode", clip_mode))
    elif segments:
        params += [("segments", tuple(segments)), ("clip_shape", bool(clip_shape))]
        if clip_mode:
            params.append(("clip_mode", clip_mode))
        if stroke:  # (half width, cap, join): the stroke as polygons
            params.append(("stroke", (float(stroke[0]), str(stroke[1]), str(stroke[2]))))
    return Recipe("grid_points", (), tuple(params))


def random_points_recipe(count: int, density_area: float, seed: int,
                         construction_crs: str) -> Recipe:
    """Random marker fill (see :func:`random_points_expression`);
    ``density_area`` 0 means an absolute count per feature."""
    return Recipe("random_points", (), (("count", int(count)), ("density", float(density_area)),
                                        ("seed", int(seed)), ("crs", construction_crs)))


def random_points_expression(recipe: Recipe, export_crs: str = "EPSG:3857") -> str:
    """Random points of a ``QgsRandomMarkerFillSymbolLayer``.

    QGIS draws ``count`` points, or ``ceil(count * area / densityArea)`` with
    a density-based count, uniformly in the polygon (holes excluded) from a
    seeded generator in *screen* coordinates, so its exact positions change
    with the view. Here the count follows QGIS and the positions are seeded
    per feature (``randf`` with a seed is deterministic), drawn in the
    bounding box and kept when inside (rejection sampling).
    """
    p = recipe.param
    crs = p("crs")
    geom = "@geometry" if crs == export_crs else f"transform(@geometry, '{export_crs}', '{crs}')"
    count, density = p("count"), p("density")
    n = f"ceil({count} * area(@q2vt_g) / {density!r})" if density else str(count)
    # Enough draws from the bounding box to leave n points inside the polygon.
    tries = (f"min({MAX_GRID_POINTS}, ceil(@q2vt_n * area(bounds(@q2vt_g)) / "
             f"max(area(@q2vt_g), 1e-12) * 1.3) + 20)")
    seed = f"{p('seed')} + abs(floor(x_min(@q2vt_g) * 7 + y_min(@q2vt_g) * 13)) % 1000003 * 4"
    draw = "@q2vt_s[4] + 2 * @element"
    body = (
        f"with_variable('q2vt_g', {geom}, "
        f"with_variable('q2vt_n', {n}, "
        f"with_variable('q2vt_s', array(x_min(@q2vt_g), y_min(@q2vt_g), "
        f"x_max(@q2vt_g) - x_min(@q2vt_g), y_max(@q2vt_g) - y_min(@q2vt_g), {seed}), "
        f"if(@q2vt_n < 1, NULL, collect_geometries(array_slice(array_filter(array_foreach("
        f"generate_series(0, {tries} - 1), "
        f"make_point(@q2vt_s[0] + randf(0, @q2vt_s[2], {draw}), "
        f"@q2vt_s[1] + randf(0, @q2vt_s[3], {draw} + 1))), "
        f"intersects(@element, @q2vt_g)), 0, @q2vt_n - 1))))))"
    )
    if crs == export_crs:
        return body
    return f"transform({body}, '{crs}', '{export_crs}')"


# Stroke-only simple marker shapes (QgsSimpleMarkerSymbolLayerBase::
# prepareMarkerPath): unit-path segments, y pointing down, scaled by size / 2.
STROKE_MARKER_PATHS = {
    "Line": (((0, -1), (0, 1)),),
    "Cross": (((-1, 0), (1, 0)), ((0, -1), (0, 1))),
    "Cross2": (((-1, -1), (1, 1)), ((1, -1), (-1, 1))),
    "ArrowHead": (((-1, -1), (0, 0)), ((0, 0), (-1, 1))),
}


# Closed simple-marker shapes (QgsSimpleMarkerSymbolLayerBase::shapeToPolygon),
# unit coordinates, y pointing down; the circle is QPainterPath::addEllipse.
MARKER_SHAPE_POLYGONS = {
    "Square": ((-1, -1), (1, -1), (1, 1), (-1, 1), (-1, -1)),
    "Diamond": ((-1, 0), (0, 1), (1, 0), (0, -1), (-1, 0)),
    "Triangle": ((-1, 1), (1, 1), (0, -1), (-1, 1)),
    "EquilateralTriangle": ((-0.8660, 0.5), (0.8660, 0.5), (0, -1), (-0.8660, 0.5)),
    "Pentagon": ((-0.9511, -0.3090), (-0.5878, 0.8090), (0.5878, 0.8090), (0.9511, -0.3090),
                 (0, -1), (-0.9511, -0.3090)),
    "Hexagon": ((-0.8660, -0.5), (-0.8660, 0.5), (0, 1), (0.8660, 0.5), (0.8660, -0.5),
                (0, -1), (-0.8660, -0.5)),
    "HalfSquare": ((-1, -1), (0, -1), (0, 1), (-1, 1), (-1, -1)),
    "QuarterSquare": ((-1, -1), (0, -1), (0, 0), (-1, 0), (-1, -1)),
    "DiagonalHalfSquare": ((-1, -1), (1, 1), (-1, 1), (-1, -1)),
    "Circle": tuple((math.cos(2 * math.pi * k / 64), math.sin(2 * math.pi * k / 64))
                    for k in range(65)),
}
_OCT = 1.0 / (1 + math.sqrt(2))
MARKER_SHAPE_POLYGONS["Octagon"] = ((-_OCT, 1), (_OCT, 1), (1, _OCT), (1, -_OCT), (_OCT, -1),
                                    (-_OCT, -1), (-1, -_OCT), (-1, _OCT), (-_OCT, 1))


def marker_paths(unit_paths, size: float, angle: float = 0.0,
                 offset_x: float = 0.0, offset_y: float = 0.0):
    """Flat (x, y, ...) paths of a marker in map units around its point (map
    y up): unit paths scaled by ``size / 2``, rotated clockwise on screen by
    ``angle`` and offset like :func:`marker_segments`."""
    radians = math.radians(angle)
    cos, sin = math.cos(radians), math.sin(radians)

    def screen(x, y):  # rotate on screen (y down), then flip to map y up
        return x * cos - y * sin, -(x * sin + y * cos)
    ox, oy = screen(offset_x, offset_y)
    half = size / 2.0
    out = []
    for path in unit_paths:
        flat = []
        for ux, uy in path:
            x, y = screen(ux * half, uy * half)
            flat += [round(x + ox, 9), round(y + oy, 9)]
        out.append(tuple(flat))
    return tuple(out)


def marker_segments(shape: str, size: float, angle: float = 0.0,
                    offset_x: float = 0.0, offset_y: float = 0.0):
    """Segments of a stroke-only marker in map units relative to its point
    (map y up): the path is scaled by ``size / 2``, rotated clockwise on
    screen by ``angle`` and moved by the rotated offset, as
    ``QgsSimpleMarkerSymbolLayer::renderPoint`` draws it."""
    radians = math.radians(angle)
    cos, sin = math.cos(radians), math.sin(radians)

    def screen(x, y):  # rotate on screen (y down), then flip to map y up
        return x * cos - y * sin, -(x * sin + y * cos)
    ox, oy = screen(offset_x, offset_y)
    half = size / 2.0
    segments = []
    for (x1, y1), (x2, y2) in STROKE_MARKER_PATHS[shape]:
        ax, ay = screen(x1 * half, y1 * half)
        bx, by = screen(x2 * half, y2 * half)
        segments.append((ax + ox, ay + oy, bx + ox, by + oy))
    return tuple(segments)


# Grid anchor of the whole feature, kept when a polygon is cut into pieces.
ANCHOR_X_FIELD = "q2vt_anchor_x"
ANCHOR_Y_FIELD = "q2vt_anchor_y"
# Pieces of at most this many pattern cells per side (and PIECE_MAX_NODES
# vertices): clipping and point-in-polygon tests stay cheap and no feature
# needs more than MAX_GRID_POINTS grid cells.
PIECE_CELLS = 100
PIECE_MAX_NODES = 256


def grid_splittable(recipe: Recipe) -> bool:
    """Whether a grid gives the same result computed piece by piece: markers
    clipped to the shape, or kept when their centre is inside. (Kept when
    completely inside / touching, or tested on a shrunken polygon, depends on
    the whole polygon.)"""
    if recipe.kind != "grid_points" or recipe.param("anchor") != "feature":
        return False
    if recipe.param("inset"):
        return False
    mode = recipe.param("clip_mode") or ("shape" if recipe.param("clip_shape") else "centroid")
    return mode in ("shape", "centroid")


def grid_anchor_expressions(recipe: Recipe, export_crs: str = "EPSG:3857"):
    """``(x, y)`` expressions of a feature's grid anchor in the recipe CRS."""
    crs = recipe.param("crs")
    geom = "@geometry" if crs == export_crs else f"transform(@geometry, '{export_crs}', '{crs}')"
    top = bool(recipe.param("top"))
    return f"x_min({geom})", (f"y_max({geom})" if top else f"y_min({geom})")


def piece_cut_expression(recipe: Recipe, export_crs: str = "EPSG:3857") -> str:
    """The feature cut into square pieces of ``PIECE_CELLS`` pattern cells (in
    the recipe CRS; the tile origin is offset by an irrational fraction so
    no grid node falls on a cut)."""
    crs = recipe.param("crs")
    geom = "@geometry" if crs == export_crs else f"transform(@geometry, '{export_crs}', '{crs}')"
    side = PIECE_CELLS * max(float(recipe.param("dx")), float(recipe.param("dy")))
    shift = side * 0.3183098861837907  # 1/pi
    tile = (f"make_rectangle_3points("
            f"make_point({shift!r} + (@q2vt_t[0] + @element % @q2vt_t[2]) * {side!r}, "
            f"{shift!r} + (@q2vt_t[1] + floor(@element / @q2vt_t[2])) * {side!r}), "
            f"make_point({shift!r} + (@q2vt_t[0] + @element % @q2vt_t[2] + 1) * {side!r}, "
            f"{shift!r} + (@q2vt_t[1] + floor(@element / @q2vt_t[2])) * {side!r}), "
            f"make_point({shift!r} + (@q2vt_t[0] + @element % @q2vt_t[2] + 1) * {side!r}, "
            f"{shift!r} + (@q2vt_t[1] + floor(@element / @q2vt_t[2]) + 1) * {side!r}))")
    body = (
        f"with_variable('q2vt_c', {geom}, "
        f"with_variable('q2vt_t', array(floor((x_min(@q2vt_c) - {shift!r}) / {side!r}), "
        f"floor((y_min(@q2vt_c) - {shift!r}) / {side!r}), "
        f"floor((x_max(@q2vt_c) - {shift!r}) / {side!r}) - "
        f"floor((x_min(@q2vt_c) - {shift!r}) / {side!r}) + 1, "
        f"floor((y_max(@q2vt_c) - {shift!r}) / {side!r}) - "
        f"floor((y_min(@q2vt_c) - {shift!r}) / {side!r}) + 1), "
        f"if(@q2vt_t[2] * @q2vt_t[3] <= 1, @q2vt_c, "
        f"collect_geometries(array_filter(array_foreach(generate_series(0, "
        f"@q2vt_t[2] * @q2vt_t[3] - 1), intersection(@q2vt_c, {tile})), "
        f"@element IS NOT NULL AND NOT is_empty(@element))))))"
    )
    return body if crs == export_crs else f"transform({body}, '{crs}', '{export_crs}')"


def grid_expression(recipe: Recipe, export_crs: str = "EPSG:3857") -> str:
    """Marker positions of a QGIS point-pattern fill, clipped to the polygon.

    Measured QGIS behaviour ("Feature" coordinate reference):

    * "Shape" clipping (texture brush): grid origin at the bounding box's
      lower-left corner, rows counted upwards;
    * the other clip modes: origin at the upper-left corner, rows counted
      downwards;
    * ``x = x0 + offX + i·dx`` (+ ``dispX`` on odd rows), ``y`` moves *down*
      by ``offY``; columns move *up* by ``dispY`` — odd columns for "Shape",
      even columns for the other modes.

    A marker is kept when its centre lies in the polygon shrunk by ``inset``
    (the marker radius for "completely within").

    With ``segments`` (stroke-only markers, see :func:`marker_segments`) the
    result is the markers' line work: clipped to the polygon for "Shape"
    clipping (``clip_shape``), otherwise whole markers whose centre is inside.
    """
    p = recipe.param
    dx, dy = p("dx"), p("dy")
    if not dx or not dy or dx <= 0 or dy <= 0:
        raise ValueError("Grid distances must be positive")
    crs = p("crs")
    geom = "@geometry" if crs == export_crs else f"transform(@geometry, '{export_crs}', '{crs}')"
    feature = p("anchor") == "feature"
    top = bool(p("top")) and feature
    x0_expr = "x_min(@q2vt_g)" if feature else "0"
    y0_expr = ("y_max(@q2vt_g)" if top else "y_min(@q2vt_g)") if feature else "0"
    if feature and p("anchor_fields"):
        # A piece of a larger polygon: the grid stays anchored to the whole
        # feature (see grid_anchor_expressions).
        x0_expr, y0_expr = f'"{ANCHOR_X_FIELD}"', f'"{ANCHOR_Y_FIELD}"'
    sy = -1.0 if top else 1.0
    # Measured: with rows counted from the top, even columns are displaced.
    col_parity = 0 if top else 1
    ox, ddx, ddy = p("off_x"), p("disp_x"), p("disp_y")
    oy = -p("off_y")
    clip = "@q2vt_g" if not p("inset") else f"buffer(@q2vt_g, {-p('inset')!r})"
    segments = p("segments") or ()
    # Closed marker shapes: flat (x, y, x, y, ...) paths, drawn as outlines
    # or (``fill``) as polygons.
    paths = [list(zip(path[0::2], path[1::2])) for path in (p("paths") or ())]
    if paths:
        segments = [(min(x for x, _ in pts), min(y for _, y in pts),
                     max(x for x, _ in pts), max(y for _, y in pts)) for pts in paths]
    # Markers whose centre lies outside the polygon can still reach into it.
    reach = max((max(math.hypot(ax, ay), math.hypot(bx, by)) for ax, ay, bx, by in segments),
                default=0.0)
    reach += max(abs(p("dev_x") or 0.0), abs(p("dev_y") or 0.0))  # deviated markers
    reach += (p("stroke") or (0.0,))[0] * math.sqrt(2)  # a wide stroke's square cap
    margin_i = int(math.ceil(reach / dx)) + 1
    margin_j = int(math.ceil(reach / dy)) + 1
    # QGIS parses deeply nested expressions very slowly (depth 16 costs
    # ~0.1 s, each extra level ~4x more): intermediate values live in arrays
    # to keep the nesting shallow.
    x0, y0 = "@q2vt_o[0]", "@q2vt_o[1]"
    lo, hi = "y_min(@q2vt_g)", "y_max(@q2vt_g)"
    rows = (f"min(({lo} - {y0}) * {sy!r}, ({hi} - {y0}) * {sy!r})",
            f"max(({lo} - {y0}) * {sy!r}, ({hi} - {y0}) * {sy!r})")
    ranges = (f"array(floor((x_min(@q2vt_g) - {x0} - abs({ddx!r})) / {dx!r}) - {margin_i}, "
              f"ceil((x_max(@q2vt_g) - {x0} + abs({ddx!r})) / {dx!r}) + {margin_i}, "
              f"floor({rows[0]} / {dy!r}) - {margin_j}, ceil({rows[1]} / {dy!r}) + {margin_j})")
    i, j = "@q2vt_ij[0]", "@q2vt_ij[1]"
    point_x = f"{x0} + {i} * {dx!r} + if(abs({j} % 2) = 1, {ddx!r}, 0)"
    point_y = f"{y0} + {sy!r} * {j} * {dy!r} + if(abs({i} % 2) = {col_parity}, {ddy!r}, 0)"
    dev_x, dev_y = p("dev_x") or 0.0, p("dev_y") or 0.0
    if dev_x or dev_y:
        # Random deviation (QgsPointPatternFillSymbolLayer::renderPolygon):
        # each marker moves by a uniform ±max; QGIS draws from one seeded
        # sequence in screen order, here the draw is seeded per grid cell.
        cell = f"(abs({i} * 100003 + {j} * 7919 + {int(p('seed') or 0) % 1000003}) * 2)"
        point_x = f"({point_x}) + (2 * randf(0, 1, {cell}) - 1) * {dev_x!r}"
        point_y = f"({point_y}) + (2 * randf(0, 1, {cell} + 1) - 1) * {dev_y!r}"
    per_point = max(1, len(segments))
    if segments:
        def pick(values):
            return f"array({', '.join(repr(float(v)) for v in values)})[@q2vt_ij[2]]"
        if paths:
            def shape(pts):
                line = "make_line(" + ", ".join(
                    f"make_point(@q2vt_p[0] + {x!r}, @q2vt_p[1] + {y!r})" for x, y in pts) + ")"
                return f"make_polygon({line})" if p("fill") else line
            element = (f"with_variable('q2vt_p', array({point_x}, {point_y}), "
                       f"array({', '.join(shape(pts) for pts in paths)})[@q2vt_ij[2]])")
        else:
            ax, ay, bx, by = (pick([seg[k] for seg in segments]) for k in range(4))
            element = (f"with_variable('q2vt_p', array({point_x}, {point_y}), "
                       f"make_line(make_point(@q2vt_p[0] + {ax}, @q2vt_p[1] + {ay}), "
                       f"make_point(@q2vt_p[0] + {bx}, @q2vt_p[1] + {by})))")
        stroke = p("stroke")
        if stroke:
            # A wide map-unit stroke as its outline, so clipping cuts it
            # exactly at the polygon edge like QGIS's clipped drawing (a
            # clipped centre line drawn wide overshot the edge, Csíkozás).
            element = (f"buffer({element}, {stroke[0]!r}, 8, cap:='{stroke[1]}', "
                       f"join:='{stroke[2]}')")
        # QgsPointPatternFillSymbolLayer::renderPolygon, per clip mode, with
        # the marker bounds (envelope of its line work):
        grow = stroke[0] if stroke else 0.0
        bx0 = min(min(seg[0], seg[2]) for seg in segments) - grow
        bx1 = max(max(seg[0], seg[2]) for seg in segments) + grow
        by0 = min(min(seg[1], seg[3]) for seg in segments) - grow
        by1 = max(max(seg[1], seg[3]) for seg in segments) + grow
        rect = (f"make_rectangle_3points(make_point({point_x} + {bx0!r}, {point_y} + {by0!r}), "
                f"make_point({point_x} + {bx1!r}, {point_y} + {by0!r}), "
                f"make_point({point_x} + {bx1!r}, {point_y} + {by1!r}))")
        centre = (f"make_point({point_x} + {(bx0 + bx1) / 2!r}, "
                  f"{point_y} + {(by0 + by1) / 2!r})")
        mode = p("clip_mode") or ("shape" if p("clip_shape") else "centroid")
        final = "@q2vt_all"
        if mode == "shape":  # drawing clipped to the polygon
            # Only the clipped line work / shapes: an arm that merely touches
            # the edge adds a point, which made GEOS results mixed collections.
            wanted = "Polygon" if p("fill") or p("stroke") else "Line"
            keep, final = "true", (
                f"collect_geometries(array_filter(geometries_to_array(intersection("
                f"@q2vt_g, @q2vt_all)), geometry_type(@element) = '{wanted}'))")
            if stroke:
                # Wide strokes of neighbouring markers overlap: collected they
                # make an invalid multipolygon (GEOS topology error), so they
                # are merged (buffer 0) before the clip.
                final = (
                    "collect_geometries(array_filter(geometries_to_array(intersection("
                    "@q2vt_g, buffer(@q2vt_all, 0))), geometry_type(@element) = 'Polygon'))")
        elif mode == "within":  # bounds completely inside
            keep = f"contains(@q2vt_g, {rect})"
        elif mode == "none":  # bounds touching the polygon, drawn whole
            keep = f"intersects(@q2vt_g, {rect})"
        else:  # centre of the bounds intersecting the polygon
            keep = f"intersects(@q2vt_g, {centre})"
    else:
        element = f"make_point({point_x}, {point_y})"
        keep, final = "true", f"intersection({clip}, @q2vt_all)"
    count = "(@q2vt_r[1] - @q2vt_r[0] + 1) * (@q2vt_r[3] - @q2vt_r[2] + 1)"
    body = (
        f"with_variable('q2vt_g', {geom}, "
        f"with_variable('q2vt_o', array({x0_expr} + {ox!r}, {y0_expr} + {oy!r}), "
        f"with_variable('q2vt_r', {ranges}, "
        f"if({count} > {MAX_GRID_POINTS}, NULL, "
        f"with_variable('q2vt_all', collect_geometries(array_filter(array_foreach("
        f"generate_series(0, {count} * {per_point} - 1), "
        f"with_variable('q2vt_ij', array("
        f"@q2vt_r[0] + floor(@element / {per_point}) % (@q2vt_r[1] - @q2vt_r[0] + 1), "
        f"@q2vt_r[2] + floor(floor(@element / {per_point}) / (@q2vt_r[1] - @q2vt_r[0] + 1)), "
        f"@element % {per_point}), "
        f"if({keep}, {element}, NULL))), @element IS NOT NULL)), {final})))))"
    )
    if crs == export_crs:
        return body
    return f"transform({body}, '{crs}', '{export_crs}')"


def _arc_through(a: str, b: str, c: str) -> str:
    """Segmentized circular arc through three point expressions."""
    def xy(point):
        return f"x({point}) || ' ' || y({point})"
    return (f"densify_by_count(geom_from_wkt('CircularString (' || {xy(a)} || ', ' || "
            f"{xy(b)} || ', ' || {xy(c)} || ')'), 0)")


def arrow_body_expression(curved: bool, repeated: bool, cut_start: float = 0.0,
                          cut_end: float = 0.0, geom: str = "@geometry") -> str:
    """Geometry of QGIS arrow bodies (``QgsArrowSymbolLayer``).

    * straight: from the first to the last vertex; *repeated*: one arrow per
      segment;
    * curved: a circular arc through the first, middle (index n/2) and last
      vertex; *curved repeated*: arcs through each vertex triple, a straight
      arrow for a remaining single segment.

    ``cut_start``/``cut_end`` (layer units) shorten a single arrow's body
    under its heads so a wide body does not stick out of the head's tip.
    """
    n = f"num_points({geom})"

    def point(index):
        return f"point_n({geom}, {index})"
    if repeated and curved:
        body = (f"collect_geometries(array_foreach(generate_series(0, {n} - 2, 2), "
                f"if(@element + 2 < {n}, "
                f"{_arc_through(point('@element + 1'), point('@element + 2'), point('@element + 3'))}, "
                f"make_line({point('@element + 1')}, {point('@element + 2')}))))")
    elif repeated:
        body = f"segments_to_lines({geom})"
    elif curved:
        body = (f"if({n} >= 3, {_arc_through(point(1), point(f'floor({n} / 2) + 1'), point(n))}, "
                f"{geom})")
    else:
        body = f"make_line({point(1)}, {point(n)})"  # QGIS joins first and last vertex
    if not repeated and (cut_start or cut_end):
        body = (f"with_variable('q2vt_body', {body}, line_substring(@q2vt_body, {float(cut_start)}, "
                f"max({float(cut_start)}, length(@q2vt_body) - {float(cut_end)})))")
    return body


def arrow_body_for(recipe: Recipe, export_crs: str = "EPSG:3857", cuts: bool = True) -> str:
    """``arrow_body_expression`` for a recipe, evaluated in the recipe's CRS
    (the project CRS, like QGIS) and returned in ``export_crs``."""
    def body(geom):
        return arrow_body_expression(
            bool(recipe.param("arrow_curved")), bool(recipe.param("arrow_repeated")),
            recipe.param("cut_start", 0.0) if cuts else 0.0,
            recipe.param("cut_end", 0.0) if cuts else 0.0, geom)
    crs = recipe.param("crs") or export_crs
    if crs == export_crs:
        return body("@geometry")
    return (f"transform(with_variable('q2vt_src', transform(@geometry, '{export_crs}', '{crs}'), "
            f"{body('@q2vt_src')}), '{crs}', '{export_crs}')")


def glyph_recipe(wkt: str, angle: str, construction_crs: str) -> Recipe:
    """A font marker's glyph outlines (map units around its point)."""
    return Recipe("glyph", (), (("wkt", wkt), ("angle", angle), ("crs", construction_crs)))


def glyph_expression(recipe: Recipe, export_crs: str = "EPSG:3857") -> str:
    """The glyph outlines rotated clockwise by the marker angle about the
    point and moved to it (``QgsFontMarkerSymbolLayer::renderPoint``)."""
    crs = recipe.param("crs") or export_crs
    point = "@geometry" if crs == export_crs else f"transform(@geometry, '{export_crs}', '{crs}')"
    body = (f"with_variable('q2vt_p', {point}, translate(rotate(geom_from_wkt('{recipe.param('wkt')}'), "
            f"{recipe.param('angle')}, make_point(0, 0)), x(@q2vt_p), y(@q2vt_p)))")
    return body if crs == export_crs else f"transform({body}, '{crs}', '{export_crs}')"


def dash_recipe(pattern, dash_offset: float, construction_crs: str, offset: float = 0.0,
                ring_filter: int = 0) -> Recipe:
    """Map-unit dash pattern drawn as its dashes (see :func:`dash_expression`)."""
    params = [("pattern", tuple(float(v) for v in pattern)), ("dash_offset", float(dash_offset)),
              ("crs", construction_crs), ("ring_filter", int(ring_filter))]
    if offset:
        params.append(("offset", float(offset)))
    return Recipe("dash_segments", (), tuple(params))


def dash_expression(recipe: Recipe, lines: str, export_crs: str = "EPSG:3857") -> str:
    """The dashes of a Qt dash pattern along ``lines`` (export CRS).

    Qt starts the pattern afresh on every line part and ring (QGIS draws
    each separately) at ``dash offset`` into the pattern, and continues it
    across vertices. Lengths are measured in the recipe CRS (map units).
    MapLibre instead restarts dashes wherever a tile clips the line, which
    moves them against markers placed in the gaps.
    """
    pattern = list(recipe.param("pattern"))
    if len(pattern) % 2:
        pattern += pattern
    period = sum(pattern)
    if period <= 0:
        raise ValueError("Dash pattern must have a positive length")
    starts = [sum(pattern[:i]) for i in range(0, len(pattern), 2)]
    lengths = pattern[0::2]
    count = len(starts)
    shift = float(recipe.param("dash_offset", 0.0)) % period
    crs = recipe.param("crs") or export_crs
    source = lines if crs == export_crs else f"transform({lines}, '{export_crs}', '{crs}')"

    def pick(values):
        return f"array({', '.join(repr(float(v)) for v in values)})[@element % {count}]"
    dash = (f"with_variable('q2vt_d', array(floor(@element / {count}) * {period!r} + "
            f"{pick(starts)} - {shift!r}, {pick(lengths)}), "
            f"if(@q2vt_d[0] + @q2vt_d[1] <= 0 OR @q2vt_d[0] >= @q2vt_n[1], NULL, "
            f"line_substring(@q2vt_n[0], max(0, @q2vt_d[0]), "
            f"min(@q2vt_n[1], @q2vt_d[0] + @q2vt_d[1]))))")
    per_line = (f"with_variable('q2vt_n', array(if(is_multipart(@q2vt_ls), "
                f"geometry_n(@q2vt_ls, @element), @q2vt_ls), 0), "
                f"with_variable('q2vt_n', array(@q2vt_n[0], length(@q2vt_n[0])), "
                f"collect_geometries(array_filter(array_foreach(generate_series(0, {count} * "
                f"(floor((@q2vt_n[1] + {shift!r}) / {period!r}) + 1) - 1), {dash}), "
                f"@element IS NOT NULL))))")
    # collect_geometries flattens the per-line multi-lines.
    body = (f"with_variable('q2vt_ls', {source}, collect_geometries(array_filter(array_foreach("
            f"generate_series(1, num_geometries(@q2vt_ls)), {per_line}), "
            f"@element IS NOT NULL AND NOT is_empty(@element))))")
    return body if crs == export_crs else f"transform({body}, '{crs}', '{export_crs}')"


def _ring_buffer(ring: str, distance: str) -> str:
    """One ring buffered as its own polygon (miter joins, limit 2) and returned
    as a counter-clockwise line, like ``QgsSymbolLayerUtils::offsetLine`` for
    polygons (GEOS in painter coordinates gives map-CCW shells)."""
    return (f"boundary(force_polygon_ccw(buffer(make_polygon({ring}), {distance}, 8, "
            f"'flat', 'miter', 2)))")


def polygon_offset_expression(recipe: Recipe, export_crs: str = "EPSG:3857") -> str:
    """Polygon outlines as QGIS draws them with a line symbol layer
    (``renderPolygonStroke``): the rings selected by ``ring_filter`` (0 all,
    1 exterior only, 2 interior only). With an ``offset`` every ring is
    buffered as its own polygon, so positive offsets move exterior and holes
    towards the feature's interior (``QgsSymbolLayerUtils::offsetLine``);
    ``ccw`` orients rings so MapLibre's right-hand offsets point inside."""
    offset = float(recipe.param("offset", 0.0) or 0.0)
    ring_filter = int(recipe.param("ring_filter", 0) or 0)
    ccw = bool(recipe.param("ccw", False))
    crs = recipe.param("crs") or export_crs

    def ring(ring_expr: str, exterior: bool) -> str:
        if offset:
            return _ring_buffer(ring_expr, repr(-offset if exterior else offset))
        if ccw:  # exterior counter-clockwise, holes clockwise (map y up)
            polygon = f"make_polygon({ring_expr})"
            oriented = f"force_polygon_ccw({polygon})" if exterior else \
                f"force_polygon_cw({polygon})"
            return f"exterior_ring({oriented})"
        return ring_expr

    def rings(part):
        exterior = (f"array({ring(f'exterior_ring({part})', True)})"
                    if ring_filter in (0, 1) else "array()")
        interior = (f"if(num_interior_rings({part}) > 0, array_foreach(generate_series(1, "
                    f"num_interior_rings({part})), {ring(f'interior_ring_n({part}, @element)', False)}), "
                    f"array())" if ring_filter in (0, 2) else "array()")
        return (f"collect_geometries(array_filter(array_cat({exterior}, {interior}), "
                f"@element IS NOT NULL AND NOT is_empty(@element)))")

    def body(geom):
        # Every polygon part (multi-polygons included) contributes its rings.
        return (f"collect_geometries(array_filter(array_foreach(generate_series(1, "
                f"num_geometries({geom})), with_variable('q2vt_part', if(is_multipart({geom}), "
                f"geometry_n({geom}, @element), {geom}), "
                f"{rings('@q2vt_part')})), @element IS NOT NULL AND NOT is_empty(@element)))")
    if crs == export_crs or not offset:
        return body("@geometry")
    return (f"transform(with_variable('q2vt_poly', transform(@geometry, '{export_crs}', '{crs}'), "
            f"{body('@q2vt_poly')}), '{crs}', '{export_crs}')")




def centroid_fill_expression(point_on_surface: bool, geom: str = "@geometry") -> str:
    """Marker position of ``QgsCentroidFillSymbolLayer`` for one polygon part.

    ``QgsSymbolLayerUtils::polygonCentroid`` uses the exterior ring only
    (holes ignored); with *point on surface* that centroid is kept unless the
    polygon has holes or the centroid falls outside the exterior, then GEOS
    point-on-surface is used (``polygonPointOnSurface``).
    """
    exterior = f"make_polygon(exterior_ring({geom}))"
    if not point_on_surface:
        return f"centroid({exterior})"
    return (f"with_variable('q2vt_c', centroid({exterior}), "
            f"if(num_interior_rings({geom}) > 0 OR NOT intersects({exterior}, @q2vt_c), "
            f"point_on_surface({geom}), @q2vt_c))")


def coerce_to_symbol_type(expression: str, symbol_type: int) -> str:
    """Geometry generator output as its sub-symbol receives it
    (``QgsGeometryGeneratorSymbolLayer::render`` coerces to MultiPoint /
    MultiLineString / MultiPolygon): lines drawn by a line symbol from a
    polygon are its rings, points drawn by a marker symbol from lines or
    polygons are their vertices."""
    if symbol_type == 1:
        return (f"with_variable('q2vt_coerce', {expression}, if(geometry_type(@q2vt_coerce) = "
                f"'Polygon', boundary(@q2vt_coerce), @q2vt_coerce))")
    if symbol_type == 0:
        return (f"with_variable('q2vt_coerce', {expression}, if(geometry_type(@q2vt_coerce) = "
                f"'Point', @q2vt_coerce, nodes_to_points(@q2vt_coerce)))")
    return expression


# --- gradient and shapeburst fills as colour bands ------------------------------------------
#
# The browser has no gradient fill. A QGIS gradient (linear, radial,
# conical) or shapeburst fill is exported as ``bands`` solid fills: band i
# covers the part of the polygon where the gradient parameter t lies in
# [i/n, (i+1)/n] and is filled with the colour at its middle. Opaque fills
# use overlapping bands (band i = every t >= i/n, drawn bottom to top) so
# no hairline seams show between neighbours; translucent ones use exact,
# non-overlapping bands.

def gradient_band_recipe(gradient_type: int, ref1, ref2, centroid1: bool, centroid2: bool,
                         angle: float, spread: int, band: int, bands: int, overlap: bool,
                         construction_crs: str, extend: float = 0.0) -> Recipe:
    """``ref1``/``ref2``: QGIS reference points as fractions of the feature's
    bounding box (x right, y down); ``gradient_type`` 0 linear, 1 radial,
    2 conical; ``spread`` 0 pad, 1 reflect, 2 repeat (QgsGradientFillSymbolLayer).
    ``extend``: bands that do not overlap are grown by this fraction of a
    band towards the band drawn after them (which covers it), so
    anti-aliased edges leave no gaps (opaque colours only)."""
    # QGIS rotates the reference points around the box centre by ``angle``
    # (QTransform::rotate in y-down box fractions: clockwise on screen).
    a = math.radians(angle or 0.0)

    def rotated(point):
        x, y = point[0] - 0.5, point[1] - 0.5
        return (0.5 + x * math.cos(a) - y * math.sin(a), 0.5 + x * math.sin(a) + y * math.cos(a))

    p1, p2 = rotated(ref1), rotated(ref2)
    return Recipe("gradient_band", (), (
        ("type", int(gradient_type)), ("p1", (float(p1[0]), float(p1[1]))),
        ("p2", (float(p2[0]), float(p2[1]))), ("c1", bool(centroid1)), ("c2", bool(centroid2)),
        ("spread", int(spread)), ("band", int(band)), ("bands", int(bands)),
        ("overlap", bool(overlap)), ("crs", construction_crs), ("extend", float(extend))))


def shapeburst_band_recipe(distance: float, whole_shape: bool, ignore_rings: bool, band: int,
                           bands: int, construction_crs: str) -> Recipe:
    """Band ``band`` of a shapeburst fill: t = distance to the boundary /
    ``distance`` (map units of ``construction_crs``), or / the largest
    distance inside the polygon (``whole_shape``). Bands overlap (inset
    polygons drawn from the edge inwards)."""
    return Recipe("shapeburst_band", (), (
        ("distance", float(distance)), ("whole", bool(whole_shape)), ("ignore_rings", bool(ignore_rings)),
        ("band", int(band)), ("bands", int(bands)), ("crs", construction_crs)))


def _band_interval(band: int, bands: int, overlap: bool):
    """(start, end) of a band's t interval, None = unbounded on that side."""
    start = None if band == 0 else band / bands
    end = None if overlap or band == bands - 1 else (band + 1) / bands
    return start, end


def color_bands_recipe(band_recipes, colors) -> Recipe:
    """All bands of one gradient / shapeburst fill in one dataset: the
    per-band recipes (gradient_band_recipe / shapeburst_band_recipe) and
    their colours (encodeColor strings). The exporter writes one polygon per
    feature and band (fidelity.bands) with BAND_FIELD and COLOR_FIELD; the
    style draws them in band order."""
    return Recipe("color_bands", (), (("bands", tuple(band_recipes)), ("colors", tuple(colors))))

