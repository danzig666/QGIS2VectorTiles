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


def test_geographic_top_scale_is_qgis_metric_scale():
    """QGIS reports metric scales in a degree CRS (QgsScaleCalculator:
    haversine metres of a degree at the middle latitude). Zoom 0 was set to
    295.8M / 111319 = 2657, so every scale range landed ~15 zooms too low."""
    from fidelity.zoom import WEB_MERCATOR_TOP_SCALE
    ctx = u.MapUnitContext.for_project(False, "degrees", 47.5)
    metres_per_degree = u.qgis_metres_per_degree(47.5)
    assert metres_per_degree == pytest.approx(52.5e3, rel=0.01)
    # QGIS's scale of the browser's ground resolution (360/512 degrees per
    # px at zoom 0, 96 dpi).
    qgis_scale = 360.0 / 512.0 * metres_per_degree / 0.0254 * 96.0
    assert ctx.top_scale == pytest.approx(qgis_scale, rel=1e-9)
    assert ctx.top_scale == pytest.approx(1.4e8, rel=0.02)
    # A 1:5000 rule switches near zoom 14.6, not near zoom 0.
    assert 14.4 < math.log2(ctx.top_scale / 5000) < 14.9
    assert u.MapUnitContext.for_project(True, "meters", 47.5).top_scale == WEB_MERCATOR_TOP_SCALE


@pytest.mark.parametrize("units,per_unit", [("degrees", 360.0 / 40075016.68557849),
                                            ("feet", 1.0 / 0.3048), ("meters", 1.0)])
def test_screen_mm_in_map_units_do_not_go_through_the_scale(units, per_unit):
    """QGIS's scale counts metres; a screen mm in project map units is the
    browser's Mercator metres per mm over the Mercator units per map unit
    (degrees and feet were taken from the metric scale as if metres)."""
    from fidelity import zoom
    cos = math.cos(math.radians(47.5))
    ctx = u.MapUnitContext.for_project(False, units, 47.5)
    try:
        zoom.configure(ctx.top_scale, ctx.map_units_per_mm_0)
        mercator_per_mm = 40075016.68557849 / 512 / 2 ** 15 * 96 / 25.4
        expected = mercator_per_mm * per_unit * (1.0 if units == "degrees" else cos)
        assert zoom.map_units_per_mm(15) == pytest.approx(expected, rel=1e-6)
        if units == "meters":
            assert zoom.map_units_per_mm(15) == pytest.approx(zoom.zoom_to_scale(15) / 1000)
    finally:
        zoom.configure(zoom.WEB_MERCATOR_TOP_SCALE)
    assert zoom.map_units_per_mm(0) == pytest.approx(zoom.WEB_MERCATOR_TOP_SCALE / 1000)


def test_projected_top_scale_counts_the_crs_unit():
    from fidelity.zoom import WEB_MERCATOR_TOP_SCALE
    cos = math.cos(math.radians(47.5))
    metres = u.MapUnitContext.for_project(False, "meters", 47.5)
    feet = u.MapUnitContext.for_project(False, "feet", 47.5)
    assert metres.top_scale == pytest.approx(WEB_MERCATOR_TOP_SCALE * cos)
    # QgsScaleCalculator converts feet to inches: same ground scale.
    assert feet.top_scale == pytest.approx(WEB_MERCATOR_TOP_SCALE * cos)


def test_meters_at_scale_are_mercator_units_in_web_mercator():
    """QgsRenderContext::convertMetersToMapUnits returns meters unchanged in
    a metre CRS: in EPSG:3857 8 'meters at scale' draw as 8 map units (they
    were 1/cos(lat) = 1.48x too wide at 47.5 N)."""
    ctx = u.MapUnitContext.for_project(True, "meters", 47.5)
    assert ctx.mercator_per_meter == 1.0
    lengths = u.LengthConverter(map_context=ctx)
    assert lengths.static(8, "m") == lengths.static(8, "map")
    # Elsewhere ground meters keep the latitude factor (QGIS measures them
    # on the ellipsoid in a degree CRS, by the unit factor in a projected one).
    geographic = u.MapUnitContext.for_project(False, "degrees", 47.5)
    assert geographic.mercator_per_meter == pytest.approx(1 / math.cos(math.radians(47.5)))
