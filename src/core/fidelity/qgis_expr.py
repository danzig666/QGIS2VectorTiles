"""
qgis_expr.py

Pure-string helpers for QGIS expressions that must be evaluated outside their
original render context (field calculators, filters, geometry generators).

Values are bound with ``with_variable`` rather than by rewriting expression
text, so string literals and identifiers that merely *contain* a variable
name are left untouched.
"""

import math
import re
from typing import List, Optional


def with_variable(name: str, value_expression: str, expression: str) -> str:
    return f"with_variable('{name}', {value_expression}, ({expression}))"


# Map-scope variables QGIS derives from the map scale (mapSettingsScope):
# @map_scale, and the vector tile zooms of that scale.
SCALE_VARIABLES = ("map_scale", "zoom_level", "vector_tile_zoom")
_SCALE_VARIABLE = r"(?:@|\bvar\(\s*')(map_scale|zoom_level|vector_tile_zoom)\b(?:'\s*\))?"
_SCALE_VARIABLE_RE = re.compile(_SCALE_VARIABLE, re.IGNORECASE)
# QgsVectorTileUtils::scaleToZoom's default scale of zoom 0.
VECTOR_TILE_Z0_SCALE = 559082264.0287178


def uses_map_scale(expression: str) -> bool:
    """True if ``expression`` reads the map scale (``@map_scale``,
    ``@zoom_level`` or ``@vector_tile_zoom``)."""
    return bool(expression) and _SCALE_VARIABLE_RE.search(expression) is not None


def map_scale_reads(text: str) -> int:
    """How many times ``text`` reads a map-scale variable."""
    return len(_SCALE_VARIABLE_RE.findall(text or ""))


def vector_tile_zoom(scale: float) -> float:
    """``@vector_tile_zoom`` at a map scale (QgsVectorTileUtils::scaleToZoom)."""
    return math.log2(VECTOR_TILE_Z0_SCALE / float(scale)) - 1.0


def zoom_level(scale: float) -> int:
    """``@zoom_level`` at a map scale (QgsVectorTileUtils::scaleToZoomLevel
    between 0 and 99999: the rounded vector tile zoom)."""
    return min(99999, max(0, int(math.floor(vector_tile_zoom(scale) + 0.5))))


def with_map_scale(expression: str, scale) -> str:
    """Bind ``@map_scale`` (and the zoom variables QGIS derives from it,
    ``@zoom_level`` and ``@vector_tile_zoom``) to ``scale`` for ``expression``."""
    if not expression:
        return expression
    names = {name.lower() for name in _SCALE_VARIABLE_RE.findall(expression)}
    if not names:
        return expression
    if "vector_tile_zoom" in names:
        expression = with_variable("vector_tile_zoom", repr(vector_tile_zoom(scale)), expression)
    if "zoom_level" in names:
        expression = with_variable("zoom_level", str(zoom_level(scale)), expression)
    if "map_scale" in names:
        expression = with_variable("map_scale", repr(float(scale)), expression)
    return expression


_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_COMPARE = r"<=|>=|<>|!=|=|<|>"
_ARITHMETIC = "-+*/%^|"
_THRESHOLD_RES = (
    re.compile(rf"{_SCALE_VARIABLE}\s*({_COMPARE})\s*({_NUMBER})", re.IGNORECASE),
    re.compile(rf"({_NUMBER})\s*({_COMPARE})\s*{_SCALE_VARIABLE}", re.IGNORECASE),
    re.compile(rf"{_SCALE_VARIABLE}\s+(?:NOT\s+)?BETWEEN\s+({_NUMBER})\s+AND\s+({_NUMBER})",
               re.IGNORECASE),
)
_TESTS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
          ">=": lambda a, b: a >= b, "=": lambda a, b: a == b, "<>": lambda a, b: a != b,
          "!=": lambda a, b: a != b}
_MIRRORED = {"<": ">", "<=": ">=", ">": "<", ">=": "<=", "=": "=", "<>": "<>", "!=": "!="}


def _threshold_scales(name: str, operator: str, value: float) -> List[float]:
    """Map scales at which ``@name operator value`` can switch."""
    if name == "map_scale":
        return [value] if value > 0 else []
    if name == "vector_tile_zoom":
        return [VECTOR_TILE_Z0_SCALE / 2.0 ** (value + 1.0)]
    # @zoom_level is a whole number, stepping at half vector tile zooms:
    # the steps where the comparison changes.
    test = _TESTS[operator]
    steps = {k for k in range(math.floor(value) - 1, math.ceil(value) + 2)
             if test(k - 1, value) != test(k, value)}
    return [VECTOR_TILE_Z0_SCALE / 2.0 ** (k - 0.5 + 1.0) for k in sorted(steps)]


def scale_thresholds(text: str) -> Optional[List[float]]:
    """The map scales at which ``text`` (one or more expressions) can change
    value, when it only compares the scale variables with numbers
    (``@map_scale < 2000``, ``@zoom_level >= 15``, ``@map_scale BETWEEN
    1000 AND 5000``); None when one of them is used otherwise (a size
    growing with the scale), so the value changes at every scale."""
    scales: List[float] = []
    covered = set()
    for pattern in _THRESHOLD_RES:
        for match in pattern.finditer(text):
            before = text[:match.start()].rstrip()[-1:]
            after = text[match.end():].lstrip()[:1]
            if (before and before in _ARITHMETIC) or (after and after in _ARITHMETIC):
                continue  # part of a calculation: 2 * @map_scale < 1000
            if pattern is _THRESHOLD_RES[0]:
                name, tests = match.group(1), [(match.group(2), match.group(3))]
            elif pattern is _THRESHOLD_RES[1]:
                name, tests = match.group(3), [(_MIRRORED[match.group(2)], match.group(1))]
            else:
                name, tests = match.group(1), [(">=", match.group(2)), ("<=", match.group(3))]
            for operator, number in tests:
                scales.extend(_threshold_scales(name.lower(), operator, float(number)))
            covered.update(m.start() for m in _SCALE_VARIABLE_RE.finditer(text, match.start(),
                                                                          match.end()))
    if any(m.start() not in covered for m in _SCALE_VARIABLE_RE.finditer(text)):
        return None
    return sorted(set(scales))


# What QGIS's lexer reads as one token that is not code: line and block
# comments, string literals ('' and backslash escapes, 'it\'s') and quoted
# identifiers ("" escapes only). Matched first, they are passed through, so
# a variable in them is not rewritten and an apostrophe in a comment does
# not hide the code after it.
_LEXEME = r"--[^\n]*|/\*.*?\*/|'(?:[^'\\]|\\.|'')*'|\"(?:[^\"]|\"\")*\""
_TOKEN_RE = re.compile(_LEXEME + r"|[$@]geometry\b", re.IGNORECASE | re.DOTALL)


def substitute_geometry(expression: str, replacement: str) -> str:
    """Replace ``@geometry`` and ``$geometry`` outside string literals,
    quoted identifiers and comments by ``(replacement)``.

    ``with_variable('geometry', ...)`` cannot be used for this: QGIS resolves
    ``@geometry`` from the context feature, not from the variable stack
    (verified in ``tests/integration/test_end_to_end.py``).
    """
    def swap(match):
        token = match.group(0)
        return f"({replacement})" if token.lower() in ("@geometry", "$geometry") else token
    return _TOKEN_RE.sub(swap, expression or "")


def bind_geometry(expression: str, geometry_expression: str) -> str:
    """Evaluate ``expression`` with its feature geometry replaced by another geometry."""
    return substitute_geometry(expression, geometry_expression)


_MEASURE_RE = re.compile(_LEXEME + r"|\$(area|length|perimeter|x|y)\b",
                         re.IGNORECASE | re.DOTALL)


def in_screen_units(expression: str, units_per_screen_unit: float, frame_crs: str = "",
                    layer_crs: str = "") -> str:
    """Evaluate a geometry generator written in screen units (mm, px, pt, in).

    QgsGeometryGeneratorSymbolLayer::render evaluates such a generator on
    the painter geometry scaled to its units (y pointing down) and maps the
    result back. The geometry is scaled here by ``1 / units_per_screen_unit``
    (frame map units per screen unit) and mirrored about its centroid in
    ``frame_crs``, the expression is evaluated, and the result is scaled
    back. With a ``layer_crs`` other than ``frame_crs`` the input and the
    result are in the layer CRS (as the exporter evaluates generators).
    """
    k = float(units_per_screen_unit)
    geometry = "@geometry"
    if frame_crs and layer_crs and frame_crs != layer_crs:
        geometry = f"transform(@geometry, '{layer_crs}', '{frame_crs}')"
    centre = f"centroid({geometry})"
    scaled = f"scale({geometry}, {1.0 / k!r}, {-1.0 / k!r}, {centre})"
    result = f"scale({substitute_geometry(expression, scaled)}, {k!r}, {-k!r}, {centre})"
    if geometry != "@geometry":
        result = f"transform({result}, '{frame_crs}', '{layer_crs}')"
    return result


def in_layer_crs(expression: str, export_crs: str, layer_crs: str, planar_measures: bool) -> str:
    """Evaluate a scalar expression as QGIS does on the source layer.

    Exported features are in ``export_crs`` (Web Mercator) but QGIS evaluates
    filters, labels and data-defined properties on geometries in the layer
    CRS. The feature geometry is transformed back, and — when the project has
    no ellipsoid, i.e. QGIS measures planimetrically in the layer CRS —
    ``$area``/``$length``/``$perimeter`` are measured on that geometry.
    ``$x``/``$y`` always refer to layer CRS coordinates. Ellipsoidal
    measurements are left to the processing context's ellipsoid.
    """
    if not expression or not layer_crs or layer_crs == export_crs:
        return expression
    geometry = f"transform(@geometry, '{export_crs}', '{layer_crs}')"

    def swap(match):
        name = (match.group(1) or "").lower()
        if not name:
            return match.group(0)
        if name in ("x", "y"):
            return f"{name}({geometry})"
        if not planar_measures:
            return match.group(0)
        return {"area": f"area({geometry})", "length": f"length({geometry})",
                "perimeter": f"perimeter({geometry})"}[name]

    # Geometry first: the measure replacements insert @geometry themselves.
    return _MEASURE_RE.sub(swap, substitute_geometry(expression, geometry))


def enabled_condition(expression: str) -> str:
    """Filter condition equivalent to a data-defined *enabled*/*show* property.

    QGIS evaluates these with a default of ``true``: NULL keeps the component.
    """
    return f"with_variable('q2vt_on', ({expression}), @q2vt_on IS NULL OR if(@q2vt_on, TRUE, FALSE))"


def and_filters(*filters: str) -> str:
    parts = [f"({f})" for f in filters if f and f.strip()]
    return " AND ".join(parts)
