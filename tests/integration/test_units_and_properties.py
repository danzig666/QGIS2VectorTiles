"""PR-02 regressions for property evaluation, units and enums (PyQGIS)."""

import math

import pytest
from qgis.core import (Qgis, QgsFeature, QgsGeometry, QgsLineSymbol, QgsMapRendererSequentialJob,
                       QgsMapSettings, QgsMarkerLineSymbolLayer, QgsProperty, QgsRectangle,
                       QgsSimpleLineSymbolLayer, QgsSimpleMarkerSymbolLayer,
                       QgsSingleSymbolRenderer, QgsSymbolLayer, QgsVectorLayer)
from qgis.PyQt.QtCore import QSize, Qt
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


@pytest.mark.parametrize("static,kind,expected", [
    (9.0, "number", ["to-number", ["coalesce", ["get", "q2vt_property_x_0"], 9.0], 9.0]),
    ("A", "string", ["to-string", ["coalesce", ["get", "q2vt_property_x_0"], "A"]]),
    (True, "boolean", ["to-boolean", ["coalesce", ["get", "q2vt_property_x_0"], True]])])
def test_a_null_generated_value_uses_the_static_one(mc, static, kind, expected):
    """A feature whose data-defined value is NULL (no tile attribute) gets
    the symbol's own value, as QGIS's valueAsDouble/String/Bool give it;
    MapLibre's to-number/to-string/to-boolean turn null into 0, '' and false."""
    assert mc.PropertyExtractor.get_value_or_expression(
        static, QgsProperty.fromExpression('"q2vt_property_x_0"'), kind) == expected


def test_field_and_expression_reference_are_equivalent(mc):
    field = "q2vt_property_size_5_00"
    by_field = mc.PropertyExtractor.get_value_or_expression(2, QgsProperty.fromField(field))
    by_expr = mc.PropertyExtractor.get_value_or_expression(
        2, QgsProperty.fromExpression(f'"{field}"'))
    assert by_field == by_expr == ["to-number", ["coalesce", ["get", field], 2], 2]


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


def test_around_point_candidates_in_qgis_order(mc):
    """QGIS tries top-right first, then right, top, bottom-right... (pal
    createCandidatesAroundPoint costs); cartographic placement follows the
    layer's own position order. MapLibre names the side at the point."""
    from qgis.core import QgsPalLayerSettings
    settings = QgsPalLayerSettings()
    settings.placement = Qgis.LabelPlacement.AroundPoint
    assert mc.TextPropertyExtractor.get_text_variable_anchor(settings) == [
        "bottom-left", "left", "bottom", "top-left", "bottom-right", "top", "right", "top-right"]
    settings.placement = Qgis.LabelPlacement.OrderedPositionsAroundPoint
    # The order as a project stores it (no Python API for it in every version).
    from qgis.core import QgsReadWriteContext
    from qgis.PyQt.QtXml import QDomDocument
    doc = QDomDocument()
    element = settings.writeXml(doc, QgsReadWriteContext())
    element.firstChildElement("placement").setAttribute("predefinedPositionOrder", "T,BL")
    ordered = QgsPalLayerSettings()
    ordered.readXml(element, QgsReadWriteContext())
    assert mc.TextPropertyExtractor.get_text_variable_anchor(ordered) == ["bottom", "top-right"]


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


def test_map_unit_translate_grows_with_the_map(mc):
    """A fill offset in map units drawn with a translate (a map unit scale
    limits it) is a zoom curve of [x, y] reaching past the last tile zoom,
    and the tile clip buffer covers it."""
    from q2vt_plugin.src.core.tiles_generator import GDALTilesGenerator  # pylint: disable=import-error
    shift = mc.PropertyExtractor.translate(30, 20, Qgis.RenderUnit.MapUnits)
    assert shift[:3] == ["interpolate", ["exponential", 2], ["zoom"]] and shift[-2] == 24
    px_per_unit_z16 = 512 * 2 ** 16 / 40075016.68557849
    z16 = GDALTilesGenerator._translate_px(shift, 16)  # pylint: disable=protected-access
    assert z16 == pytest.approx(math.hypot(30, 20) * px_per_unit_z16, rel=1e-3)
    assert mc.PropertyExtractor.translate(1, 2, Qgis.RenderUnit.Pixels) == [1, 2]
    assert GDALTilesGenerator._translate_px([3, 4], 16) == 5  # pylint: disable=protected-access


def _evaluate(expression, properties, scope=None):
    """The style expression forms keep_upright writes, evaluated."""
    scope = scope or {}
    if not isinstance(expression, list) or not expression or not isinstance(expression[0], str):
        return expression
    op, args = expression[0], expression[1:]

    def ev(value):
        return _evaluate(value, properties, scope)
    if op == "literal":
        return args[0]
    if op == "get":
        return properties.get(args[0])
    if op == "var":
        return scope[args[0]]
    if op == "let":
        return _evaluate(args[2], properties, {**scope, args[0]: ev(args[1])})
    if op in ("to-number", "coalesce"):
        values = [ev(a) for a in args]
        return next((float(v) for v in values if v is not None), 0.0)
    if op == "%":
        return math.fmod(ev(args[0]), ev(args[1]))
    if op in ("+", "-", ">=", "<"):
        a, b = ev(args[0]), ev(args[1])
        return {"+": a + b, "-": a - b, ">=": a >= b, "<": a < b}[op]
    if op == "all":
        return all(ev(a) for a in args)
    if op == "case":
        for i in range(0, len(args) - 1, 2):
            if ev(args[i]):
                return ev(args[i + 1])
        return ev(args[-1])
    raise AssertionError(op)


def test_upside_down_labels_are_turned_upright_as_qgis_does(mc):
    """QGIS ("Show upside-down labels: never", the default) turns a straight
    label whose clockwise angle lies in [90, 270) by 180 degrees within its
    box; MapLibre never turns point labels (text-keep-upright is for line
    placement). A fixed or data-defined angle was drawn upside down."""
    from qgis.core import QgsPalLayerSettings
    settings = QgsPalLayerSettings()
    settings.placement = Qgis.LabelPlacement.OverPoint
    settings.angleOffset = 150
    assert mc.TextPropertyExtractor.flips_upside_down(settings)
    layout = {"text-rotate": mc.TextPropertyExtractor.get_text_rotate(settings),
              "text-anchor": "bottom-left", "text-offset": [1.0, -0.5]}
    mc.TextPropertyExtractor.keep_upright(layout)
    # The box stays: the anchor and the offset turn with the label.
    assert layout == {"text-rotate": -30, "text-anchor": "top-right", "text-offset": [-1.0, 0.5]}
    upright = {"text-rotate": 60, "text-anchor": "bottom"}
    mc.TextPropertyExtractor.keep_upright(upright)
    assert upright == {"text-rotate": 60, "text-anchor": "bottom"}

    # A data-defined rotation as the export writes it (a dataset field).
    field = "q2vt_property_labelrotation"
    layout = {"text-rotate": ["to-number", ["coalesce", ["get", field], 0], 0],
              "text-anchor": "bottom", "text-offset": [0, -1.0]}
    mc.TextPropertyExtractor.keep_upright(layout)
    turned = {value: _evaluate(layout["text-rotate"], {field: value})
              for value in (0, 60, 90, 120, 200, 269, 270, 300, -100, 420)}
    assert turned == {0: 0, 60: 60, 90: -90, 120: -60, 200: 20, 269: 89, 270: 270, 300: 300,
                      -100: 80, 420: 60}
    assert _evaluate(layout["text-anchor"], {field: 120}) == "top"
    assert _evaluate(layout["text-anchor"], {field: 60}) == "bottom"
    assert _evaluate(layout["text-offset"], {field: 120}) == [0, 1.0]
    assert _evaluate(layout["text-offset"], {field: 300}) == [0, -1.0]

    # Curved labels are always upright; the other modes keep the angle.
    settings.placement = Qgis.LabelPlacement.Curved
    assert not mc.TextPropertyExtractor.flips_upside_down(settings)
    settings.placement = Qgis.LabelPlacement.OverPoint
    settings.upsidedownLabels = Qgis.UpsideDownLabelHandling.AlwaysAllowUpsideDown
    assert not mc.TextPropertyExtractor.flips_upside_down(settings)


def _dash_at(dasharray, zoom):
    """The ``line-dasharray`` MapLibre uses at ``zoom`` (a list or a step)."""
    if not isinstance(dasharray[0], str):
        return dasharray
    value = dasharray[2][1]
    for stop, array in zip(dasharray[3::2], dasharray[4::2]):
        if zoom >= stop:
            value = array[1]
    return value


def _px_per_metre(zoom):
    return 512 * 2 ** zoom / 40075016.68557849


def _dashed_line(width, width_unit, dashes, dash_unit, cap=Qt.PenCapStyle.FlatCap):
    line = QgsSimpleLineSymbolLayer(QColor("black"), width)
    line.setWidthUnit(width_unit)
    line.setPenCapStyle(cap)
    line.setUseCustomDashPattern(True)
    line.setCustomDashVector(dashes)
    line.setCustomDashPatternUnit(dash_unit)
    return line


def _line_style_exporter(mc):
    """A style converter for one symbol of a map exported from zoom 0 (the
    Publish window's default) to 16."""
    from fidelity.diagnostics import DiagnosticCollector
    exporter = mc.QgisMapLibreStyleExporter.__new__(mc.QgisMapLibreStyleExporter)
    exporter.pattern_images, exporter.marker_symbols, exporter.marker_counter = {}, {}, 0
    exporter.profile = mc.ExportProfile()
    exporter.context = mc.ConversionContext(DiagnosticCollector())
    exporter.style, exporter.maxzoom = {"layers": []}, 16
    mc.PropertyExtractor.context = exporter.context
    exporter.context.reference_zoom = 0
    return exporter


def test_screen_dashes_on_a_map_unit_width_follow_the_zoom(mc):
    """MapLibre multiplies line-dasharray by the line width. A 1 m wide line
    with 3/2 mm dashes: the ratio was computed once with the width at the
    style's first zoom (1.3e-5 px from zoom 0), so the dashes were a million
    widths long and the line solid at every zoom. QGIS keeps 3 mm dashes."""
    from qgis.core import QgsLineSymbol
    line = _dashed_line(1.0, Qgis.RenderUnit.MapUnits, [3.0, 2.0], Qgis.RenderUnit.Millimeters)
    exporter = _line_style_exporter(mc)
    exporter._convert_symbol(QgsLineSymbol([line]), "s", "src", "q2vt", 0, 24)
    paint = exporter.style["layers"][0]["paint"]
    width, dasharray = paint["line-width"], paint["line-dasharray"]
    assert dasharray[:2] == ["step", ["zoom"]]
    for zoom in (14.0, 16.0, 17.5, 18.0):
        dash, gap = _dash_at(dasharray, zoom)
        width_px = ex.evaluate_zoom_curve(width, zoom)
        # Within 2^0.25 of QGIS's 3 / 2 mm (half-zoom steps).
        assert dash * width_px == pytest.approx(3 * 96 / 25.4, rel=0.19)
        assert gap * width_px == pytest.approx(2 * 96 / 25.4, rel=0.19)


def test_map_unit_dashes_with_corner_tweak_follow_the_zoom(mc):
    """10 m dashes on a 0.5 mm line with "tweak dash pattern at corners"
    are not cut into dash geometry: the native dasharray took the dash
    length at the style's first zoom (1e-4 px from zoom 0) and, with the
    square cap, became [1.0001, 0] - a solid line at every zoom."""
    from qgis.core import QgsLineSymbol
    line = _dashed_line(0.5, Qgis.RenderUnit.Millimeters, [10.0, 10.0],
                        Qgis.RenderUnit.MapUnits, Qt.PenCapStyle.SquareCap)
    line.setTweakDashPatternOnCorners(True)
    exporter = _line_style_exporter(mc)
    exporter._convert_symbol(QgsLineSymbol([line]), "s", "src", "q2vt", 0, 24)
    paint = exporter.style["layers"][0]["paint"]
    width = paint["line-width"]
    assert width == pytest.approx(0.5 * 96 / 25.4, rel=1e-3)
    for zoom in (16.0, 18.0, 21.0):
        dash, gap = _dash_at(paint["line-dasharray"], zoom)
        # Qt draws a square cap on every dash: one width added to it here.
        assert (dash - 1) * width == pytest.approx(10 * _px_per_metre(zoom), rel=0.19)
        assert (gap + 1) * width == pytest.approx(10 * _px_per_metre(zoom), rel=0.19)


@pytest.mark.parametrize("unit", [Qgis.RenderUnit.Millimeters, Qgis.RenderUnit.MapUnits])
def test_dashes_in_the_line_width_unit_stay_one_array(mc, unit):
    line = _dashed_line(1.0, unit, [3.0, 2.0], unit)
    width = mc.LinePropertyExtractor.get_line_width(line)
    assert mc.LinePropertyExtractor.get_line_dasharray(line, width, 0, 24) == [3.0, 2.0]
