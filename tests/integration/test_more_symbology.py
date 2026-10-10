"""Point cluster and point displacement renderers: the exported datasets
(grouped per zoom band) drawn with their converted symbols look like QGIS
drawing the original renderer at the same scale."""

import math
import os
import sys

import pytest
from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsMapSettings, QgsMarkerSymbol,
                       QgsProcessingFeedback, QgsPointClusterRenderer,
                       QgsPointDisplacementRenderer, QgsRectangle, QgsSingleSymbolRenderer)
from qgis.PyQt.QtCore import QSize

from q2vt_render import ink_mask, mask_difference, render

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_materialize import EXTENT, _layer  # noqa: E402  pylint: disable=wrong-import-position

# A tight group of six, a pair 5 m apart and a lone point (3 mm tolerance
# is about 9 m at the test scale).
POINTS = ([f"POINT({-50 + 3 * math.cos(k)} {3 * math.sin(k)})" for k in range(6)]
          + ["POINT(0 -60)", "POINT(5 -60)", "POINT(60 40)"])


def _zoom(extent, size=(300, 300)) -> float:
    from q2vt_plugin.src.utils.zoom_levels import ZoomLevels  # pylint: disable=import-error
    settings = QgsMapSettings()
    settings.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    settings.setExtent(extent)
    settings.setOutputSize(QSize(*size))
    return math.log2(ZoomLevels.zoom_to_scale(0) / settings.scale())


def _export(layer, tmp_path, low, high):
    """Like test_materialize._export, for the zooms around the test scale
    (point groups are exported per zoom band)."""
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener  # pylint: disable=import-error
    from q2vt_plugin.src.core.rules_exporter import RulesExporter  # pylint: disable=import-error
    from fidelity.diagnostics import DiagnosticCollector
    from q2vt_fixtures import reset_project
    reset_project(layer)
    diags = DiagnosticCollector()
    rules = RulesFlattener(low, high, str(tmp_path), QgsProcessingFeedback(), diags,
                           extent=EXTENT).flatten_all_rules()
    utils = tmp_path / "utils"
    utils.mkdir()
    layers, rules = RulesExporter(rules, EXTENT, low, high, str(utils), 0, QgsProcessingFeedback(),
                                  diagnostics=diags).export()
    by_name = {l.name(): l for l in layers}
    rendered = []
    for rule in rules:
        out = by_name[rule.output_dataset].clone()  # rules may share a dataset
        symbol = rule.rule.symbol().clone()
        if out.geometryType() == Qgis.GeometryType.Line and symbol.type() != Qgis.SymbolType.Line:
            # A polygon outline travels as a line layer inside a fill symbol.
            from qgis.core import QgsLineSymbol
            symbol = QgsLineSymbol([symbol.symbolLayer(i).clone() for i in range(symbol.symbolLayerCount())])
        out.setRenderer(QgsSingleSymbolRenderer(symbol))
        rendered.append(out)
    return rendered, rules, diags


def _compare(plugin, tmp_path, renderer):
    layer = _layer("Point", POINTS, str(tmp_path / "pts.gpkg"))
    renderer.setEmbeddedRenderer(QgsSingleSymbolRenderer(
        QgsMarkerSymbol.createSimple({"name": "circle", "size": "2", "color": "#2b83ba"})))
    layer.setRenderer(renderer)
    expected = render([layer], EXTENT)
    zoom = _zoom(EXTENT)
    rendered, rules, diags = _export(layer, tmp_path, int(zoom) - 1, int(zoom) + 1)
    visible = [(out, rule) for out, rule in zip(rendered, rules)
               if rule.point_group and rule.visibility is not None and rule.visibility.contains(zoom)]
    assert visible, [r.visibility for r in rules]
    got = render([out for out, _ in reversed(visible)], EXTENT)  # first rule = bottom
    return expected, got, [rule for _, rule in visible], diags


def test_point_cluster_groups_like_qgis(plugin, tmp_path):
    expected, got, rules, _ = _compare(plugin, tmp_path, QgsPointClusterRenderer())
    roles = {rule.point_group[1] for rule in rules}
    assert roles == {"cluster", "members"}, roles
    # Two clusters (6 and 2 points) and the lone point, like QGIS.
    assert mask_difference(ink_mask(expected), ink_mask(got)) < 0.08


def test_point_displacement_places_members_like_qgis(plugin, tmp_path):
    renderer = QgsPointDisplacementRenderer()
    renderer.setCircleRadiusAddition(0.5)
    expected, got, rules, _ = _compare(plugin, tmp_path, renderer)
    roles = {rule.point_group[1] for rule in rules}
    assert {"members", "center", "circle"} <= roles, roles
    assert mask_difference(ink_mask(expected), ink_mask(got)) < 0.08


def test_point_displacement_grid(plugin, tmp_path):
    renderer = QgsPointDisplacementRenderer()
    renderer.setPlacement(QgsPointDisplacementRenderer.Placement.Grid)
    expected, got, rules, _ = _compare(plugin, tmp_path, renderer)
    assert "grid" in {rule.point_group[1] for rule in rules}
    assert mask_difference(ink_mask(expected), ink_mask(got)) < 0.08


def test_grouping_follows_qgis_order_and_tolerance():
    from fidelity import point_groups as pg
    # The second point joins the first group; the third is within tolerance
    # of the group's centre but not of its first point: a new group.
    groups = pg.group_points([(0, 0), (4, 0), (9, 0), (100, 0)], 5)
    assert groups == [[0, 1], [2], [3]]
    # Ring: radius max(diagonal / 2, n * diagonal / 2 pi); the first member
    # straight below the centre (painter y down), then clockwise on screen.
    positions, radius, _ = pg.displaced((0, 0), 4, pg.RING, 2.0, 2.0, 0.0)
    assert radius == pytest.approx(4 / math.pi)
    assert positions[0] == pytest.approx((0, -radius))
    assert positions[1] == pytest.approx((radius, 0), abs=1e-9)
    # Grid: rows of 2 for 4 members, centred; QGIS joins row and column neighbours.
    positions, _, size = pg.displaced((0, 0), 4, pg.GRID, 2.0, 2.0, 0.0)
    assert size == 2 and len(pg.grid_lines(positions, size)) == 4


# --- other renderers and symbol layer types -----------------------------------
LINE = "LINESTRING(-100 -60, -30 40, 40 -40, 100 50)"
SQUARES = ["POLYGON((-90 -90, -10 -90, -10 -10, -90 -10, -90 -90))",
           "POLYGON((-30 -30, 50 -30, 50 50, -30 50, -30 -30))"]


def _render_compare(layer, tmp_path, zooms=(14, 18)):
    """QGIS drawing the layer vs QGIS drawing the exported datasets with
    their converted symbols: ink difference and mean colour difference."""
    import numpy as np
    expected = render([layer], EXTENT)
    rendered, rules, diags = _export(layer, tmp_path, *zooms)
    zoom = _zoom(EXTENT)
    shown = sorted(((rule.order, index, out) for index, (out, rule) in enumerate(zip(rendered, rules))
                    if rule.visibility is None or rule.visibility.contains(zoom)),
                   key=lambda item: (item[0], item[1]))  # QGIS draw order, bottom first
    got = render([out for _, _, out in reversed(shown)], EXTENT)
    if os.environ.get("Q2VT_DUMP"):
        expected.save(os.path.join(os.environ["Q2VT_DUMP"], layer.name() + "_q.png"))
        got.save(os.path.join(os.environ["Q2VT_DUMP"], layer.name() + "_b.png"))

    def pixels(image):
        image = image.convertToFormat(image.Format.Format_RGB32)
        ptr = image.constBits()
        ptr.setsize(image.sizeInBytes())
        return np.frombuffer(ptr, np.uint8).reshape(image.height(), image.width(), 4)[..., :3].astype(int)
    a, b = pixels(expected), pixels(got)
    ink = (a < 250).any(axis=2) | (b < 250).any(axis=2)
    return (mask_difference(ink_mask(expected), ink_mask(got)),
            float(np.abs(a - b).max(axis=2)[ink].mean()), rules, diags)


def test_interpolated_line_colour_and_width_follow_qgis(plugin, tmp_path):
    from qgis.core import (QgsColorRampShader, QgsGradientColorRamp, QgsInterpolatedLineColor,
                           QgsInterpolatedLineSymbolLayer, QgsInterpolatedLineWidth, QgsLineSymbol)
    from qgis.PyQt.QtGui import QColor
    layer = _layer("LineString", [LINE], str(tmp_path / "l.gpkg"))
    interpolated = QgsInterpolatedLineSymbolLayer()
    shader = QgsColorRampShader(0, 10, QgsGradientColorRamp(QColor("#2c7bb6"), QColor("#d7191c")))
    shader.classifyColorRamp(5, -1)
    interpolated.setInterpolatedColor(QgsInterpolatedLineColor(shader))
    width = QgsInterpolatedLineWidth()
    width.setIsVariableWidth(True)
    width.setMinimumValue(0)
    width.setMaximumValue(10)
    width.setMinimumWidth(0.5)
    width.setMaximumWidth(4)
    interpolated.setInterpolatedWidth(width)
    interpolated.setExpressionsStringForColor("0", "10")
    interpolated.setExpressionsStringForWidth("0", "10")
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([interpolated])))
    shape, colour, rules, _ = _render_compare(layer, tmp_path)
    assert any(r.recipe is not None and r.recipe.kind == "interpolated_segments" for r in rules)
    assert shape < 0.1 and colour < 30, (shape, colour)


def test_vector_field_lines_match_qgis(plugin, tmp_path):
    from qgis.core import (QgsField, QgsFeature, QgsGeometry, QgsLineSymbol, QgsVectorFieldSymbolLayer,
                           QgsVectorLayer)
    from qgis.PyQt.QtCore import QVariant
    from q2vt_fixtures import to_geopackage
    memory = QgsVectorLayer("Point?crs=EPSG:3857", "vf", "memory")
    memory.dataProvider().addAttributes([QgsField("dx", QVariant.Double), QgsField("dy", QVariant.Double)])
    memory.updateFields()
    features = []
    for x, y, dx, dy in ((-60, -60, 40, 10), (0, 30, -20, 50), (60, -20, 30, -40)):
        feature = QgsFeature(memory.fields())
        feature.setAttributes([dx, dy])
        feature.setGeometry(QgsGeometry.fromWkt(f"POINT({x} {y})"))
        features.append(feature)
    memory.dataProvider().addFeatures(features)
    layer = to_geopackage(memory, str(tmp_path / "vf.gpkg"))
    field = QgsVectorFieldSymbolLayer()
    field.setXAttribute("dx")
    field.setYAttribute("dy")
    field.setScale(1.0)
    field.setDistanceUnit(Qgis.RenderUnit.MapUnits)
    field.setSubSymbol(QgsLineSymbol.createSimple({"color": "black", "width": "0.6"}))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol([field])))
    shape, _, _, _ = _render_compare(layer, tmp_path)
    assert shape < 0.1, shape


def test_merged_and_inverted_polygons_match_qgis(plugin, tmp_path):
    from qgis.core import QgsFillSymbol, QgsInvertedPolygonRenderer, QgsMergedFeatureRenderer
    for name, kind in (("merged", QgsMergedFeatureRenderer), ("inverted", QgsInvertedPolygonRenderer)):
        layer = _layer("Polygon", SQUARES, str(tmp_path / f"{name}.gpkg"))
        layer.setRenderer(kind(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
            {"color": "#fdae61", "outline_color": "black", "outline_width": "0.6"}))))
        folder = tmp_path / name
        folder.mkdir()
        shape, colour, rules, _ = _render_compare(layer, folder)
        assert {r.merge for r in rules} == {"merge" if name == "merged" else "invert"}
        assert shape < 0.05 and colour < 20, (name, shape, colour)


def test_heatmap_becomes_a_maplibre_heatmap(plugin, tmp_path):
    from qgis.core import QgsGradientColorRamp, QgsHeatmapRenderer, QgsProcessingFeedback
    from qgis.PyQt.QtGui import QColor
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener  # pylint: disable=import-error
    from fidelity.diagnostics import DiagnosticCollector
    from fidelity.heatmap import heatmap_paint
    from q2vt_fixtures import reset_project
    layer = _layer("Point", POINTS, str(tmp_path / "hm.gpkg"))
    heatmap = QgsHeatmapRenderer()
    heatmap.setColorRamp(QgsGradientColorRamp(QColor(0, 0, 255, 0), QColor("red")))
    heatmap.setRadius(10)
    heatmap.setRadiusUnit(Qgis.RenderUnit.Millimeters)
    layer.setRenderer(heatmap)
    reset_project(layer)
    rules = RulesFlattener(14, 18, str(tmp_path), QgsProcessingFeedback(), DiagnosticCollector(),
                           extent=EXTENT).flatten_all_rules()
    assert len(rules) == 1 and rules[0].heatmap
    paint = heatmap_paint(rules[0].heatmap, 1.0)
    assert {"heatmap-radius", "heatmap-color", "heatmap-intensity", "heatmap-weight"} <= set(paint)
    # Screen radius: 10 mm at 96 dpi, fitted to MapLibre's kernel.
    assert paint["heatmap-radius"] == pytest.approx(10 * 96 / 25.4 * 1.33, rel=0.01)


@pytest.mark.parametrize("weight,expected", [('w', [2.0, 0.0, 0.0]), ('"w"', [2.0, 0.0, 0.0]),
                                              ('"w" * 1', [2.0, None, 0.0])])
def test_heatmap_null_weight_follows_qgis(plugin, tmp_path, weight, expected):
    """QgsHeatmapRenderer reads a weight naming a numeric field from the
    attribute: its NULL weighs 0. Any other NULL weighs 1 (the value does
    not convert; the web fallback). The exported weight makes the field's
    NULL explicit."""
    from qgis.core import (NULL, QgsExpression, QgsExpressionContext, QgsExpressionContextUtils,
                           QgsFeature, QgsField, QgsGeometry, QgsHeatmapRenderer,
                           QgsProcessingFeedback, QgsSymbolLayer, QgsVectorLayer)
    from qgis.PyQt.QtCore import QVariant
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener  # pylint: disable=import-error
    from fidelity.diagnostics import DiagnosticCollector
    from q2vt_fixtures import reset_project, to_geopackage
    memory = QgsVectorLayer("Point?crs=EPSG:3857", "heat", "memory")
    memory.dataProvider().addAttributes([QgsField("w", QVariant.Double)])
    memory.updateFields()
    features = []
    for k, value in enumerate([2.0, None, 0.0]):
        feature = QgsFeature(memory.fields())
        feature.setAttributes([value])
        feature.setGeometry(QgsGeometry.fromWkt(f"POINT({-60 + 60 * k} 0)"))
        features.append(feature)
    memory.dataProvider().addFeatures(features)
    layer = to_geopackage(memory, str(tmp_path / "heat.gpkg"))
    heatmap = QgsHeatmapRenderer()
    heatmap.setWeightExpression(weight)
    layer.setRenderer(heatmap)
    reset_project(layer)
    rules = RulesFlattener(14, 16, str(tmp_path), QgsProcessingFeedback(), DiagnosticCollector(),
                           extent=EXTENT).flatten_all_rules()
    prop = rules[0].rule.symbol().symbolLayer(0).dataDefinedProperties().property(
        QgsSymbolLayer.Property.PropertySize)
    expression = QgsExpression(prop.asExpression())
    context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    values = []
    for feature in layer.getFeatures():
        context.setFeature(feature)
        value = expression.evaluate(context)
        values.append(None if value is None or value == NULL else float(value))
    assert values == expected


def _converter():
    from q2vt_plugin.src.core import maplibre_converter as mc  # pylint: disable=import-error
    from fidelity.diagnostics import DiagnosticCollector
    exporter = mc.QgisMapLibreStyleExporter.__new__(mc.QgisMapLibreStyleExporter)
    exporter.pattern_images, exporter.marker_symbols, exporter.marker_counter = {}, {}, 0
    exporter.profile = mc.ExportProfile()
    exporter.context = mc.ConversionContext(DiagnosticCollector())
    exporter.style, exporter.maxzoom = {"layers": []}, 17
    mc.PropertyExtractor.context = exporter.context
    exporter.context.reference_zoom = 14
    return exporter


def test_lineburst_is_a_line_pattern_with_colour_one_on_the_left(plugin):
    from qgis.core import QgsLineburstSymbolLayer, QgsLineSymbol
    from qgis.PyQt.QtGui import QColor
    exporter = _converter()
    burst = QgsLineburstSymbolLayer(QColor("#0000ff"), QColor("#ffff00"))
    burst.setWidth(3)
    exporter._convert_symbol(QgsLineSymbol([burst]), "s", "src", "q2vt", 14, 24)
    layer_def = exporter.style["layers"][0]
    assert layer_def["type"] == "line" and "line-pattern" in layer_def["paint"]
    image = exporter.pattern_images[layer_def["paint"]["line-pattern"]].img_1x
    # MapLibre puts the image's first row on the right of the line direction:
    # colour 2 first, colour 1 (QGIS: the left edge) last.
    top, bottom = image.getpixel((0, 0)), image.getpixel((0, image.height - 1))
    assert top[2] < 30 and top[0] > 220 and bottom[2] > 220 and bottom[0] < 30


def test_raster_line_repeats_like_qgis(plugin, tmp_path):
    from PIL import Image
    from qgis.core import QgsLineSymbol, QgsRasterLineSymbolLayer
    path = str(tmp_path / "stripes.png")
    stripes = Image.new("RGBA", (30, 10), (0, 0, 255, 255))
    stripes.paste((255, 0, 0, 255), (0, 0, 30, 3))  # red band at the top
    stripes.save(path)
    exporter = _converter()
    raster = QgsRasterLineSymbolLayer(path)
    raster.setWidth(2)
    raster.setWidthUnit(Qgis.RenderUnit.Millimeters)
    exporter._convert_symbol(QgsLineSymbol([raster]), "s", "src", "q2vt", 14, 24)
    layer_def = exporter.style["layers"][0]
    images = exporter.pattern_images[layer_def["paint"]["line-pattern"]]
    one, two = images.img_1x, images.img_2x
    width_px = 2 * 96 / 25.4
    # A power-of-two image width (seamless repeats) whose proportion gives
    # QGIS's repeat: round(width x aspect) px for a line width_px wide.
    assert one.width & (one.width - 1) == 0 and two.size == (2 * one.width, 2 * one.height)
    period = one.width / one.height * width_px
    assert period == pytest.approx(round(width_px * 3), rel=0.004)
    # QGIS draws the image's top on the left of the line: flipped for MapLibre.
    assert one.getpixel((one.width // 2, one.height - 1))[0] > 200
