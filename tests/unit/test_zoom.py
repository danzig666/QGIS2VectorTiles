import pytest

from fidelity import zoom as zm
from fidelity.model import OverzoomPolicy, ZoomInterval


def test_roundtrip():
    for z in [0, 1, 7.5, 16, 22]:
        assert zm.scale_to_zoom(zm.zoom_to_scale(z)) == pytest.approx(z)


def test_fractional_single_zoom_rule_is_nonempty():
    # Scale range strictly between zoom 3 and zoom 4 (regression: legacy
    # integer rounding produced min_zoom 4 > max_zoom 3).
    interval = zm.interval_from_scales(zm.zoom_to_scale(3.2), zm.zoom_to_scale(3.8))
    assert not interval.is_empty
    assert interval.min_zoom == pytest.approx(3.2)
    assert interval.max_zoom == pytest.approx(3.8)
    assert interval.tile_zooms(0, 14) == (3, 3)


def test_breakpoint_semantics_half_open():
    interval = ZoomInterval(5, 10)
    assert interval.contains(5)
    assert interval.contains(9.999)
    assert not interval.contains(10)
    assert not interval.contains(4.999)


def test_integer_breakpoints_tile_range():
    interval = zm.interval_from_scales(zm.zoom_to_scale(5), zm.zoom_to_scale(10))
    assert interval.tile_zooms(0, 22) == (5, 9)


def test_unbounded_interval_and_overzoom_policies():
    interval = zm.interval_from_scales(0, 0)
    assert interval.max_zoom is None
    assert interval.style_bounds(14, OverzoomPolicy.PERSIST) == (0, 24)
    assert interval.style_bounds(14, OverzoomPolicy.STOP) == (0, 15)
    assert interval.tile_zooms(2, 14) == (2, 14)


def test_tile_zoom_helpers_match_interval():
    assert zm.tile_min_zoom(0) == 0
    assert zm.tile_max_zoom(0) == zm.MAX_TILE_ZOOM
    assert zm.tile_min_zoom(zm.zoom_to_scale(3.2)) == 3
    assert zm.tile_max_zoom(zm.zoom_to_scale(3.8)) == 3
    assert zm.tile_max_zoom(zm.zoom_to_scale(10)) == 9


def test_inverted_scales_are_empty():
    interval = zm.interval_from_scales(zm.zoom_to_scale(8), zm.zoom_to_scale(6))
    assert interval.is_empty
    assert interval.tile_zooms(0, 22) is None
