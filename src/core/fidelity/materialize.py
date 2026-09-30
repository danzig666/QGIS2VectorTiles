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

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

ANGLE_FIELD = "q2vt_mat_angle"
ORDINAL_FIELD = "q2vt_mat_ordinal"
LENGTH_FIELD = "q2vt_mat_length"
COUNT_FIELD = "q2vt_mat_npoints"

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
    params = (("offset", float(offset)), ("crs", crs)) if offset else ()
    return Recipe("marker_points", tuple(sorted(set(placements) & POINT_PLACEMENTS)), params)


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


MAX_GRID_POINTS = 200000


def grid_recipe(dx: float, dy: float, disp_x: float, disp_y: float, off_x: float,
                off_y: float, construction_crs: str, anchor: str, inset: float = 0.0,
                rows_from_top: bool = False) -> Recipe:
    """Point-pattern grid in map units (see :func:`grid_expression`)."""
    return Recipe("grid_points", (), (
        ("dx", float(dx)), ("dy", float(dy)), ("disp_x", float(disp_x)),
        ("disp_y", float(disp_y)), ("off_x", float(off_x)), ("off_y", float(off_y)),
        ("crs", construction_crs), ("anchor", anchor), ("inset", float(inset)),
        ("top", bool(rows_from_top))))


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
    """
    p = recipe.param
    dx, dy = p("dx"), p("dy")
    if not dx or not dy or dx <= 0 or dy <= 0:
        raise ValueError("Grid distances must be positive")
    crs = p("crs")
    geom = "@geometry" if crs == export_crs else f"transform(@geometry, '{export_crs}', '{crs}')"
    feature = p("anchor") == "feature"
    top = bool(p("top")) and feature
    x0 = "x_min(@q2vt_g)" if feature else "0"
    y0 = ("y_max(@q2vt_g)" if top else "y_min(@q2vt_g)") if feature else "0"
    sy = -1.0 if top else 1.0
    # Measured: with rows counted from the top, even columns are displaced.
    col_parity = 0 if top else 1
    ox, ddx, ddy = p("off_x"), p("disp_x"), p("disp_y")
    oy = -p("off_y")
    clip = "@q2vt_g" if not p("inset") else f"buffer(@q2vt_g, {-p('inset')!r})"
    lo, hi = ("y_min(@q2vt_g)", "y_max(@q2vt_g)")
    j_range = (
        f"with_variable('q2vt_j0', floor(min(({lo} - @q2vt_y0) * {sy!r}, ({hi} - @q2vt_y0) * {sy!r})"
        f" / {dy!r}) - 1, "
        f"with_variable('q2vt_j1', ceil(max(({lo} - @q2vt_y0) * {sy!r}, ({hi} - @q2vt_y0) * {sy!r})"
        f" / {dy!r}) + 1, "
    )
    body = (
        f"with_variable('q2vt_g', {geom}, "
        f"with_variable('q2vt_x0', {x0} + {ox!r}, with_variable('q2vt_y0', {y0} + {oy!r}, "
        f"with_variable('q2vt_i0', floor((x_min(@q2vt_g) - @q2vt_x0 - abs({ddx!r})) / {dx!r}) - 1, "
        f"with_variable('q2vt_i1', ceil((x_max(@q2vt_g) - @q2vt_x0 + abs({ddx!r})) / {dx!r}) + 1, "
        + j_range +
        f"with_variable('q2vt_ni', @q2vt_i1 - @q2vt_i0 + 1, "
        f"with_variable('q2vt_n', @q2vt_ni * (@q2vt_j1 - @q2vt_j0 + 1), "
        f"if(@q2vt_n > {MAX_GRID_POINTS}, NULL, "
        f"intersection({clip}, collect_geometries(array_foreach(generate_series(0, @q2vt_n - 1), "
        f"with_variable('q2vt_i', @q2vt_i0 + @element % @q2vt_ni, "
        f"with_variable('q2vt_j', @q2vt_j0 + floor(@element / @q2vt_ni), "
        f"make_point(@q2vt_x0 + @q2vt_i * {dx!r} + if(abs(@q2vt_j % 2) = 1, {ddx!r}, 0), "
        f"@q2vt_y0 + {sy!r} * @q2vt_j * {dy!r} + if(abs(@q2vt_i % 2) = {col_parity}, {ddy!r}, 0)))))))))))))))))"
    )
    if crs == export_crs:
        return body
    return f"transform({body}, '{crs}', '{export_crs}')"
