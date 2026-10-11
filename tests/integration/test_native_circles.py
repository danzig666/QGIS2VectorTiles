"""PR-09: plain circular simple markers become native MapLibre circles."""

import json

import pytest
from qgis.core import (Qgis, QgsMarkerSymbol, QgsProperty, QgsSimpleMarkerSymbolLayer,
                       QgsSymbolLayer)
from qgis.PyQt.QtCore import QPointF, Qt
from qgis.PyQt.QtGui import QColor

from fidelity import expressions as ex
from fidelity.diagnostics import DiagnosticCollector

MM = 96 / 25.4


@pytest.fixture
def exporter(plugin):
    from q2vt_plugin.src.core import maplibre_converter as mc  # pylint: disable=import-error
    exporter = mc.QgisMapLibreStyleExporter.__new__(mc.QgisMapLibreStyleExporter)
    exporter.pattern_images, exporter.marker_symbols, exporter.marker_counter = {}, {}, 0
    exporter.profile = mc.ExportProfile()
    exporter.context = mc.ConversionContext(DiagnosticCollector())
    exporter.style = {"layers": []}
    mc.PropertyExtractor.context = exporter.context
    return exporter


def _circle(size=4.0, stroke=0.4, fill="#ff0000", stroke_color="#000000"):
    layer = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, size)
    layer.setColor(QColor(fill))
    layer.setStrokeColor(QColor(stroke_color))
    layer.setStrokeWidth(stroke)
    layer.setStrokeWidthUnit(Qgis.RenderUnit.Millimeters)
    return QgsMarkerSymbol([layer])


def _convert(exporter, symbol):
    exporter._convert_symbol(symbol, "s", "src_layer", "src", 10, 16)
    return exporter.style["layers"]


def test_simple_circle_is_native(exporter):
    layers = _convert(exporter, _circle())
    assert [l["type"] for l in layers] == ["circle"]
    paint = layers[0]["paint"]
    # QGIS centres the stroke on the edge; MapLibre draws it outside the radius.
    assert paint["circle-radius"] == pytest.approx((4.0 - 0.4) / 2 * MM)
    assert paint["circle-stroke-width"] == pytest.approx(0.4 * MM)
    assert paint["circle-color"] == "rgba(255, 0, 0, 1.0)"
    assert paint["circle-stroke-color"] == "rgba(0, 0, 0, 1.0)"
    assert not exporter.marker_symbols


def test_hairline_and_no_pen(exporter):
    symbol = _circle(stroke=0.0)
    paint = _convert(exporter, symbol)[0]["paint"]
    assert paint["circle-stroke-width"] == 1.0
    symbol = _circle()
    symbol.symbolLayer(0).setStrokeStyle(Qt.PenStyle.NoPen)
    exporter.style["layers"] = []
    paint = _convert(exporter, symbol)[0]["paint"]
    assert "circle-stroke-width" not in paint
    assert paint["circle-radius"] == pytest.approx(2.0 * MM)


def test_data_defined_size_and_color(exporter):
    symbol = _circle()
    layer = symbol.symbolLayer(0)
    layer.setDataDefinedProperty(QgsSymbolLayer.Property.PropertySize,
                                 QgsProperty.fromField("q2vt_property_size_0_00"))
    layer.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyFillColor,
                                 QgsProperty.fromField("q2vt_property_fill_color_0_00"))
    paint = _convert(exporter, symbol)[0]["paint"]
    assert "q2vt_property_size_0_00" in ex.referenced_fields(paint["circle-radius"])
    assert paint["circle-color"][0] == "to-color"
    ex.validate_zoom_usage(paint["circle-radius"])


def test_map_unit_circle_scales_with_zoom(exporter):
    symbol = _circle(size=20.0, stroke=0.0)
    symbol.symbolLayer(0).setStrokeStyle(Qt.PenStyle.NoPen)
    symbol.symbolLayer(0).setSizeUnit(Qgis.RenderUnit.MapUnits)
    radius = _convert(exporter, symbol)[0]["paint"]["circle-radius"]
    assert ex.is_zoom_curve(radius)
    assert ex.evaluate_zoom_curve(radius, 17) == pytest.approx(
        2 * ex.evaluate_zoom_curve(radius, 16), rel=1e-6)


@pytest.mark.parametrize("change", ["square", "offset", "dash", "translucent_stroke", "two_layers"])
def test_ineligible_markers_stay_sprites(exporter, change):
    symbol = _circle()
    layer = symbol.symbolLayer(0)
    if change == "square":
        layer.setShape(Qgis.MarkerShape.Square)
    elif change == "offset":
        layer.setOffset(QPointF(1, 0))
    elif change == "dash":
        layer.setStrokeStyle(Qt.PenStyle.DashLine)
    elif change == "translucent_stroke":
        layer.setStrokeColor(QColor(0, 0, 0, 100))
    elif change == "two_layers":
        symbol.appendSymbolLayer(QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, 2))
    layers = _convert(exporter, symbol)
    assert layers[0]["type"] == "symbol"


@pytest.mark.parametrize("char,as_text", [("A", True), ("\U0001F815", False)])
def test_font_markers_beyond_the_bmp_become_sprites(exporter, char, as_text):
    """MapLibre glyph ranges end at U+FFFF (QGIS draws such characters from
    a fallback font): those font markers are exported as images."""
    from qgis.core import QgsFontMarkerSymbolLayer
    exporter.utils_dir, exporter.glyphs = "", {}
    layer = QgsFontMarkerSymbolLayer("DejaVu Sans", char, 4)
    layers = _convert(exporter, QgsMarkerSymbol([layer]))
    assert ("text-field" in layers[0]["layout"]) == as_text
    assert ("icon-image" in layers[0]["layout"]) != as_text


def test_map_unit_sprites_are_drawn_per_zoom(exporter):
    """No GPU mipmaps for sprites: one image per zoom, drawn at that zoom's
    size, keeps MapLibre's scaling within 0.67-1.33x (an image oversampled
    24x aliased: thin outlines broke into dots)."""
    exporter.maxzoom = 16
    symbol = _circle(size=12.0, stroke=0.2)
    symbol.symbolLayer(0).setShape(Qgis.MarkerShape.Square)
    symbol.symbolLayer(0).setSizeUnit(Qgis.RenderUnit.MapUnits)
    symbol.symbolLayer(0).setStrokeWidthUnit(Qgis.RenderUnit.MapUnits)
    layers = _convert(exporter, symbol)
    assert [(l["minzoom"], l["maxzoom"]) for l in layers] == \
        [(10, 11), (11, 12), (12, 13), (13, 14), (14, 15), (15, 16)]
    requests = [exporter.marker_symbols[l["layout"]["icon-image"]] for l in layers]
    assert [r.oversampling for r in requests] == [1.5] * 6
    # Each image is drawn for its own zoom: 2x the map units per pixel below
    # (from z14, where the marker is over one pixel; smaller ones stay 1 px).
    ratios = [a.map_units_per_pixel / b.map_units_per_pixel for a, b in zip(requests, requests[1:])]
    assert ratios[-2:] == pytest.approx([2.0] * 2)
    # Past the archive's zooms + 3, one image drawn with more oversampling.
    exporter.style["layers"] = []
    layers = exporter._convert_symbol(symbol, "o", "src_layer", "src", 14, 24) or \
        exporter.style["layers"]
    assert (layers[-1]["minzoom"], layers[-1]["maxzoom"]) == (19, 24)
    assert exporter.marker_symbols[layers[-1]["layout"]["icon-image"]].oversampling > 1.5
    # A marker already large gets no further images (the sprite sheet must
    # fit a GPU texture): the last one is scaled up.
    symbol.symbolLayer(0).setSize(150.0)
    exporter.style["layers"] = []
    _ = exporter._convert_symbol(symbol, "big", "src_layer", "src", 14, 24)
    layers = exporter.style["layers"]
    assert layers[-1]["maxzoom"] == 24 and layers[-1]["minzoom"] <= 17
    for layer in layers:
        request = exporter.marker_symbols[layer["layout"]["icon-image"]]
        assert 150.0 / request.map_units_per_pixel * request.oversampling <= 1.5 * 256


def test_static_screen_sprites_are_drawn_one_to_one(exporter):
    symbol = _circle()
    symbol.symbolLayer(0).setShape(Qgis.MarkerShape.Square)
    layer = _convert(exporter, symbol)[0]
    assert exporter.marker_symbols[layer["layout"]["icon-image"]].oversampling == 1.0


def test_marker_sized_by_map_unit_extents_grows_with_the_map(exporter):
    """An ellipse whose width and height are in map units grows with the map
    even though its nominal size unit is millimetres (a zoning-code box)."""
    from qgis.core import QgsEllipseSymbolLayer
    exporter.maxzoom = 16
    box = QgsEllipseSymbolLayer()
    box.setShape(Qgis.MarkerShape.Square)
    box.setSize(30)
    box.setSizeUnit(Qgis.RenderUnit.Millimeters)
    box.setSymbolWidth(28)
    box.setSymbolHeight(18)
    box.setSymbolWidthUnit(Qgis.RenderUnit.MapUnits)
    box.setSymbolHeightUnit(Qgis.RenderUnit.MapUnits)
    layers = _convert(exporter, QgsMarkerSymbol([box]))
    size = layers[-1]["layout"]["icon-size"]
    assert ex.is_zoom_curve(size)
    assert ex.evaluate_zoom_curve(size, 16.5) == pytest.approx(
        2 ** 0.5 * ex.evaluate_zoom_curve(size, 16), rel=1e-3)


def test_data_defined_symbol_opacity_of_a_sprite_is_the_icon_opacity(exporter):
    """QgsMarkerSymbol::renderPoint replaces the symbol's opacity by its
    data-defined Opacity per feature: the sprite (drawn without a feature)
    is opaque and icon-opacity reads the attribute."""
    from qgis.core import QgsSymbol
    symbol = _circle()
    symbol.symbolLayer(0).setShape(Qgis.MarkerShape.Square)
    symbol.setOpacity(0.5)
    key = getattr(QgsSymbol.Property, "Opacity", None) or QgsSymbol.PropertyOpacity
    symbol.setDataDefinedProperty(key, QgsProperty.fromField("q2vt_property_alpha_0_00"))
    layer = _convert(exporter, symbol)[0]
    opacity = layer["paint"]["icon-opacity"]
    assert ex.referenced_fields(opacity) == {"q2vt_property_alpha_0_00"}
    # A NULL value keeps the static opacity (valueAsDouble's default).
    assert '["coalesce", ["get", "q2vt_property_alpha_0_00"], 50.0]' in json.dumps(opacity)
    assert exporter.marker_symbols[layer["layout"]["icon-image"]].symbol.opacity() == 1.0
    assert symbol.opacity() == 0.5  # the converted symbol itself is left alone
    # Without one the static opacity stays in the sprite, applied once.
    symbol.setDataDefinedProperty(key, QgsProperty())
    exporter.style["layers"] = []
    layer = _convert(exporter, symbol)[0]
    assert layer["paint"]["icon-opacity"] == 1.0
    assert exporter.marker_symbols[layer["layout"]["icon-image"]].symbol.opacity() == 0.5


def test_data_defined_raster_marker_opacity_is_the_icon_opacity(exporter, tmp_path, monkeypatch):
    """QgsRasterMarkerSymbolLayer reads its own data-defined opacity (other
    markers ignore it): one raster layer gets it as icon-opacity; one among
    several changes the image, so each value is its own sprite variant."""
    from qgis.core import QgsRasterMarkerSymbolLayer
    from PIL import Image
    path = str(tmp_path / "cell.png")
    Image.new("RGBA", (8, 8), (0, 0, 200, 255)).save(path)
    raster = QgsRasterMarkerSymbolLayer(path, 6)
    raster.setOpacity(0.4)
    raster.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyOpacity,
                                  QgsProperty.fromField("q2vt_property_alpha_35_00"))
    second = raster.clone()
    layer = _convert(exporter, QgsMarkerSymbol([raster]))[0]
    assert ex.referenced_fields(layer["paint"]["icon-opacity"]) == {"q2vt_property_alpha_35_00"}
    # NULL keeps the layer's static opacity.
    assert '["coalesce", ["get", "q2vt_property_alpha_35_00"], 40.0]' in \
        json.dumps(layer["paint"]["icon-opacity"])
    sprite = exporter.marker_symbols[layer["layout"]["icon-image"]].symbol
    assert sprite.symbolLayer(0).opacity() == 1.0
    asked = []

    def distinct(_source, fields):
        asked.append(fields)

    monkeypatch.setattr(exporter, "_distinct_values", distinct)
    exporter.style["layers"] = []
    circle = _circle()
    two = QgsMarkerSymbol([second, circle.symbolLayer(0).clone()])
    layer = _convert(exporter, two)[0]
    assert asked == [["q2vt_property_alpha_35_00"]]  # its values are sprite variants
    assert layer["paint"]["icon-opacity"] == 1.0
