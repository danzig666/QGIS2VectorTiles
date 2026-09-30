"""PR-09: plain circular simple markers become native MapLibre circles."""

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
