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


def _compare(plugin, tmp_path, renderer, below=-1):
    """QGIS vs the exported groups at the test scale; exported up to
    ``below`` zooms under it (from 1: drawn from overzoomed tiles)."""
    layer = _layer("Point", POINTS, str(tmp_path / "pts.gpkg"))
    renderer.setEmbeddedRenderer(QgsSingleSymbolRenderer(
        QgsMarkerSymbol.createSimple({"name": "circle", "size": "2", "color": "#2b83ba"})))
    layer.setRenderer(renderer)
    expected = render([layer], EXTENT)
    zoom = _zoom(EXTENT)
    rendered, rules, diags = _export(layer, tmp_path, int(zoom) - 1 - max(below, 0),
                                     int(zoom) - below)
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


def test_point_cluster_past_the_last_tiles_groups_like_qgis(plugin, tmp_path):
    """Exported three zooms below the test scale: the browser overzooms the
    last tiles, where the grouping goes on per zoom (a 1 mm distance splits
    the pair within the overzoom); the lone point (no other within the
    grouping distance of the first overzoom written) is written once for
    all of them, the others per zoom."""
    renderer = QgsPointClusterRenderer()
    renderer.setTolerance(1)
    expected, got, rules, _ = _compare(plugin, tmp_path, renderer, below=3)
    stable = [rule for rule in rules if dict(rule.point_group[3]).get("part") == "stable"]
    assert len(stable) == 1 and stable[0].visibility.max_zoom is None
    assert {rule.get_attr("v") is not None for rule in rules} == {True}
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


def test_isolated_points_stay_alone_at_smaller_tolerances():
    from fidelity import point_groups as pg
    points = [(0, 0), (4, 0), (20, 0), (20, 30), (50, 5)]
    assert pg.isolated(points, 5) == {2, 3, 4}
    assert pg.isolated(points, 30) == set()  # (20, 30) is 30 from (20, 0)
    for tolerance in (5, 3, 1):
        alone = {g[0] for g in pg.group_points(points, tolerance) if len(g) == 1}
        assert pg.isolated(points, 5) <= alone


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


def test_heatmap_intensity_follows_the_overzoomed_map(plugin, tmp_path):
    """Exported 0-16 and viewed up to zoom 22: QGIS scales the screen-radius
    kernel to the densest spot at every zoom. The intensity stops ended at
    the max tile zoom, so zoomed in past it MapLibre kept zoom 16's maximum
    (a kernel 64 times wider in map units at zoom 22): a pale heatmap."""
    from qgis.core import QgsGradientColorRamp, QgsHeatmapRenderer, QgsProcessingFeedback
    from qgis.PyQt.QtGui import QColor
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener  # pylint: disable=import-error
    from fidelity.diagnostics import DiagnosticCollector
    from fidelity.heatmap import GAUSS, max_density, map_units_per_css_px
    from q2vt_fixtures import reset_project
    layer = _layer("Point", POINTS, str(tmp_path / "hm.gpkg"))
    heatmap = QgsHeatmapRenderer()
    heatmap.setColorRamp(QgsGradientColorRamp(QColor(0, 0, 255, 0), QColor("red")))
    heatmap.setRadius(10)
    heatmap.setRadiusUnit(Qgis.RenderUnit.Millimeters)
    layer.setRenderer(heatmap)
    reset_project(layer)
    rules = RulesFlattener(0, 16, str(tmp_path), QgsProcessingFeedback(), DiagnosticCollector(),
                           extent=EXTENT).flatten_all_rules()
    stops = dict(rules[0].heatmap["intensity_stops"])
    assert sorted(stops) == list(range(0, 25))
    points = [(f.geometry().asPoint().x(), f.geometry().asPoint().y(), 1.0)
              for f in layer.getFeatures()]
    for zoom in (17, 20, 22):
        radius = 10 * 96 / 25.4 * map_units_per_css_px(zoom)
        assert stops[zoom] == pytest.approx(1 / (GAUSS * max_density(points, radius)), rel=1e-4)
    # Zoomed in, points stop overlapping: the maximum falls towards one point.
    assert stops[22] > stops[16] * 1.5


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


def test_random_fill_in_map_units_from_zoom_0_is_a_tint_when_dense(plugin):
    """Ten markers per 400 map units², 4 map units wide, exported from zoom
    0: zoomed out, a texture cell held up to billions of markers (the export
    ran out of memory). Over a marker per pixel it is a uniform tint, as
    dense as the scattered markers of the zooms above it."""
    import time
    from qgis.core import QgsFillSymbol, QgsRandomMarkerFillSymbolLayer
    exporter = _converter()
    exporter.maxzoom = 16
    exporter.context.reference_zoom = 0  # the rule's min zoom
    random_fill = QgsRandomMarkerFillSymbolLayer(10, Qgis.PointCountMethod.DensityBased, 400, 3)
    random_fill.setDensityAreaUnit(Qgis.RenderUnit.MapUnits)
    random_fill.setSubSymbol(QgsMarkerSymbol.createSimple(
        {"size": "4", "size_unit": "MapUnit", "color": "255,0,0", "outline_style": "no"}))
    started = time.time()
    exporter._convert_symbol(QgsFillSymbol([random_fill]), "s", "src", "q2vt", 0, 16)
    assert time.time() - started < 30
    pattern = exporter.style["layers"][0]["paint"]["fill-pattern"]
    assert pattern[:2] == ["step", ["zoom"]]
    names = [pattern[2]] + pattern[4::2]

    def cover(image):
        alpha = image.getchannel("A")
        return sum(alpha.getdata()) / 255 / (image.width * image.height)

    tints, scattered = [], []
    for name in names:
        image = exporter.pattern_images[name].img_1x
        assert image.width <= 512 and image.height <= 512
        (tints if image.width <= 16 else scattered).append(cover(image))
    assert tints and scattered
    # The expected cover of the markers (1 - exp(-density * marker area)).
    expected = 1 - math.exp(-10 / 400 * math.pi * 2 ** 2)
    assert all(t == pytest.approx(expected, abs=0.03) for t in tints)
    assert scattered[0] == pytest.approx(tints[-1], abs=0.04)


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


def test_marker_line_with_map_unit_details_gets_a_sprite_per_zoom(plugin):
    """A screen-size marker with a map-unit outline along a map-unit
    interval (the native part of a map exported from zoom 0): one sprite per
    zoom, drawn at that zoom's scale. One sprite drawn at the style's first
    zoom had an outline of 1e-5 px there, transparent at every zoom."""
    from qgis.core import QgsLineSymbol, QgsMarkerLineSymbolLayer, QgsSimpleMarkerSymbolLayer
    from q2vt_plugin.src.core.maplibre_converter import PropertyExtractor
    exporter = _converter()
    marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Square, 3)
    marker.setSizeUnit(Qgis.RenderUnit.Millimeters)
    marker.setStrokeWidth(2)
    marker.setStrokeWidthUnit(Qgis.RenderUnit.MapUnits)
    line = QgsMarkerLineSymbolLayer(True, 20)
    line.setIntervalUnit(Qgis.RenderUnit.MapUnits)
    line.setSubSymbol(QgsMarkerSymbol([marker]))
    exporter._convert_symbol(QgsLineSymbol([line]), "s", "src", "q2vt", 0, 15)
    layers = {layer["id"]: layer for layer in exporter.style["layers"]}
    assert {"s_z0", "s_z12", "s_z14"} <= set(layers)
    assert (layers["s_z12"]["minzoom"], layers["s_z12"]["maxzoom"]) == (12, 13)
    for zoom in (12, 13, 14):
        request = exporter.marker_symbols[layers[f"s_z{zoom}"]["layout"]["icon-image"]]
        assert request.map_units_per_pixel == pytest.approx(
            1.0 / PropertyExtractor.static_pixels(1.0, "map", zoom), rel=1e-6)


def test_embedded_raster_line_image_is_a_line_pattern(plugin, tmp_path):
    """A raster line with its image embedded in the style (base64:) is the
    image along the line like a file's; QImageReader took it for a missing
    file and the line was a faint symbol preview (Shoe Prints)."""
    import base64
    from PIL import Image
    from qgis.core import QgsLineSymbol, QgsRasterLineSymbolLayer
    path = tmp_path / "prints.png"
    Image.new("RGBA", (40, 10), (0, 0, 0, 255)).save(path)
    exporter = _converter()
    raster = QgsRasterLineSymbolLayer("base64:" + base64.b64encode(path.read_bytes()).decode())
    raster.setWidth(4)
    exporter._convert_symbol(QgsLineSymbol([raster]), "s", "src", "q2vt", 14, 24)
    paint = exporter.style["layers"][0]["paint"]
    one = exporter.pattern_images[paint["line-pattern"]].img_1x
    assert one.width / one.height == pytest.approx(4, rel=0.01)
    assert not exporter.context.diagnostics.by_code("Q2VT_PATTERN_APPROXIMATE")


def test_hatch_of_zero_width_lines_is_a_hairline(plugin):
    """A hatch line of width 0 is QGIS's one-pixel hairline: the texture was
    fully transparent (geo_e5 lost its blue hatch)."""
    from qgis.core import QgsFillSymbol, QgsLinePatternFillSymbolLayer, QgsLineSymbol
    exporter = _converter()
    hatch = QgsLinePatternFillSymbolLayer()
    hatch.setDistance(1.6)
    hatch.setLineAngle(0)
    hatch.setSubSymbol(QgsLineSymbol.createSimple({"color": "0,170,255", "width": "0"}))
    exporter._convert_symbol(QgsFillSymbol([hatch]), "s", "src", "q2vt", 14, 24)
    name = exporter.style["layers"][0]["paint"]["fill-pattern"]
    alpha = exporter.pattern_images[name].img_1x.getchannel("A")
    assert alpha.getextrema()[1] > 100  # was 0


def test_unused_offset_unit_does_not_mix_a_metre_marker(plugin):
    """A marker sized in metres with an offset of 0 mm is all in map units
    (Course End was reported as mixing units)."""
    from qgis.core import QgsSimpleMarkerSymbolLayer
    exporter = _converter()
    marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, 90)
    marker.setSizeUnit(Qgis.RenderUnit.MetersInMapUnits)
    marker.setStrokeWidthUnit(Qgis.RenderUnit.MetersInMapUnits)
    marker.setOffsetUnit(Qgis.RenderUnit.Millimeters)
    exporter._convert_symbol(QgsMarkerSymbol([marker]), "s", "src", "q2vt", 14, 24)
    assert not exporter.context.diagnostics.by_code("Q2VT_MIXED_UNITS")


def test_hatch_of_markers_is_qgis_brush_tile(plugin):
    """A hatch whose line is a marker line (arrowheads every 3.2 mm, 3.2 mm
    apart) is drawn the way QGIS fills it by default: the sub-symbol drawn
    into a tile truncated to whole pixels (applyPattern); a cropped preview
    repeated at the wrong period made a dense dark mesh."""
    import math as _math
    from qgis.core import (QgsFillSymbol, QgsLinePatternFillSymbolLayer, QgsLineSymbol,
                           QgsMarkerLineSymbolLayer, QgsMarkerSymbol)
    exporter = _converter()
    exporter._exact_screen_texture = True
    hatch = QgsLinePatternFillSymbolLayer()
    hatch.setDistance(3.2)
    hatch.setLineAngle(45)
    markers = QgsMarkerLineSymbolLayer(True, 3.2)
    markers.setSubSymbol(QgsMarkerSymbol.createSimple({"name": "arrowhead", "size": "3"}))
    hatch.setSubSymbol(QgsLineSymbol([markers]))
    exporter._convert_symbol(QgsFillSymbol([hatch]), "s", "src", "q2vt", 14, 24)
    image = exporter.pattern_images[exporter.style["layers"][0]["paint"]["fill-pattern"]].img_1x
    spacing = 3.2 * 96 / 25.4
    side = int(spacing / _math.sin(_math.radians(45)))
    assert image.size == (side, side)
    assert image.getchannel("A").getextrema()[1] > 200


def test_dense_marker_line_is_a_line_pattern(plugin):
    """Markers closer than their size (moss: 1 mm every 0.2 mm) are a line
    pattern drawn by QGIS: MapLibre widens a line symbol's spacing to its
    length, which kept one marker in five."""
    from qgis.core import QgsLineSymbol, QgsMarkerLineSymbolLayer, QgsMarkerSymbol
    exporter = _converter()
    markers = QgsMarkerLineSymbolLayer(True, 0.2)
    markers.setSubSymbol(QgsMarkerSymbol.createSimple({"name": "circle", "size": "1"}))
    exporter._convert_symbol(QgsLineSymbol([markers]), "s", "src", "q2vt", 14, 24)
    layer_def = exporter.style["layers"][0]
    assert layer_def["type"] == "line" and "line-pattern" in layer_def["paint"]
    image = exporter.pattern_images[layer_def["paint"]["line-pattern"]].img_1x
    assert image.width & (image.width - 1) == 0 and layer_def["paint"]["line-width"] == image.height


def test_line_blur_spreads_the_same_ink(plugin):
    """A blurred line (a firefly glow) fades over the scale of QGIS's stack
    blur (exponential smoothing both ways: QgsImageOperation) with the same
    ink, as a line with a triangular profile: the blur was ignored."""
    import math as _math
    from qgis.core import QgsBlurEffect, QgsEffectStack, QgsLineSymbol, QgsSimpleLineSymbolLayer
    exporter = _converter()
    line = QgsSimpleLineSymbolLayer()
    line.setWidth(1.8)
    blur = QgsBlurEffect()
    blur.setBlurLevel(3.2)
    blur.setBlurUnit(Qgis.RenderUnit.Millimeters)
    line.setPaintEffect(QgsEffectStack(blur))
    exporter._convert_symbol(QgsLineSymbol([line]), "s", "src", "q2vt", 14, 24)
    paint = exporter.style["layers"][0]["paint"]
    px = 96 / 25.4
    width = 1.8 * px
    scale = -1 / _math.log(0.875)  # radius 12 px: weight 2 of 16
    half = width / 2 + 2 * scale
    assert paint["line-width"] == pytest.approx(2 * half, rel=0.01)
    assert paint["line-blur"] == pytest.approx(half, rel=0.01)
    assert paint["line-opacity"] == pytest.approx(width / half, rel=0.01)
    assert not exporter.context.diagnostics.by_code("Q2VT_UNSUPPORTED_EFFECT")


def test_screen_blended_layer_is_left_out(plugin):
    """A layer drawn only through a Screen blend (an overlay for dark maps)
    leaves a white map white in QGIS: it is left out, not drawn plainly."""
    from qgis.core import QgsDrawSourceEffect, QgsEffectStack, QgsFillSymbol, QgsSimpleFillSymbolLayer
    from qgis.PyQt.QtGui import QColor, QPainter
    exporter = _converter()
    fill = QgsSimpleFillSymbolLayer(QColor("orange"))
    source = QgsDrawSourceEffect()
    source.setBlendMode(QPainter.CompositionMode.CompositionMode_Screen)
    fill.setPaintEffect(QgsEffectStack(source))
    exporter._convert_symbol(QgsFillSymbol([fill]), "s", "src", "q2vt", 14, 24)
    assert exporter.style["layers"] == []
    assert exporter.context.diagnostics.by_code("Q2VT_UNSUPPORTED_EFFECT")


def test_marker_layers_keep_their_angles_against_the_first(plugin):
    """Each layer of a marker on a line turns by the line's angle plus its own
    (a data-defined angle per layer): the sprite keeps each layer's angle
    against the first one's, which drives icon-rotate. Without the field
    QGIS drew every layer at 0 degrees (a measure line's arrowheads)."""
    from qgis.core import (QgsMarkerSymbol, QgsProperty, QgsSimpleMarkerSymbolLayer,
                           QgsSymbolLayer)
    exporter = _converter()
    tick = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Line, 8)
    head = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Triangle, 3)
    head.setAngle(90)
    for layer, base in ((tick, 0.0), (head, 90.0)):
        layer.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyAngle,
                                     QgsProperty.fromExpression(f'"q2vt_mat_angle" - 90 + {base}'))
    symbol = QgsMarkerSymbol([tick, head])
    sprite = exporter._relative_layer_angles(symbol, tick)
    assert [layer.angle() for layer in sprite.symbolLayers()] == [0.0, 90.0]
    assert not any(layer.dataDefinedProperties().isActive(QgsSymbolLayer.Property.PropertyAngle)
                   for layer in sprite.symbolLayers())
    # The style's symbol keeps its rotation for icon-rotate.
    assert symbol.symbolLayer(0).dataDefinedProperties().isActive(QgsSymbolLayer.Property.PropertyAngle)


def test_random_values_of_dense_marker_lines_stay_with_qgis(plugin):
    """The random size of a dense marker line's markers is left to QGIS,
    which draws a new one for every marker of the line pattern; one value
    per feature (a generated field) made every marker alike."""
    from qgis.core import QgsMarkerLineSymbolLayer, QgsMarkerSymbol
    from q2vt_plugin.src.core.ddp_fetcher import dense_marker_line
    markers = QgsMarkerLineSymbolLayer(True, 0.3)
    markers.setSubSymbol(QgsMarkerSymbol.createSimple({"name": "circle", "size": "0.375"}))
    assert dense_marker_line(markers)
    markers.setInterval(1.0)
    assert not dense_marker_line(markers)  # MapLibre spaces them right
    hashes = QgsMarkerLineSymbolLayer(True, 3.0)  # strokes across the line
    hashes.setSubSymbol(QgsMarkerSymbol.createSimple({"name": "line", "size": "3"}))
    assert not dense_marker_line(hashes)
    markers.setInterval(0.3)
    markers.setRotateSymbols(False)
    assert not dense_marker_line(markers)
