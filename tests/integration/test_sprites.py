"""Sprite rendering: input types, failures, transparency, anchors, 1x/2x."""

import json

import pytest
from qgis.core import (Qgis, QgsFillSymbol, QgsLinePatternFillSymbolLayer, QgsMarkerSymbol,
                       QgsSimpleMarkerSymbolLayer)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor

from fidelity.diagnostics import DiagnosticCollector


@pytest.fixture
def sg(plugin):
    from q2vt_plugin.src.core import sprite_generator  # pylint: disable=import-error
    return sprite_generator


def _marker(color="red", size=4.0, angle=0.0, offset=None):
    layer = QgsSimpleMarkerSymbolLayer()
    layer.setColor(QColor(color))
    layer.setStrokeColor(QColor(color))
    layer.setSize(size)
    layer.setAngle(angle)
    if offset:
        from qgis.PyQt.QtCore import QPointF
        layer.setOffset(QPointF(*offset))
    return QgsMarkerSymbol([layer])


def test_symbol_layer_is_rejected_with_actionable_error(sg):
    with pytest.raises(sg.SpriteInputError, match="QgsSymbol"):
        sg.SymbolImage(QgsLinePatternFillSymbolLayer(), "p")


def test_generator_reports_wrong_input_instead_of_transparent_image(sg, tmp_path):
    diags = DiagnosticCollector()
    gen = sg.SpriteGenerator({"bad": QgsLinePatternFillSymbolLayer(), "ok": _marker()},
                             str(tmp_path), 3, diagnostics=diags)
    assert gen.generate()
    assert "bad" in gen.failed and gen.names == ["ok"]
    assert diags.by_code("Q2VT_SPRITE_WRONG_INPUT")
    index = json.loads((tmp_path / "sprite" / "sprite.json").read_text())
    assert set(index) == {"ok"}


def test_render_exception_is_not_replaced_by_transparent_image(sg, tmp_path):
    class Broken(QgsMarkerSymbol):
        def clone(self):
            raise RuntimeError("renderer exploded")

    diags = DiagnosticCollector()
    gen = sg.SpriteGenerator({"broken": Broken()}, str(tmp_path), 3, diagnostics=diags)
    assert gen.generate() is None
    assert diags.by_code("Q2VT_SPRITE_RENDER_FAILED")
    assert not diags.by_code("Q2VT_SPRITE_TRANSPARENT")


def test_transparent_marker_is_by_design_not_failure(sg, tmp_path):
    diags = DiagnosticCollector()
    gen = sg.SpriteGenerator({"clear": _marker(QColor(0, 0, 0, 0))}, str(tmp_path), 3,
                             diagnostics=diags)
    assert gen.generate()
    assert not gen.failed
    assert diags.by_code("Q2VT_SPRITE_TRANSPARENT")


def test_wrapped_pattern_layer_renders_non_transparent(sg):
    fill = QgsFillSymbol()
    fill.changeSymbolLayer(0, QgsLinePatternFillSymbolLayer())
    img = sg.SymbolImage(fill, "pattern")
    assert img.img.getbbox() is not None


def test_offset_marker_keeps_origin_at_image_centre(sg):
    # A marker drawn 3 mm right of its origin: the tight crop would move the
    # anchor; the symmetric crop keeps the origin in the centre.
    img = sg.SymbolImage(_marker(offset=(3, 0)), "m", 3).img
    bbox = img.getbbox()
    assert bbox[0] > img.width / 2  # ink entirely right of centre


def test_rotation_is_not_baked_when_style_rotates(sg):
    rotated = sg.SymbolImage(_marker(size=6, angle=45).clone(), "a", 3, bake_rotation=False)
    upright = sg.SymbolImage(_marker(size=6, angle=0), "b", 3, bake_rotation=False)
    assert rotated.img.size == upright.img.size
    assert list(rotated.img.getdata()) == list(upright.img.getdata())


def test_2x_sheet_is_rendered_at_double_resolution(sg, tmp_path):
    gen = sg.SpriteGenerator({"m": _marker()}, str(tmp_path), 3)
    gen.generate()
    one = gen.index[1]["m"]
    two = gen.index[2]["m"]
    # Oversampled 3x (1x sheet) and 6x (2x sheet); logical sizes agree.
    assert one["pixelRatio"] == 3 and two["pixelRatio"] == 6
    assert abs(two["width"] / 6 - one["width"] / 3) <= 1


def test_screen_unit_point_pattern_texture(plugin, tmp_path):
    from q2vt_plugin.src.core.maplibre_converter import QgisMapLibreStyleExporter
    from qgis.core import QgsPointPatternFillSymbolLayer, Qgis
    # The constructor needs a vector tile layer; set the fields the pattern code uses.
    exporter = QgisMapLibreStyleExporter.__new__(QgisMapLibreStyleExporter)
    from q2vt_plugin.src.core import maplibre_converter as mc
    exporter.pattern_images, exporter.marker_counter = {}, 0
    exporter.profile = mc.ExportProfile()
    exporter.context = mc.ConversionContext(DiagnosticCollector())
    mc.PropertyExtractor.context = exporter.context
    layer = QgsPointPatternFillSymbolLayer()
    layer.setDistanceX(4)
    layer.setDistanceY(3)
    layer.setDisplacementX(2)
    for unit in ("DistanceX", "DistanceY", "DisplacementX"):
        getattr(layer, f"set{unit}Unit")(Qgis.RenderUnit.Millimeters)
    name = exporter._register_point_pattern(layer)
    cells = exporter.pattern_images[name]
    # MapLibre grows a texture 2x with the map until the next zoom: screen
    # sizes are drawn at 1/sqrt(2), exact in the middle of every zoom.
    from fidelity.patterns import point_pattern_cell
    px = 96 / 25.4 * exporter.TEXTURE_SCREEN_SCALE
    for ratio, image in ((1, cells.img_1x), (2, cells.img_2x)):
        width, height, _, _ = point_pattern_cell(4 * px * ratio, 3 * px * ratio,
                                                 2 * px * ratio, 0)
        assert image.size == (width, height)
    assert cells.img_1x.getbbox() is not None


def test_sprite_png_has_straight_alpha(sg, tmp_path):
    # MapLibre premultiplies sprite pixels itself: a half-transparent red
    # marker must be stored as (255, 0, 0, ~128), not premultiplied (128, 0, 0).
    from PIL import Image
    gen = sg.SpriteGenerator({"m": _marker(QColor(255, 0, 0, 128), size=6)}, str(tmp_path), 3)
    gen.generate()
    entry = gen.index[1]["m"]
    sheet = Image.open(tmp_path / "sprite" / "sprite.png").convert("RGBA")
    centre = sheet.getpixel((entry["x"] + entry["width"] // 2, entry["y"] + entry["height"] // 2))
    assert centre[3] == pytest.approx(128, abs=3)
    assert centre[0] >= 250 and centre[1] <= 3 and centre[2] <= 3


def test_data_defined_font_marker_text_fits_the_sprite(sg):
    """The canvas is sized with the variant's attributes: a data-defined
    character string longer than the static character is not cut off."""
    from qgis.core import QgsFontMarkerSymbolLayer, QgsProperty, QgsSymbolLayer
    layer = QgsFontMarkerSymbolLayer("DejaVu Sans", "A", 12)
    layer.setColor(QColor("black"))
    layer.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyCharacter,
                                 QgsProperty.fromExpression("concat('A', \"v\")"))
    static = sg.SymbolImage(QgsMarkerSymbol([layer.clone()]), "s", 1, True)
    wide = sg.SymbolImage(QgsMarkerSymbol([layer]), "w", 1, True,
                          attributes={"v": "BCDEFGH"})
    # Eight letters: sized from the static "A" the canvas would cut them off.
    assert wide.img.width > 5 * static.img.width


def test_point_pattern_texture_applies_the_offset(plugin, monkeypatch):
    """QGIS shifts the markers inside the pattern cell by the offset; two
    patterns offset against each other (e.g. "\\" and "/" forming zig-zags)
    must not collapse onto the same place."""
    from q2vt_plugin.src.core.maplibre_converter import QgisMapLibreStyleExporter
    from q2vt_plugin.src.core import maplibre_converter as mc
    from qgis.core import QgsPointPatternFillSymbolLayer, Qgis
    from PIL import ImageChops
    exporter = QgisMapLibreStyleExporter.__new__(QgisMapLibreStyleExporter)
    exporter.pattern_images, exporter.marker_counter = {}, 0
    exporter.profile = mc.ExportProfile()
    exporter.context = mc.ConversionContext(DiagnosticCollector())
    mc.PropertyExtractor.context = exporter.context
    monkeypatch.setattr(exporter, "TEXTURE_SCREEN_SCALE", 1.0)  # whole-pixel shifts
    cells = []
    for offset in (0.0, 4.0):
        layer = QgsPointPatternFillSymbolLayer()
        layer.setSubSymbol(QgsMarkerSymbol([QgsSimpleMarkerSymbolLayer()]))
        for name, value in (("DistanceX", 16.0), ("DistanceY", 16.0), ("OffsetX", offset)):
            getattr(layer, f"set{name}")(value)
            getattr(layer, f"set{name}Unit")(Qgis.RenderUnit.Pixels)
        cells.append(exporter.pattern_images[exporter._register_point_pattern(layer)].img_1x)
    rolled = ImageChops.offset(cells[0], 4, 0)  # 4 px to the right
    assert ImageChops.difference(rolled, cells[1]).getbbox() is None
    assert ImageChops.difference(cells[0], cells[1]).getbbox() is not None


def _outline_ink(img):
    """Ink of the outline along the middle row's left half (dark pixels of a
    white marker with a black stroke), in pixels."""
    rgba = img.convert("RGBA")
    width, height = rgba.size
    row = [rgba.getpixel((x, height // 2)) for x in range(width // 2)]
    return sum((255 - r) / 255 * a / 255 for r, _g, _b, a in row)


@pytest.mark.parametrize("ratio", [1, 3, 6])
def test_zero_width_stroke_is_one_css_pixel_in_oversampled_sprites(sg, ratio):
    """QGIS draws a zero stroke width one device pixel wide: in a sprite
    oversampled 3x that was a third of a CSS pixel, a grey fringe once
    MapLibre shrinks the icon (the black outline of flow arrows)."""
    layer = QgsSimpleMarkerSymbolLayer()
    layer.setShape(Qgis.MarkerShape.Square)
    layer.setSize(4)
    layer.setColor(QColor("white"))
    layer.setStrokeColor(QColor("black"))
    layer.setStrokeWidth(0)
    image = sg.SymbolImage(QgsMarkerSymbol([layer.clone()]), "m", ratio).img
    assert _outline_ink(image) == pytest.approx(ratio, rel=0.35)
    assert layer.strokeWidth() == 0  # the project's symbol is left alone


def test_data_defined_stroke_width_is_kept_in_sprites(sg):
    from qgis.core import QgsProperty, QgsSymbolLayer
    layer = QgsSimpleMarkerSymbolLayer()
    layer.setStrokeWidth(0)
    layer.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyStrokeWidth, QgsProperty.fromValue(0.5))
    symbol = QgsMarkerSymbol([layer])
    sg._hairlines_to_one_pixel(symbol)  # pylint: disable=protected-access
    assert symbol.symbolLayer(0).strokeWidth() == 0


def _limited_scale(kind):
    from qgis.core import QgsMapUnitScale
    if kind == "range":
        return QgsMapUnitScale(50000, 5000)
    limits = QgsMapUnitScale()
    limits.minSizeMMEnabled, limits.minSizeMM = True, 1.0
    limits.maxSizeMMEnabled, limits.maxSizeMM = True, 4.0
    return limits


@pytest.mark.parametrize("kind", ["mm", "range"])
@pytest.mark.parametrize("reference_zoom", [6, 12, 15, 19])
def test_map_unit_marker_limits_apply_once(plugin, sg, kind, reference_zoom):
    """A map-unit square with min/max sizes or a scale range: QGIS limits the
    size when it draws the sprite, and icon-size applied the limits again
    against the unlimited size (a blob at mid zooms, specks when zoomed in);
    with a scale range the sprite's render context had scale 1 and the
    marker came out empty."""
    import math
    from fidelity import expressions as ex
    from fidelity.zoom import scale_to_zoom
    from q2vt_plugin.src.core import maplibre_converter as mc  # pylint: disable=import-error
    layer = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Square, 100)
    layer.setColor(QColor("black"))
    layer.setStrokeStyle(Qt.PenStyle.NoPen)
    layer.setSizeUnit(Qgis.RenderUnit.MapUnits)
    layer.setSizeMapUnitScale(_limited_scale(kind))
    symbol = QgsMarkerSymbol([layer])
    mc.PropertyExtractor.context = mc.ConversionContext(DiagnosticCollector())
    mc.PropertyExtractor.context.reference_zoom = float(reference_zoom)
    icon_size, map_units_per_pixel, oversampling = mc.IconPropertyExtractor.marker_scale(
        symbol, layer)
    image = sg.SymbolImage(symbol, "m", oversampling, False, map_units_per_pixel)
    assert not image.transparent
    sprite_px = image.img.width / oversampling
    for zoom in (reference_zoom, reference_zoom + 0.5, reference_zoom + 3):
        clamped_zoom = zoom
        if kind == "range":
            clamped_zoom = min(max(zoom, scale_to_zoom(50000)), scale_to_zoom(5000))
        expected = 100 * 512 * 2 ** clamped_zoom / 40075016.68557849
        if kind == "mm":
            expected = min(max(expected, 96 / 25.4), 4 * 96 / 25.4)
        drawn = sprite_px * ex.evaluate_zoom_curve(icon_size, zoom)
        assert drawn == pytest.approx(expected, rel=0.15, abs=1.0), (zoom, sprite_px)
    assert math.isfinite(sprite_px)


@pytest.mark.parametrize("exact", [True, False])
def test_percentage_pattern_offsets_match_the_absolute_texture(plugin, monkeypatch, exact):
    """Percentage displacement and offset of a point pattern texture are
    shares of the spacing (applyPattern) and are not reported as units
    without a reference dimension."""
    from q2vt_plugin.src.core.maplibre_converter import QgisMapLibreStyleExporter
    from q2vt_plugin.src.core import maplibre_converter as mc
    from qgis.core import QgsPointPatternFillSymbolLayer, Qgis
    from PIL import ImageChops
    exporter = QgisMapLibreStyleExporter.__new__(QgisMapLibreStyleExporter)
    exporter.pattern_images, exporter.marker_counter = {}, 0
    exporter.profile = mc.ExportProfile()
    exporter.context = mc.ConversionContext(DiagnosticCollector())
    mc.PropertyExtractor.context = exporter.context
    monkeypatch.setattr(exporter, "TEXTURE_SCREEN_SCALE", 1.0)
    # QGIS's own texture brush (screen patterns) or the generic cell builder.
    monkeypatch.setattr(exporter, "_exact_screen_texture", exact, raising=False)
    cells = []
    for value, unit in ((50.0, Qgis.RenderUnit.Percentage), (8.0, Qgis.RenderUnit.Pixels)):
        layer = QgsPointPatternFillSymbolLayer()
        layer.setSubSymbol(QgsMarkerSymbol([QgsSimpleMarkerSymbolLayer()]))
        for name in ("DistanceX", "DistanceY"):
            getattr(layer, f"set{name}")(16.0)
            getattr(layer, f"set{name}Unit")(Qgis.RenderUnit.Pixels)
        for name in ("DisplacementX", "OffsetY"):
            getattr(layer, f"set{name}")(value)
            getattr(layer, f"set{name}Unit")(unit)
        cells.append(exporter.pattern_images[exporter._register_point_pattern(layer)].img_1x)
    assert cells[0].size == cells[1].size
    assert ImageChops.difference(cells[0], cells[1]).getbbox() is None
    assert not exporter.context.diagnostics.by_code("Q2VT_UNIT_PERCENTAGE")
