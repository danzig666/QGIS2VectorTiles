import math

import pytest

from fidelity import units as u


class FakeEnum:
    def __init__(self, name, value):
        self.name, self.value = name, value


@pytest.mark.parametrize("value,expected", [
    (FakeEnum("Millimeters", 0), u.MM), (FakeEnum("MapUnits", 1), u.MAP),
    (FakeEnum("MetersInMapUnits", 7), u.METERS), (0, u.MM), (4, u.PT), (5, u.INCH),
    ("RenderUnit.Pixels", u.PX), ("Points", u.PT), (None, u.UNKNOWN), (99, u.UNKNOWN),
    (True, u.UNKNOWN), ("mmm", u.UNKNOWN),
])
def test_normalize_unit(value, expected):
    assert u.normalize_unit(value) == expected


def test_physical_units_at_96_dpi():
    conv = u.LengthConverter()
    assert conv.static(25.4, "mm") == pytest.approx(96)
    assert conv.static(72, "pt") == pytest.approx(96)
    assert conv.static(1, "in") == 96
    assert conv.static(3, "px") == 3


def test_unknown_unit_is_an_error_not_millimeters():
    with pytest.raises(u.UnitError):
        u.LengthConverter().static(1, "furlongs")
    with pytest.raises(u.UnitError):
        u.LengthConverter().static(1, "pct")


def _eval_curve(curve, zoom):
    """Evaluate an exponential base-2 MapLibre interpolate with numeric stops."""
    stops = list(zip(curve[3::2], curve[4::2]))
    if zoom <= stops[0][0]:
        return stops[0][1]
    for (z0, o0), (z1, o1) in zip(stops, stops[1:]):
        if z0 <= zoom <= z1:
            t = (2 ** (zoom - z0) - 1) / (2 ** (z1 - z0) - 1)
            return o0 + t * (o1 - o0)
    return stops[-1][1]


@pytest.mark.parametrize("zoom", [0, 3.3, 10, 14.5, 18])
def test_web_mercator_map_units_follow_resolution(zoom):
    curve = u.LengthConverter().static(10, "map")
    resolution = u.EARTH_CIRCUMFERENCE / (512 * 2 ** zoom)
    assert _eval_curve(curve, zoom) == pytest.approx(10 / resolution, rel=1e-9)


def test_map_unit_clamps_are_exact_at_knees():
    mus = u.MapUnitScale(min_size_mm=1.0, max_size_mm=5.0)
    curve = u.LengthConverter().static(10, "map", mus)
    mm = 96 / 25.4
    for zoom in [0, 5, 10, 12.7, 13, 14, 16, 20]:
        raw = 10 * 512 * 2 ** zoom / u.EARTH_CIRCUMFERENCE
        expected = min(max(raw, 1 * mm), 5 * mm)
        assert _eval_curve(curve, zoom) == pytest.approx(expected, rel=1e-6)


def test_data_defined_map_units_multiply_inside_stops():
    curve = u.LengthConverter().convert(["get", "w"], "map")
    assert curve[0] == "interpolate" and curve[2] == ["zoom"]
    assert all(isinstance(out, list) and out[0] == "*" for out in curve[4::2])


def test_data_defined_map_units_have_a_stop_at_every_zoom():
    """MapLibre lays out a data-driven text-size at the stops around the tile
    zoom, packed to at most 512 px: stops at 0 and 24 only clipped the z24
    size and made map-unit labels ~4x too small at zoom 17 (Földrészletek)."""
    curve = u.LengthConverter().convert(["get", "size"], "map")
    zooms = curve[3::2]
    assert zooms == [float(z) for z in range(0, 25)]
    # Neighbouring stops differ by exactly 2x (exponential base 2 is exact).
    factors = [out[2] for out in curve[4::2]]
    assert all(b == pytest.approx(2 * a) for a, b in zip(factors, factors[1:]))


def test_projected_crs_uses_reference_latitude():
    ctx = u.MapUnitContext.for_project(False, "meters", 47.5)
    assert not ctx.exact
    assert ctx.mercator_per_map_unit == pytest.approx(1 / math.cos(math.radians(47.5)))


def test_device_pixel_ratio_does_not_change_css_size():
    # Sizes are CSS px; there is no DPR parameter by design.
    assert u.LengthConverter(dpi=96).static(1, "mm") == pytest.approx(3.7795, rel=1e-4)
