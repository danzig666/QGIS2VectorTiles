import math

import pytest

from fidelity import expressions as ex


def test_mul_constant_folds_numbers():
    assert ex.mul(2, 3.5) == 7.0
    assert ex.mul(1, ["get", "a"]) == ["get", "a"]


def test_mul_never_uses_python_list_arithmetic():
    # Regression: icon-size used `list / number` and raised TypeError.
    expr = ex.div(ex.to_number(ex.get("q2vt_size"), 1), 3)
    assert expr == ["*", ["to-number", ["get", "q2vt_size"], 1], 1.0 / 3]


def test_zero_divisor_returns_fallback():
    assert ex.div(5, 0, fallback=1) == 1
    guarded = ex.div(4, ex.get("d"), fallback=0)
    assert guarded[0] == "case" and guarded[2] == 0


def test_feature_multiplication_is_pushed_into_zoom_stops():
    curve = ex.exponential_zoom_curve([(0, 1.0), (10, 1024.0)])
    result = ex.mul(curve, ex.get("w"))
    assert ex.is_zoom_curve(result)
    assert result[4] == ["get", "w"]  # x1 folded
    assert result[6] == ["*", 1024.0, ["get", "w"]]
    ex.validate_zoom_usage(result)


def test_two_zoom_curves_cannot_be_multiplied():
    curve = ex.exponential_zoom_curve([(0, 1.0), (10, 2.0)])
    with pytest.raises(ex.ExpressionError):
        ex.mul(curve, curve)


def test_nested_zoom_is_rejected():
    with pytest.raises(ex.ExpressionError):
        ex.validate_zoom_usage(["*", 2, ["interpolate", ["linear"], ["zoom"], 0, 1, 5, 2]])
    with pytest.raises(ex.ExpressionError):
        ex.validate_zoom_usage(["+", ["zoom"], 1])


def test_non_finite_numbers_rejected():
    with pytest.raises(ex.ExpressionError):
        ex.mul(math.inf, 2)
    with pytest.raises(ex.ExpressionError):
        ex.check_finite_numbers(["*", float("nan"), 1])


def test_referenced_fields():
    expr = ["case", ["has", "a"], ["get", "a"], ["to-number", ["get", "b"], 0]]
    assert ex.referenced_fields(expr) == {"a", "b"}


def test_clamp_inside_zoom_curve():
    curve = ex.exponential_zoom_curve([(0, 0.5), (10, 512.0)])
    clamped = ex.clamp(curve, 1, 100)
    assert clamped[4] == 1 and clamped[6] == 100
