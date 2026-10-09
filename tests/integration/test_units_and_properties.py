"""PR-02 regressions for property evaluation, units and enums (PyQGIS)."""

import pytest
from qgis.core import (Qgis, QgsFeature, QgsGeometry, QgsLineSymbol, QgsMapRendererSequentialJob,
                       QgsMapSettings, QgsMarkerLineSymbolLayer, QgsProperty, QgsRectangle,
                       QgsSimpleLineSymbolLayer, QgsSimpleMarkerSymbolLayer,
                       QgsSingleSymbolRenderer, QgsSymbolLayer, QgsVectorLayer)
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor

from fidelity import expressions as ex


@pytest.fixture
def mc(plugin):
    from q2vt_plugin.src.core import maplibre_converter  # pylint: disable=import-error
    from fidelity.diagnostics import DiagnosticCollector
    maplibre_converter.PropertyExtractor.context = maplibre_converter.ConversionContext(
        DiagnosticCollector())
    return maplibre_converter


@pytest.mark.parametrize("expression,expected", [("0", 0.0), ("0.0", 0.0), ("3", 3.0)])
def test_falsy_numeric_literals_are_preserved(mc, expression, expected):
    prop = QgsProperty.fromExpression(expression)
    assert mc.PropertyExtractor.get_value_or_expression(5, prop) == expected


def test_false_and_empty_string_are_preserved(mc):
    assert mc.PropertyExtractor.get_value_or_expression(
        True, QgsProperty.fromExpression("false")) is False
    assert mc.PropertyExtractor.get_value_or_expression(
        "x", QgsProperty.fromExpression("''")) == ""


def test_null_uses_static_value_and_eval_error_is_reported(mc):
    ctx = mc.PropertyExtractor.context
    assert mc.PropertyExtractor.get_value_or_expression(5, QgsProperty.fromExpression("NULL")) == 5
    assert not ctx.diagnostics.items
    assert mc.PropertyExtractor.get_value_or_expression(
        5, QgsProperty.fromExpression("to_int('abc') + array(1)")) == 5
    assert ctx.diagnostics.by_code("Q2VT_DDP_EVAL_ERROR")


def test_quoted_static_number_becomes_a_number(mc):
    # Legacy: ddp_fetcher stored static values as "'3'", yielding the string '3'.
    assert mc.PropertyExtractor.get_value_or_expression(
        1, QgsProperty.fromExpression("'3'")) == 3.0


def test_field_and_expression_reference_are_equivalent(mc):
    field = "q2vt_property_size_5_00"
    by_field = mc.PropertyExtractor.get_value_or_expression(2, QgsProperty.fromField(field))
    by_expr = mc.PropertyExtractor.get_value_or_expression(
        2, QgsProperty.fromExpression(f'"{field}"'))
    assert by_field == by_expr == ["to-number", ["get", field], 2]


def test_color_field_reference_is_typed(mc):
    field = "q2vt_property_fill_color_3_00"
    prop = QgsProperty.fromExpression(
        f"with_variable('color', \"{field}\", '#' || substr(@color,8,2) || substr(@color,2,6))")
    out = mc.PropertyExtractor.get_value_or_expression("rgba(0, 0, 0, 1)", prop, "color")
    assert out == ["to-color", ["get", field], "rgba(0, 0, 0, 1)"]


def test_data_defined_icon_size_is_a_valid_expression(mc):
    layer = QgsSimpleMarkerSymbolLayer()
    layer.setSize(4)
    layer.setDataDefinedProperty(QgsSymbolLayer.Property.PropertySize,
                                 QgsProperty.fromField("q2vt_property_size_5_00"))
    size = mc.IconPropertyExtractor.get_icon_size(layer, 1.0)
    assert isinstance(size, list)
    ex.validate_zoom_usage(size)
    # Static size 4 at feature value 8 => icon-size 2 (logical sprite size = static size).
    assert "q2vt_property_size_5_00" in ex.referenced_fields(size)


def test_line_width_units(mc):
    layer = QgsSimpleLineSymbolLayer()
    layer.setWidth(1.0)
    layer.setWidthUnit(Qgis.RenderUnit.Millimeters)
    assert mc.LinePropertyExtractor.get_line_width(layer) == pytest.approx(96 / 25.4)
    layer.setWidthUnit(Qgis.RenderUnit.Points)
    assert mc.LinePropertyExtractor.get_line_width(layer) == pytest.approx(96 / 72)
    layer.setWidth(0)
    assert mc.LinePropertyExtractor.get_line_width(layer) == 1.0  # QGIS hairline
    layer.setWidth(1)
    layer.setWidthUnit(Qgis.RenderUnit.MapUnits)
    curve = mc.LinePropertyExtractor.get_line_width(layer)
    assert ex.is_zoom_curve(curve)  # legacy: treated as millimetres (3.78)


def test_data_defined_width_is_converted_from_its_unit(mc):
    layer = QgsSimpleLineSymbolLayer()
    layer.setWidth(0.5)
    layer.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyStrokeWidth,
                                 QgsProperty.fromField("q2vt_property_stroke_width_1_00"))
    width = mc.LinePropertyExtractor.get_line_width(layer)
    # ["case", [== w 0], 1 (hairline), w] with w = value * px-per-mm
    assert width[0] == "case" and width[2] == 1
    scaled = width[3]
    assert scaled[0] == "*" and scaled[2] == pytest.approx(96 / 25.4)


def test_data_defined_opacity_is_percent(mc):
    layer = QgsSimpleLineSymbolLayer()
    layer.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyOpacity,
                                 QgsProperty.fromField("q2vt_property_opacity_35_00"))
    out = mc.LinePropertyExtractor.get_line_opacity(layer, QgsLineSymbol())
    assert ex.referenced_fields(out) == {"q2vt_property_opacity_35_00"}
    assert "0.01" in str(out)  # divided by 100


@pytest.mark.parametrize("placement,expected", [
    (Qgis.MarkerLinePlacement.Interval, "line"),
    (Qgis.MarkerLinePlacement.CentralPoint, "line-center"),
    (Qgis.MarkerLinePlacement.LastVertex, "line"),
])
def test_marker_line_placement_uses_named_flags(mc, placement, expected):
    layer = QgsMarkerLineSymbolLayer()
    layer.setPlacements(placement)
    assert mc.LinePropertyExtractor.get_marker_line_symbol_placement(layer) == expected
    approx = mc.PropertyExtractor.context.diagnostics.by_code("Q2VT_MARKER_PLACEMENT_APPROX")
    assert bool(approx) == (placement == Qgis.MarkerLinePlacement.LastVertex)


def test_unknown_unit_is_reported_not_millimetres(mc):
    out = mc.PropertyExtractor.length(2.0, Qgis.RenderUnit.Unknown)
    assert out == 2.0
    assert mc.PropertyExtractor.context.diagnostics.by_code("Q2VT_UNIT_UNKNOWN")


def test_round_numeric_values_keeps_strings(mc, tmp_path):
    exporter = mc.QgisMapLibreStyleExporter.__new__(mc.QgisMapLibreStyleExporter)
    data = {"a": ["get", "2020"], "b": 0.000012345, "c": 1.23456789, "d": True, "e": "07"}
    out = exporter.round_numeric_values(data)
    assert out == {"a": ["get", "2020"], "b": 1.234e-05, "c": 1.2346, "d": True, "e": "07"}


def _render_offset_rows(offset_px):
    layer = QgsVectorLayer("LineString?crs=EPSG:3857", "l", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt("LINESTRING(-50 0, 50 0)"))
    layer.dataProvider().addFeatures([feature])
    line = QgsSimpleLineSymbolLayer(QColor("red"), 2)
    line.setWidthUnit(Qgis.RenderUnit.Pixels)
    line.setOffset(offset_px)
    line.setOffsetUnit(Qgis.RenderUnit.Pixels)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([line])))
    settings = QgsMapSettings()
    settings.setLayers([layer])
    settings.setDestinationCrs(layer.crs())
    settings.setExtent(QgsRectangle(-100, -100, 100, 100))
    settings.setOutputSize(QSize(200, 200))
    settings.setBackgroundColor(QColor("white"))
    job = QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    img = job.renderedImage()
    return sorted({y for y in range(img.height()) if QColor(img.pixel(100, y)).green() < 200})


def test_positive_line_offset_is_right_of_direction_like_maplibre(mc):
    rows = _render_offset_rows(10)
    assert rows and min(rows) > 100  # image y down: below an eastward line = its right side
    layer = QgsSimpleLineSymbolLayer()
    layer.setOffset(10)
    layer.setOffsetUnit(Qgis.RenderUnit.Pixels)
    assert mc.LinePropertyExtractor.get_line_offset(layer) == 10


def _label_settings(buffer_size, buffer_unit, text_px=40):
    from qgis.core import QgsPalLayerSettings, QgsTextBufferSettings, QgsTextFormat
    settings = QgsPalLayerSettings()
    text = QgsTextFormat()
    text.setSize(text_px)
    text.setSizeUnit(Qgis.RenderUnit.Pixels)
    buffer = QgsTextBufferSettings()
    buffer.setEnabled(True)
    buffer.setSize(buffer_size)
    buffer.setSizeUnit(buffer_unit)
    text.setBuffer(buffer)
    settings.setFormat(text)
    return settings, text


def test_label_halo_is_half_the_qgis_buffer(mc):
    """QGIS strokes the glyph outline with a pen as wide as the buffer size
    (measured: a 10 px buffer reaches 5 px beyond the glyphs); a MapLibre
    halo reaches its full width. Percentages are of the text size."""
    settings, text = _label_settings(10, Qgis.RenderUnit.Pixels)
    assert mc.TextPropertyExtractor.get_text_halo_width(text, settings) == pytest.approx(5.0)
    settings, text = _label_settings(10, Qgis.RenderUnit.Percentage)
    assert mc.TextPropertyExtractor.get_text_halo_width(text, settings) == pytest.approx(2.0)


def test_single_line_label_placement(mc):
    from qgis.core import QgsPalLayerSettings
    settings = QgsPalLayerSettings()
    settings.placement = Qgis.LabelPlacement.Line
    assert mc.IconPropertyExtractor.get_symbol_placement(settings) == "line-center"
    settings.repeatDistance = 100
    assert mc.IconPropertyExtractor.get_symbol_placement(settings) == "line"
    line = settings.lineSettings()
    line.setPlacementFlags(Qgis.LabelLinePlacementFlags(
        Qgis.LabelLinePlacementFlag.AboveLine | Qgis.LabelLinePlacementFlag.MapOrientation))
    settings.setLineSettings(line)
    assert mc.TextPropertyExtractor.get_text_anchor(settings) == "bottom"
    settings.multilineAlign = Qgis.LabelMultiLineAlignment.Left
    assert mc.TextPropertyExtractor.get_text_justify(settings) == "left"


def test_map_unit_label_repeat_grows_with_the_map(mc):
    """A 200 m repeat distance is 200 m at every zoom: the label spacing
    doubles per zoom (one zoom's pixels repeated road names end to end
    further in); a millimetre repeat stays the same on screen."""
    from qgis.core import QgsPalLayerSettings
    settings = QgsPalLayerSettings()
    settings.placement = Qgis.LabelPlacement.Curved
    settings.repeatDistance = 200
    settings.repeatDistanceUnit = Qgis.RenderUnit.MapUnits
    spacing = mc.IconPropertyExtractor.get_symbol_spacing(settings)
    assert not ex.is_number(spacing)
    at = {zoom: ex.evaluate_zoom_curve(spacing, zoom) for zoom in (14, 15)}
    assert at[15] == pytest.approx(2 * at[14], rel=0.01)
    assert at[14] == pytest.approx(200 / (40075016.68557849 / (512 * 2 ** 14)), rel=0.02)
    settings.repeatDistance = 70
    settings.repeatDistanceUnit = Qgis.RenderUnit.Millimeters
    assert mc.IconPropertyExtractor.get_symbol_spacing(settings) == pytest.approx(70 * 96 / 25.4)


def test_label_text_replacements_match_qgis(plugin):
    """Szabályozás övezetkódok: the layer's text replacements ("Ut" -> "Köu",
    whole-word "Z" -> "Zkp", ...) were not applied to the exported labels."""
    from qgis.core import (QgsExpression, QgsExpressionContext, QgsStringReplacement,
                           QgsStringReplacementCollection)
    from q2vt_plugin.src.core.rules_exporter import RulesExporter  # pylint: disable=import-error
    collection = QgsStringReplacementCollection([
        QgsStringReplacement("Vasut", "Kök", False, False),
        QgsStringReplacement("Ut", "Köu", False, False),
        QgsStringReplacement("Z", "Zkp", False, True),
        QgsStringReplacement("Gip", "Gipe", False, False),
        QgsStringReplacement("Eg", "Ee", False, True),
        QgsStringReplacement("a.b", "x'y", True, False),
        QgsStringReplacement("(1)", "[1]", False, True),
    ])
    for text in ["Ut-4", "Vasut", "ut", "Z", "Zöld", "Z-1", "Eg", "Egy", "eg 2", "Gip-x",
                 "a.b axb", "Lk (1)", "", "Mt"]:
        literal = "'" + text.replace("'", "''") + "'"
        expression = QgsExpression(RulesExporter._substituted(literal, collection))
        assert not expression.hasParserError(), expression.parserErrorString()
        assert expression.evaluate(QgsExpressionContext()) == collection.process(text), text
