import math

import pytest

from fidelity import expressions as ex


def test_mul_constant_folds_numbers():
    assert ex.mul(2, 3.5) == 7.0
    assert ex.mul(1, ["get", "a"]) == ["get", "a"]


def test_mul_never_uses_python_list_arithmetic():
    # Regression: icon-size used `list / number` and raised TypeError.
    expr = ex.div(ex.to_number(ex.get("q2vt_size"), 1), 3)
    assert expr == ["*", ["to-number", ["coalesce", ["get", "q2vt_size"], 1], 1], 1.0 / 3]


def test_to_string_and_to_boolean_fall_back_on_null():
    """MapLibre's to-string turns null into '' and to-boolean into false: a
    data-defined character or flag without a value lost its static one."""
    assert ex.to_string(ex.get("c"), "A") == ["to-string", ["coalesce", ["get", "c"], "A"]]
    assert ex.to_string("left") == "left"
    assert ex.to_boolean(ex.get("b"), True) == ["to-boolean", ["coalesce", ["get", "b"], True]]
    assert ex.to_boolean(False) is False


def test_to_number_falls_back_on_null():
    """MapLibre's to-number turns null (a feature without the property) into
    0, not into its next argument: a null data-defined size drew labels at
    size 0. The fallback is put in place of null first."""
    assert ex.to_number(ex.get("size"), 9) == \
        ["to-number", ["coalesce", ["get", "size"], 9], 9]


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
    assert clamped[4] == 1 and clamped[-1] == 100
    # The curve is cut where it crosses the bounds, not bent: clamping a stop
    # output alone changed the whole segment (a map-unit spacing of 0.0001 px
    # at z0 raised to 1 px was about 1 px too large at z14).
    for zoom in (0, 1, 3, 5.5, 8, 9.9, 10):
        expected = min(max(ex.evaluate_zoom_curve(curve, zoom), 1), 100)
        assert ex.evaluate_zoom_curve(clamped, zoom) == pytest.approx(expected, rel=1e-9)
    map_units = ex.exponential_zoom_curve([(0, 0.00255), (24, 42869.3)])
    assert ex.evaluate_zoom_curve(ex.clamp(map_units, 1.0), 14) == pytest.approx(
        ex.evaluate_zoom_curve(map_units, 14), rel=1e-9)


def test_camera_only_detection():
    curve = ex.exponential_zoom_curve([(0, 1.0), (10, 2.0)])
    assert ex.is_camera_only(curve) and ex.is_camera_only(3)
    assert not ex.is_camera_only(ex.mul(curve, ex.get("w")))


def test_add_folds_constants_and_zoom_curves():
    assert ex.add(1, 2) == 3
    assert ex.add(0, ["get", "a"]) == ["get", "a"]
    a = ex.exponential_zoom_curve([(0, 1), (24, 2 ** 24)])
    b = ex.exponential_zoom_curve([(0, 2), (24, 2 ** 25)])
    total = ex.add(a, b)
    assert ex.evaluate_zoom_curve(total, 10) == pytest.approx(3 * 2 ** 10)
    c = ex.exponential_zoom_curve([(0, 1), (12, 2 ** 12), (24, 2 ** 12)])
    mixed = ex.add(a, c)
    assert ex.evaluate_zoom_curve(mixed, 16) == pytest.approx(2 ** 16 + 2 ** 12)
    assert ex.add(a, ["get", "w"])[4] == ["+", 1, ["get", "w"]]
    with pytest.raises(ex.ExpressionError):
        ex.add(["step", ["zoom"], 0, 5, 1], a)
