import math

import pytest

Image = pytest.importorskip("PIL.Image")

from fidelity.assets import AtlasEntry, pack, symmetric_crop_box  # noqa: E402
from fidelity.model import ExportProfile  # noqa: E402
from fidelity.patterns import (LinePatternSpec, render_line_pattern,  # noqa: E402
                               solve_periodic_cell)

PROFILE = ExportProfile()


@pytest.mark.parametrize("angle", [0, 45, 90, 135, 17, 30, 60])
def test_periodic_cell_within_tolerance(angle):
    spec = LinePatternSpec(angle, 8.0, 1.0, (0, 0, 0, 255))
    cell = solve_periodic_cell(spec, PROFILE)
    assert cell is not None and cell.within_tolerance
    assert cell.angle_error_deg <= PROFILE.tolerance_angle_deg
    assert PROFILE.within_tolerance(8.0, cell.spacing_px)


def test_horizontal_cell_is_exact_spacing():
    cell = solve_periodic_cell(LinePatternSpec(0, 10.0, 1.0, (0, 0, 0, 255)), PROFILE)
    assert cell.size == 10 and cell.spacing_px == 10.0


def _rows_with_ink(img):
    return [y for y in range(img.height) if any(img.getpixel((x, y))[3] for x in range(img.width))]


def test_rendered_cell_is_seamless_when_tiled():
    spec = LinePatternSpec(17, 7.0, 1.2, (200, 0, 0, 255))
    cell = solve_periodic_cell(spec, PROFILE)
    img = render_line_pattern(spec, cell, pixel_ratio=1)
    # Tiling 2x2 must equal rendering the analytical function on the big area:
    # compare the right edge column with the column just before the left edge.
    big = Image.new("RGBA", (img.width * 2, img.height * 2))
    for dx in (0, img.width):
        for dy in (0, img.height):
            big.paste(img, (dx, dy))
    ref_spec = LinePatternSpec(17, 7.0, 1.2, (200, 0, 0, 255))
    wide = render_line_pattern(ref_spec, type(cell)(cell.size * 2, cell.normal, cell.spacing_px,
                                                    cell.angle_deg, 0, 0, True))
    assert list(big.getdata()) == list(wide.getdata())


def test_2x_render_is_true_resolution_not_upscale():
    # Line centred on a pixel row (offset 0.5 px) so coverage is not split.
    spec = LinePatternSpec(0, 6.0, 1.0, (0, 0, 0, 255), offset_px=0.5)
    cell = solve_periodic_cell(spec, PROFILE)
    one = render_line_pattern(spec, cell, 1)
    two = render_line_pattern(spec, cell, 2)
    assert two.size == (one.width * 2, one.height * 2)
    assert len(_rows_with_ink(one)) == 1 and len(_rows_with_ink(two)) == 2


def test_transparent_color_stays_transparent():
    spec = LinePatternSpec(45, 5.0, 1.0, (0, 0, 0, 0))
    cell = solve_periodic_cell(spec, PROFILE)
    img = render_line_pattern(spec, cell)
    assert img.getbbox() is None


def test_atlas_is_deterministic_and_ratios_consistent():
    def entry(name, w, h):
        return AtlasEntry(name, {1: (Image.new("RGBA", (w, h), (1, 2, 3, 255)), 1),
                                 2: (Image.new("RGBA", (w * 2, h * 2), (1, 2, 3, 255)), 2)})
    a = pack([entry("b", 5, 7), entry("a", 3, 3)])
    b = pack([entry("a", 3, 3), entry("b", 5, 7)])
    assert a.index == b.index
    for name, one in a.index[1].items():
        two = a.index[2][name]
        assert (two["x"], two["y"]) == (one["x"] * 2, one["y"] * 2)
        assert two["width"] / two["pixelRatio"] == one["width"] / one["pixelRatio"]


def test_symmetric_crop_keeps_origin_centred():
    box = symmetric_crop_box((50, 40, 70, 60), 100, 100)
    left, top, right, bottom = box
    assert (left + right) / 2 == 50 and (top + bottom) / 2 == 50
    assert right - left >= 20 and math.isclose(bottom - top, 20)
