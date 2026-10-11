"""Zoom <-> scale and unit conversions as QGIS does them: a geographic project
CRS, a symbology reference scale and geometry generators in screen units
(Publish defaults: zooms 0-16, overzoom to 22)."""

import json
import math
import os

import pytest
from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsExpression,
                       QgsExpressionContext, QgsFeature, QgsField, QgsFillSymbol,
                       QgsGeometry, QgsGeometryGeneratorSymbolLayer, QgsLineSymbol,
                       QgsMarkerSymbol, QgsPalLayerSettings, QgsProcessingFeedback,
                       QgsRectangle, QgsRuleBasedRenderer, QgsScaleCalculator,
                       QgsSimpleFillSymbolLayer, QgsSimpleLineSymbolLayer,
                       QgsSimpleMarkerSymbolLayer, QgsSingleSymbolRenderer, QgsTextFormat,
                       QgsVectorLayer, QgsVectorLayerSimpleLabeling)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor

from fidelity import expressions as ex
from q2vt_fixtures import reset_project, to_geopackage

CX, CY = 2120500.0, 6020500.0  # Web Mercator, about 19 E 47.5 N
EXTENT = QgsRectangle(CX - 2000, CY - 2000, CX + 2000, CY + 2000)
EARTH = 40075016.68557849
LON = math.degrees(CX / 6378137.0)
LAT = math.degrees(2 * math.atan(math.exp(CY / 6378137.0)) - math.pi / 2)


def _layer(path, kind, wkts, crs="EPSG:3857", fields=()):
    layer = QgsVectorLayer(f"{kind}?crs={crs}", "lyr", "memory")
    layer.dataProvider().addAttributes([QgsField(name, QVariant.String) for name in fields])
    layer.updateFields()
    features = []
    for wkt in wkts:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        if fields:
            feature.setAttributes(["Abc"] * len(fields))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    return to_geopackage(layer, path)


def _export(tmp_path, min_zoom=0, max_zoom=16):
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-error
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    exporter = QGIS2VectorTiles(min_zoom=min_zoom, max_zoom=max_zoom, extent=EXTENT,
                                output_dir=str(out), feedback=QgsProcessingFeedback(), serve=False)
    result = exporter.convert_project_to_vector_tiles()
    with open(os.path.join(result, "style", "style.json"), encoding="utf-8") as handle:
        return exporter, json.load(handle)


@pytest.fixture(autouse=True)
def _web_mercator_zooms(plugin):
    """An export configures the zoom scales of its project CRS: back to the
    Web Mercator defaults after each test."""
    yield
    from q2vt_plugin.src.utils.zoom_levels import ZoomLevels  # pylint: disable=import-error
    from q2vt_plugin.src.core.fidelity.zoom import WEB_MERCATOR_TOP_SCALE  # pylint: disable=import-error
    ZoomLevels.configure(WEB_MERCATOR_TOP_SCALE)


def _data_layers(style, kind=None):
    return [l for l in style["layers"] if l.get("source-layer") and (kind is None or l["type"] == kind)]


# --- geographic project CRS ---------------------------------------------------

def _geographic_project(layer):
    project = reset_project(layer)
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    return project


def test_geographic_zoom_scale_matches_qgis_scale_calculator(plugin, tmp_path):
    """QGIS shows metric scales in an EPSG:4326 project; zoom 0 had scale
    2657 (295.8M / 111319), so the export mapped QGIS's scale of web zoom 16
    to zoom 0.3."""
    from q2vt_plugin.src.core.fidelity import zoom as fidelity_zoom  # pylint: disable=import-error
    d = 0.004
    ring = [(LON - d, LAT - d), (LON + d, LAT - d), (LON + d, LAT + d), (LON - d, LAT + d)]
    wkt = "POLYGON((" + ", ".join(f"{x} {y}" for x, y in ring + ring[:1]) + "))"
    layer = _layer(str(tmp_path / "p.gpkg"), "Polygon", [wkt], crs="EPSG:4326")
    root = QgsRuleBasedRenderer.Rule(None)
    for color, low, high in (("255,0,0", 5000, 0), ("0,0,255", 0, 5000)):
        rule = QgsRuleBasedRenderer.Rule(QgsFillSymbol.createSimple({"color": color}))
        rule.setMinimumScale(low)
        rule.setMaximumScale(high)
        root.appendChild(rule)
    layer.setRenderer(QgsRuleBasedRenderer(root))
    _geographic_project(layer)
    _, style = _export(tmp_path)
    # QGIS's own scale of the browser's ground resolution at zoom 16 (360/512
    # degrees per px at zoom 0, 512 px wide, 96 dpi).
    calculator = QgsScaleCalculator(96, Qgis.DistanceUnit.Degrees)
    width = 360.0 / 2 ** 16
    qgis_scale = calculator.calculate(QgsRectangle(LON - width / 2, LAT - 0.1, LON + width / 2, LAT + 0.1), 512)
    assert fidelity_zoom.scale_to_zoom(qgis_scale) == pytest.approx(16, abs=0.01)
    fills = _data_layers(style, "fill")
    colours = {l["paint"]["fill-color"]: l for l in fills}
    assert len(colours) == 2  # the zoomed-out (blue) rule was dropped
    red = next(l for c, l in colours.items() if c.startswith("rgba(255, 0, 0"))
    blue = next(l for c, l in colours.items() if c.startswith("rgba(0, 0, 255"))
    assert 14.4 < red["minzoom"] < 14.9
    assert blue.get("minzoom", 0) == 0 and blue["maxzoom"] == pytest.approx(red["minzoom"])


def test_geographic_screen_millimetres_are_qgis_degrees_per_mm(plugin):
    """Screen-unit materializations (arrows, offsets, grids) in a degree
    CRS: QGIS's map units per mm are degrees per px x px per mm; they were
    about 1e5 times too small (arrows vanished)."""
    from q2vt_plugin.src.core.materializer import SymbolMaterializer  # pylint: disable=import-error
    from q2vt_plugin.src.core.fidelity.diagnostics import DiagnosticCollector  # pylint: disable=import-error
    layer = QgsVectorLayer("LineString?crs=EPSG:4326", "lyr", "memory")
    _geographic_project(layer)
    per_mm = SymbolMaterializer(DiagnosticCollector())._map_units_per_mm(None, 15.0, layer)  # pylint: disable=protected-access
    assert per_mm == pytest.approx(360.0 / 512 / 2 ** 15 * 96 / 25.4, rel=1e-6)


def _degrees_per_mm(zoom):
    return 360.0 / 512 / 2 ** zoom * 96 / 25.4


def test_geographic_screen_lengths_of_marker_lines_are_degrees(plugin, tmp_path):
    """Interval markers spaced in mm and vertex markers offset in mm in a
    degree CRS: the zoom's metric scale (QGIS's) was taken as degrees, so
    the spacing and offset came out tens of thousands of times too large
    (no markers on the web)."""
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener  # pylint: disable=import-error
    from q2vt_plugin.src.core.rules_exporter import RulesExporter  # pylint: disable=import-error
    from q2vt_plugin.src.core.fidelity.diagnostics import DiagnosticCollector  # pylint: disable=import-error
    from q2vt_plugin.src.core.fidelity.units import MapUnitContext  # pylint: disable=import-error
    from q2vt_plugin.src.utils.zoom_levels import ZoomLevels  # pylint: disable=import-error
    from qgis.core import QgsMarkerLineSymbolLayer
    d = 0.002
    wkt = f"LINESTRING({LON - d} {LAT}, {LON} {LAT + d}, {LON + d} {LAT}, {LON + 2 * d} {LAT + d})"
    layer = _layer(str(tmp_path / "l.gpkg"), "LineString", [wkt], crs="EPSG:4326")
    circle = QgsMarkerSymbol([QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, 2.0)])
    interval = QgsMarkerLineSymbolLayer(True, 4)
    interval.setIntervalUnit(Qgis.RenderUnit.Millimeters)
    interval.setSubSymbol(circle.clone())
    vertices = QgsMarkerLineSymbolLayer(True, 4)
    vertices.setPlacements(Qgis.MarkerLinePlacement.InnerVertices)
    vertices.setOffset(3)
    vertices.setOffsetUnit(Qgis.RenderUnit.Millimeters)
    vertices.setSubSymbol(circle.clone())
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([interval, vertices])))
    _geographic_project(layer)
    context = MapUnitContext.for_project(False, "degrees", LAT)
    ZoomLevels.configure(context.top_scale, context.map_units_per_mm_0)
    diagnostics = DiagnosticCollector()
    rules = RulesFlattener(0, 16, str(tmp_path), QgsProcessingFeedback(),
                           diagnostics).flatten_all_rules()
    utils = tmp_path / "utils"
    utils.mkdir()
    _, rules = RulesExporter(rules, EXTENT, 0, 16, str(utils), 0, QgsProcessingFeedback(),
                             diagnostics=diagnostics).export()
    placed = [r for r in rules if r.recipe is not None and r.recipe.kind == "marker_points"]
    spaced = [r for r in placed if r.recipe.param("interval") is not None]
    offset = [r for r in placed if r.recipe.param("offset")]
    assert spaced and offset
    for rule in spaced:
        band = rule.visibility
        if band.max_zoom is not None:
            middle = (band.min_zoom + band.max_zoom) / 2
            assert rule.recipe.param("interval") == pytest.approx(4 * _degrees_per_mm(middle), rel=1e-6)
    for rule in offset:
        # At the middle of the rule's zoom (QGIS's offset at zoom o + 1/2).
        assert abs(rule.recipe.param("offset")) == pytest.approx(
            3 * _degrees_per_mm(rule.get_attr("o")) / math.sqrt(2), rel=1e-6)


# --- symbology reference scale ----------------------------------------------

def _scale_of(zoom):
    return EARTH / 512 / 2 ** zoom * 96 / 0.0254


def _curve_at(value, zoom):
    return value if ex.is_number(value) else ex.evaluate_zoom_curve(value, zoom)


def test_reference_scale_sizes_follow_the_map(plugin, tmp_path):
    """QgsRenderContext::convertToPainterUnits multiplies mm / pt sizes by
    reference scale / map scale, for symbols and labels (the label engine
    takes the renderer's reference scale): they were constants on the web."""
    reference = 20000.0
    line = _layer(str(tmp_path / "l.gpkg"), "LineString",
                  [f"LINESTRING({CX - 150} {CY}, {CX + 150} {CY})"])
    line.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([
        QgsSimpleLineSymbolLayer(QColor("black"), 1.0)])))
    line.renderer().setReferenceScale(reference)
    point = _layer(str(tmp_path / "p.gpkg"), "Point", [f"POINT({CX} {CY + 100})"], fields=("name",))
    marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, 3.0)
    point.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol([marker])))
    point.renderer().setReferenceScale(reference)
    settings = QgsPalLayerSettings()
    settings.fieldName = "name"
    text = QgsTextFormat()
    text.setSize(10)  # points
    settings.setFormat(text)
    point.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    point.setLabelsEnabled(True)
    plain = _layer(str(tmp_path / "n.gpkg"), "LineString",
                   [f"LINESTRING({CX - 150} {CY - 50}, {CX + 150} {CY - 50})"])
    plain.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([
        QgsSimpleLineSymbolLayer(QColor("red"), 1.0)])))
    reset_project(line, point, plain)
    exporter, style = _export(tmp_path)
    at_reference = math.log2(_scale_of(0) / reference)
    widths = [l["paint"]["line-width"] for l in _data_layers(style, "line")]
    scaled = [w for w in widths if not ex.is_number(w)]
    assert len(scaled) == 1
    assert [w for w in widths if ex.is_number(w)] == [pytest.approx(96 / 25.4, rel=1e-3)]  # plain
    for zoom in (at_reference, 12, 16, 18, 21):
        factor = reference / _scale_of(zoom)
        assert _curve_at(scaled[0], zoom) == pytest.approx(96 / 25.4 * factor, rel=0.01)
        radius = _curve_at(_data_layers(style, "circle")[0]["paint"]["circle-radius"], zoom)
        # Less half the 1 px hairline stroke MapLibre draws outside the radius.
        assert radius + 0.5 == pytest.approx(1.5 * 96 / 25.4 * factor, rel=0.01)
        size = _curve_at(_data_layers(style, "symbol")[0]["layout"]["text-size"], zoom)
        assert size == pytest.approx(10 * 96 / 72 * factor, rel=0.01)
    # The project's own symbols are untouched.
    assert line.renderer().symbol().symbolLayer(0).widthUnit() == Qgis.RenderUnit.Millimeters
    assert point.labeling().settings().format().size() == 10
    assert not exporter.diagnostics.by_code("Q2VT_REFERENCE_SCALE")


def test_reference_scale_scales_data_defined_sizes(plugin):
    from q2vt_plugin.src.core.reference_scale import ReferenceScaleConverter  # pylint: disable=import-error
    from qgis.core import QgsProperty, QgsSymbolLayer
    layer = QgsSimpleLineSymbolLayer(QColor("black"), 1.0)
    layer.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyStrokeWidth,
                                 QgsProperty.fromField("w"))
    layer.setOffset(2.0)
    converter = ReferenceScaleConverter(10000)
    symbol = QgsLineSymbol([layer])
    converter.symbol(symbol)
    layer = symbol.symbolLayer(0)
    assert layer.widthUnit() == layer.offsetUnit() == Qgis.RenderUnit.MapUnits
    assert layer.width() == pytest.approx(10.0)  # 1 mm at 1:10000 = 10 m
    assert layer.offset() == pytest.approx(20.0)
    prop = layer.dataDefinedProperties().property(QgsSymbolLayer.Property.PropertyStrokeWidth)
    assert prop.expressionString() == '("w") * 10.0'
    assert not converter.unhandled


def _reference_symbols():
    from qgis.core import (QgsEllipseSymbolLayer, QgsHashedLineSymbolLayer,
                           QgsLinePatternFillSymbolLayer, QgsMarkerLineSymbolLayer,
                           QgsPointPatternFillSymbolLayer)
    mm = Qgis.RenderUnit.Millimeters
    markers = QgsMarkerLineSymbolLayer(True, 4)
    markers.setIntervalUnit(mm)
    markers.setSubSymbol(QgsMarkerSymbol([QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, 2.0)]))
    hashes = QgsHashedLineSymbolLayer(True, 4)
    hashes.setIntervalUnit(mm)
    hashes.setHashLength(3)
    hashes.setHashLengthUnit(mm)
    hashes.setSubSymbol(QgsLineSymbol([QgsSimpleLineSymbolLayer(QColor("black"), 0.5)]))
    hatch = QgsLinePatternFillSymbolLayer()
    hatch.setDistance(5)
    hatch.setDistanceUnit(mm)
    hatch.setSubSymbol(QgsLineSymbol([QgsSimpleLineSymbolLayer(QColor("black"), 1.5)]))
    # Drawn line by line (not with the image brush): the hatch line follows
    # the reference scale as well.
    lines = hatch.clone()
    lines.setClipMode(Qgis.LineClipMode.NoClipping)
    dots = QgsPointPatternFillSymbolLayer()
    dots.setDistanceX(6)
    dots.setDistanceY(6)
    dots.setDistanceXUnit(mm)
    dots.setDistanceYUnit(mm)
    dots.setSubSymbol(QgsMarkerSymbol([QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, 2.0)]))
    ellipse = QgsEllipseSymbolLayer()
    ellipse.setSymbolWidth(3)
    ellipse.setSymbolHeight(2)
    ellipse.setSymbolWidthUnit(mm)
    ellipse.setSymbolHeightUnit(mm)
    ellipse.setFillColor(QColor("black"))
    return {
        "marker line": ("LineString", QgsLineSymbol([markers])),
        "hashed line": ("LineString", QgsLineSymbol([hashes])),
        "line pattern": ("Polygon", QgsFillSymbol([hatch])),
        "line pattern lines": ("Polygon", QgsFillSymbol([lines])),
        "point pattern": ("Polygon", QgsFillSymbol([dots])),
        "ellipse": ("Point", QgsMarkerSymbol([ellipse])),
    }


@pytest.mark.parametrize("name", ["marker line", "hashed line", "line pattern",
                                  "line pattern lines", "point pattern", "ellipse"])
def test_reference_scale_conversion_draws_like_qgis(plugin, name):
    """Converted symbols drawn without a reference scale look like the
    originals QGIS draws with it. Setters that write through to sub-symbols
    or other sizes (a marker line's width is its marker's size) scaled
    those sizes twice, or zeroed the hatch line width. QGIS's image brush
    of a pattern fill draws its sub-symbol without the reference scale."""
    from q2vt_plugin.src.core.reference_scale import ReferenceScaleConverter  # pylint: disable=import-error
    from q2vt_render import ink_mask, mask_difference, render
    kind, symbol = _reference_symbols()[name]
    wkt = {"LineString": f"LINESTRING({CX - 120} {CY - 40}, {CX} {CY + 50}, {CX + 120} {CY - 30})",
           "Polygon": f"POLYGON(({CX - 300} {CY - 250}, {CX + 300} {CY - 250}, {CX + 300} {CY + 250}, "
                      f"{CX - 300} {CY + 250}, {CX - 300} {CY - 250}))",
           "Point": f"POINT({CX} {CY})"}[kind]
    reference = 20000.0
    layers = []
    for converted in (False, True):
        layer = QgsVectorLayer(f"{kind}?crs=EPSG:3857", "lyr", "memory")
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        layer.dataProvider().addFeatures([feature])
        copy = symbol.clone()
        if converted:
            converter = ReferenceScaleConverter(reference)
            converter.symbol(copy)
            assert not converter.unhandled and not converter.map_unit_sizes
        layer.setRenderer(QgsSingleSymbolRenderer(copy))
        if not converted:
            layer.renderer().setReferenceScale(reference)
        layers.append(layer)
    for scale in (10000.0, 20000.0, 40000.0):
        half = 150 * scale * 0.0254 / 96  # 300 px at 96 dpi
        extent = QgsRectangle(CX - half, CY - half, CX + half, CY + half)
        original, converted = (ink_mask(render([layer], extent)) for layer in layers)
        assert original
        assert mask_difference(original, converted) < 0.05, (name, scale, len(original), len(converted))


def test_reference_scale_reports_map_unit_sizes(plugin):
    """QgsRenderContext::convertToPainterUnits multiplies map-unit sizes by
    reference / map scale too: those keep their map size on the web."""
    from q2vt_plugin.src.core.reference_scale import ReferenceScaleConverter  # pylint: disable=import-error
    layer = QgsSimpleLineSymbolLayer(QColor("black"), 10.0)
    layer.setWidthUnit(Qgis.RenderUnit.MapUnits)
    converter = ReferenceScaleConverter(20000)
    converter.symbol(QgsLineSymbol([layer]))
    assert converter.map_unit_sizes == ["SimpleLine width"]


# --- geometry generators in screen units --------------------------------------

def _generator(expression, unit, geometry_type="Fill"):
    generator = QgsGeometryGeneratorSymbolLayer.create(
        {"geometryModifier": expression, "SymbolType": geometry_type})
    generator.setUnits(unit)
    generator.setSubSymbol(QgsFillSymbol([QgsSimpleFillSymbolLayer(QColor("blue"))]))
    return generator


def _generated(flat_rule, geometry):
    from q2vt_plugin.src.core.rules_exporter import RulesExporter  # pylint: disable=import-error
    # A fill generator's sub-symbol layers are drawn on its output
    # (pre_generator); others keep the generator symbol layer.
    generator = flat_rule.pre_generator or \
        flat_rule.rule.symbol().symbolLayer(0).geometryExpression()
    expression = QgsExpression(RulesExporter._generator_in_layer_crs(  # pylint: disable=protected-access
        generator, flat_rule))
    context = QgsExpressionContext()
    feature = QgsFeature()
    feature.setGeometry(geometry)
    context.setFeature(feature)
    result = expression.evaluate(context)
    assert not expression.hasEvalError(), expression.evalErrorString()
    return result


@pytest.mark.parametrize("crs", ["EPSG:3857", "EPSG:4326"])
def test_screen_unit_generator_keeps_its_screen_size_at_every_zoom(plugin, tmp_path, crs):
    """QGIS evaluates a generator in mm on the painter geometry scaled to
    mm (QgsGeometryGeneratorSymbolLayer::render): buffer($geometry, 5) is a
    5 mm circle at every scale. It was evaluated in map units (a 5 m circle)."""
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener  # pylint: disable=import-error
    from q2vt_plugin.src.core.fidelity.diagnostics import DiagnosticCollector  # pylint: disable=import-error
    point = f"POINT({LON} {LAT})" if crs == "EPSG:4326" else f"POINT({CX} {CY})"
    layer = _layer(str(tmp_path / "g.gpkg"), "Point", [point], crs=crs)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol([
        _generator("buffer($geometry, 5)", Qgis.RenderUnit.Millimeters)])))
    reset_project(layer)
    rules = RulesFlattener(0, 16, str(tmp_path), QgsProcessingFeedback(),
                           DiagnosticCollector()).flatten_all_rules()
    by_zoom = {}
    for rule in rules:
        visible = rule.visibility
        by_zoom[(visible.min_zoom, visible.max_zoom)] = rule
    # One generator per tile zoom, and per zoom of the overzoom up to 22.
    assert sorted(by_zoom) == [(z, z + 1.0) for z in range(0, 22)] + [(22, None)]
    centre = QgsGeometry.fromWkt(f"POINT({CX} {CY})")
    for (zoom, _), rule in by_zoom.items():
        if zoom not in (0, 10, 16, 18, 22):
            continue
        circle = _generated(rule, centre)  # the exporter holds Web Mercator geometry
        radius_px = math.sqrt(circle.area() / math.pi) / (EARTH / 512 / 2 ** zoom)
        assert radius_px == pytest.approx(5 * 96 / 25.4, rel=0.02)


def test_screen_unit_translation_points_down_the_screen(plugin, tmp_path):
    """Painter y points down: translate($geometry, 15, 15) in pixels moves the
    copy right and down by 15 px (it went 15 m right and up)."""
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener  # pylint: disable=import-error
    from q2vt_plugin.src.core.fidelity.diagnostics import DiagnosticCollector  # pylint: disable=import-error
    square = f"POLYGON(({CX - 60} {CY - 60}, {CX + 60} {CY - 60}, {CX + 60} {CY + 60}, " \
             f"{CX - 60} {CY + 60}, {CX - 60} {CY - 60}))"
    layer = _layer(str(tmp_path / "s.gpkg"), "Polygon", [square])
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([
        QgsSimpleFillSymbolLayer(QColor("grey")),
        _generator("translate($geometry, 15, 15)", Qgis.RenderUnit.Pixels)])))
    reset_project(layer)
    rules = RulesFlattener(14, 19, str(tmp_path), QgsProcessingFeedback(),
                           DiagnosticCollector()).flatten_all_rules()
    generated = [r for r in rules if r.pre_generator is not None
                 or r.rule.symbol().symbolLayer(0).layerType() == "GeometryGenerator"]
    plain = [r for r in rules if r not in generated]
    # One generator per zoom (its fill, and its outline drawn as a line).
    assert plain and len({r.visibility.min_zoom for r in generated}) == 22 - 14 + 1
    assert len({r.visibility.min_zoom for r in generated if r.get_attr("c") == 2}) == 22 - 14 + 1
    for rule in generated:
        zoom = rule.visibility.min_zoom
        moved = _generated(rule, QgsGeometry.fromWkt(square)).centroid().asPoint()
        per_px = EARTH / 512 / 2 ** zoom
        assert (moved.x() - CX) / per_px == pytest.approx(15, rel=1e-6)
        assert (moved.y() - CY) / per_px == pytest.approx(-15, rel=1e-6)
