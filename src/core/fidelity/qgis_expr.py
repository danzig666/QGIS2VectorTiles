"""
qgis_expr.py

Pure-string helpers for QGIS expressions that must be evaluated outside their
original render context (field calculators, filters, geometry generators).

Values are bound with ``with_variable`` rather than by rewriting expression
text, so string literals and identifiers that merely *contain* a variable
name are left untouched.
"""

import re


def with_variable(name: str, value_expression: str, expression: str) -> str:
    return f"with_variable('{name}', {value_expression}, ({expression}))"


def with_map_scale(expression: str, scale) -> str:
    """Bind ``@map_scale`` to ``scale`` for ``expression``."""
    if not expression or "map_scale" not in expression:
        return expression
    return with_variable("map_scale", repr(float(scale)), expression)


_TOKEN_RE = re.compile(
    r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[$@]geometry\b", re.IGNORECASE)


def substitute_geometry(expression: str, replacement: str) -> str:
    """Replace ``@geometry`` and ``$geometry`` outside string literals and
    quoted identifiers by ``(replacement)``.

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


_MEASURE_RE = re.compile(
    r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|\$(area|length|perimeter|x|y)\b", re.IGNORECASE)


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
