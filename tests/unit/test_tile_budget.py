"""Tiles end at the data's detail when an export would make far too many
(fidelity.tile_budget): country borders of a continent down to zoom 16 were
~90 million tiles (hours, the export seemed to hang near its end)."""

from fidelity import tile_budget as tb


def _extent_tiles(zoom):
    return 4 ** zoom  # no extent limit


def test_coarse_large_polygons_end_at_the_budget_not_below_their_detail():
    # 34 million km² of country polygons, 640 m shortest segments.
    countries = tb.LayerDetail("countries", area=34e12, length=3e8, features=23, detail=642.0)
    cap, tiles = tb.capped_max_zoom([countries], 0, 16, _extent_tiles)
    assert cap == 11 and tiles <= tb.BUDGET
    assert tb.detail_zoom([countries], 0, 16) <= cap


def test_a_small_export_keeps_every_zoom():
    # A town's parcels: 25 km², however detailed.
    parcels = tb.LayerDetail("parcels", area=25e6, length=2e6, features=4000, detail=0.5)
    assert tb.capped_max_zoom([parcels], 0, 16, _extent_tiles)[0] == 16


def test_fine_detail_is_never_cut_for_the_budget():
    # A large export of detailed data: the tiles go down to its detail.
    detailed = tb.LayerDetail("coast", area=34e12, length=3e8, features=10, detail=5.0)
    cap, _ = tb.capped_max_zoom([detailed], 0, 16, _extent_tiles)
    assert cap == tb.detail_zoom([detailed], 0, 16) and cap >= 15


def test_unknown_detail_keeps_every_zoom():
    unknown = tb.LayerDetail("x", area=34e12, length=3e8, features=10, detail=None)
    assert tb.capped_max_zoom([unknown], 0, 16, _extent_tiles)[0] == 16


def test_points_count_once_per_tile():
    quakes = tb.LayerDetail("quakes", points=2143, detail=903.0)
    assert tb.tiles_at([quakes], 16, 10 ** 9) == 2143
    assert tb.tiles_at([quakes], 2, 16) == 16  # at most the extent's tiles
