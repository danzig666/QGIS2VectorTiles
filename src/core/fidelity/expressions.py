"""
expressions.py

Typed construction of MapLibre style expressions.

Rules enforced here (MapLibre style spec, "expressions"):

* ``["zoom"]`` may only be the input of a *top-level* ``step`` or
  ``interpolate``. Feature-dependent arithmetic on a zoom curve is therefore
  pushed into the stop outputs instead of wrapping the curve.
* Python arithmetic is never applied to expression lists: use :func:`mul` /
  :func:`div`, which constant-fold numbers and build expressions otherwise.
* Every generated number is finite.
"""

import math
from numbers import Real
from typing import Any, Iterable, List, Optional, Set

Expression = Any  # a JSON value: number, string, bool, list, dict


class ExpressionError(ValueError):
    """Raised for expressions that MapLibre would reject."""


def is_number(value: Any) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool)


def finite(value: float) -> float:
    if not is_number(value) or not math.isfinite(float(value)):
        raise ExpressionError(f"Non-finite number in expression: {value!r}")
    return value


def is_expression(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and isinstance(value[0], str)


def get(field: str) -> List:
    if not isinstance(field, str) or not field:
        raise ExpressionError(f"Invalid field name: {field!r}")
    return ["get", field]


def to_number(expr: Expression, fallback: float = 0) -> Expression:
    """Coerce to a number; nulls and unparsable values become ``fallback``."""
    if is_number(expr):
        return finite(expr)
    return ["to-number", expr, finite(fallback)]


def to_color(expr: Expression, fallback: str) -> Expression:
    if isinstance(expr, str):
        return expr
    return ["to-color", expr, fallback]


def is_zoom_curve(expr: Expression) -> bool:
    """True for a top-level ``interpolate``/``step`` driven by ``["zoom"]``."""
    if not is_expression(expr):
        return False
    if expr[0] == "interpolate" and len(expr) >= 5:
        return expr[2] == ["zoom"]
    if expr[0] == "step" and len(expr) >= 3:
        return expr[1] == ["zoom"]
    return False


def contains_zoom(expr: Expression) -> bool:
    if expr == ["zoom"]:
        return True
    if isinstance(expr, list):
        return any(contains_zoom(item) for item in expr)
    return False


def _map_outputs(curve: List, fn) -> List:
    """Apply ``fn`` to every stop output of a zoom curve."""
    if curve[0] == "interpolate":
        head = curve[:3]
        body = curve[3:]
        out = list(head)
        for i in range(0, len(body), 2):
            out.extend([body[i], fn(body[i + 1])])
        return out
    # step: ["step", input, default, z1, out1, ...]
    out = [curve[0], curve[1], fn(curve[2])]
    body = curve[3:]
    for i in range(0, len(body), 2):
        out.extend([body[i], fn(body[i + 1])])
    return out


def mul(a: Expression, b: Expression) -> Expression:
    """Multiply two values, folding constants and respecting zoom curves."""
    if is_number(a) and is_number(b):
        return finite(a * b)
    if is_number(a) and a == 1:
        return b
    if is_number(b) and b == 1:
        return a
    a_curve, b_curve = is_zoom_curve(a), is_zoom_curve(b)
    if a_curve and b_curve:
        raise ExpressionError("Cannot multiply two zoom curves")
    if a_curve:
        return _map_outputs(a, lambda out: mul(out, b))
    if b_curve:
        return _map_outputs(b, lambda out: mul(a, out))
    if contains_zoom(a) or contains_zoom(b):
        raise ExpressionError("Zoom expression nested inside arithmetic")
    return ["*", a, b]


def div(a: Expression, b: Expression, fallback: float = 0) -> Expression:
    """Divide ``a`` by ``b``; a zero or non-numeric divisor yields ``fallback``."""
    if is_number(b):
        if b == 0:
            return finite(fallback)
        return mul(a, 1.0 / b)
    if is_zoom_curve(b) or contains_zoom(b):
        raise ExpressionError("Zoom expression used as a divisor")
    if is_zoom_curve(a):
        return _map_outputs(a, lambda out: div(out, b, fallback))
    return ["case", ["==", b, 0], finite(fallback), ["/", a, b]]


def clamp(expr: Expression, low: Optional[float] = None,
          high: Optional[float] = None) -> Expression:
    if is_number(expr):
        value = expr
        if low is not None:
            value = max(value, low)
        if high is not None:
            value = min(value, high)
        return finite(value)
    if is_zoom_curve(expr):
        return _map_outputs(expr, lambda out: clamp(out, low, high))
    result = expr
    if low is not None:
        result = ["max", result, finite(low)]
    if high is not None:
        result = ["min", result, finite(high)]
    return result


def exponential_zoom_curve(stops: Iterable, base: float = 2.0) -> Expression:
    """``interpolate exponential`` over ``[(zoom, output), ...]``.

    Consecutive duplicate zooms are dropped; a single stop folds to its value.
    """
    cleaned = []
    for zoom, output in sorted(stops, key=lambda s: s[0]):
        if cleaned and abs(cleaned[-1][0] - zoom) < 1e-9:
            continue
        cleaned.append((finite(zoom), output if not is_number(output) else finite(output)))
    if not cleaned:
        raise ExpressionError("Zoom curve needs at least one stop")
    if len(cleaned) == 1:
        return cleaned[0][1]
    expr: List = ["interpolate", ["exponential", base], ["zoom"]]
    for zoom, output in cleaned:
        expr.extend([zoom, output])
    return expr


def validate_zoom_usage(expr: Expression, top_level: bool = True) -> None:
    """Raise :class:`ExpressionError` if ``["zoom"]`` is misused."""
    if not isinstance(expr, list):
        return
    if expr == ["zoom"]:
        raise ExpressionError("['zoom'] must be the input of a top-level step/interpolate")
    if is_zoom_curve(expr):
        if not top_level:
            raise ExpressionError("Zoom curve nested inside another expression")
        start = 3 if expr[0] == "interpolate" else 2
        for item in expr[start:]:
            validate_zoom_usage(item, top_level=False)
        return
    for item in expr:
        validate_zoom_usage(item, top_level=False)


def referenced_fields(expr: Expression) -> Set[str]:
    """Attribute names read by an expression (``get``/``has`` with literal names)."""
    fields: Set[str] = set()
    if isinstance(expr, list):
        if len(expr) >= 2 and expr[0] in ("get", "has") and isinstance(expr[1], str) \
                and len(expr) == 2:
            fields.add(expr[1])
        for item in expr:
            fields |= referenced_fields(item)
    elif isinstance(expr, dict):
        for value in expr.values():
            fields |= referenced_fields(value)
    return fields


def check_finite_numbers(expr: Expression) -> None:
    if is_number(expr):
        finite(expr)
    elif isinstance(expr, list):
        for item in expr:
            check_finite_numbers(item)
    elif isinstance(expr, dict):
        for value in expr.values():
            check_finite_numbers(value)


def evaluate_zoom_curve(curve: Expression, zoom: float) -> float:
    """Evaluate a numeric ``interpolate`` zoom curve (linear or exponential)."""
    if is_number(curve):
        return curve
    if not is_zoom_curve(curve) or curve[0] != "interpolate":
        raise ExpressionError("Only numeric interpolate zoom curves can be evaluated")
    base = curve[1][1] if curve[1][0] == "exponential" else 1.0
    stops = list(zip(curve[3::2], curve[4::2]))
    if any(not is_number(out) for _, out in stops):
        raise ExpressionError("Zoom curve has feature-dependent outputs")
    if zoom <= stops[0][0]:
        return stops[0][1]
    for (z0, o0), (z1, o1) in zip(stops, stops[1:]):
        if z0 <= zoom <= z1:
            if base == 1.0:
                t = (zoom - z0) / (z1 - z0)
            else:
                t = (base ** (zoom - z0) - 1) / (base ** (z1 - z0) - 1)
            return o0 + t * (o1 - o0)
    return stops[-1][1]


def ratio(numerator: Expression, denominator: Expression,
          zooms=tuple(range(0, 25))) -> Expression:
    """``numerator / denominator`` for camera-only values.

    Two exponential curves that scale identically (map-unit offsets over
    map-unit icon sizes) have a constant ratio; otherwise the ratio is
    sampled at integer zooms into a ``step`` curve.
    """
    if is_number(numerator) and is_number(denominator):
        return 0 if denominator == 0 else finite(numerator / denominator)
    values = []
    for zoom in zooms:
        num = evaluate_zoom_curve(numerator, zoom)
        den = evaluate_zoom_curve(denominator, zoom)
        values.append((zoom, 0.0 if den == 0 else num / den))
    distinct = {round(v, 9) for _, v in values}
    if len(distinct) == 1:
        return finite(values[0][1])
    expr: List = ["step", ["zoom"], finite(values[0][1])]
    for zoom, value in values[1:]:
        expr.extend([zoom, finite(value)])
    return expr
