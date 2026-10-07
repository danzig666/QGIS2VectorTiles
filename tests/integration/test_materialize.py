"""Materialized components render like the original QGIS symbol.

Each test renders the original layer with QGIS and the exported dataset(s)
with the converted symbol(s), also with QGIS, and compares inked pixels.
"""

import math
import pytest
from qgis.core import (Qgis, QgsFeature, QgsField, QgsFillSymbol, QgsGeometry,
                       QgsHashedLineSymbolLayer, QgsLinePatternFillSymbolLayer, QgsLineSymbol,
                       QgsMarkerLineSymbolLayer, QgsMarkerSymbol, QgsProcessingFeedback,
                       QgsRectangle, QgsSimpleMarkerSymbolLayer, QgsSingleSymbolRenderer,
                       QgsVectorLayer, QgsSimpleLineSymbolLayer, QgsLineSymbolLayer)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor

from q2vt_fixtures import reset_project, to_geopackage
from q2vt_render import ink_mask, mask_difference, render

EXTENT = QgsRectangle(-120, -120, 120, 120)


def _layer(kind, wkts, path):
    layer = QgsVectorLayer(f"{kind}?crs=EPSG:3857", "src", "memory")
    layer.dataProvider().addAttributes([QgsField("id", QVariant.Int)])
    layer.updateFields()
    feats = []
    for i, wkt in enumerate(wkts):
        f = QgsFeature(layer.fields())
        f.setAttributes([i])
        f.setGeometry(QgsGeometry.fromWkt(wkt))
        feats.append(f)
    layer.dataProvider().addFeatures(feats)
    return to_geopackage(layer, path)


def _export(plugin, layer, tmp_path):
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener
    from q2vt_plugin.src.core.rules_exporter import RulesExporter
    from fidelity.diagnostics import DiagnosticCollector
    reset_project(layer)
    diags = DiagnosticCollector()
    rules = RulesFlattener(0, 22, str(tmp_path), QgsProcessingFeedback(), diags).flatten_all_rules()
    utils = tmp_path / "utils"
    utils.mkdir()
    layers, rules = RulesExporter(rules, EXTENT, 0, 22, str(utils), 0, QgsProcessingFeedback(),
                                  diagnostics=diags).export()
    by_name = {l.name(): l for l in layers}
    rendered = []
    for rule in rules:
        out = by_name[rule.output_dataset]
        out.setRenderer(QgsSingleSymbolRenderer(rule.rule.symbol().clone()))
        rendered.append(out)
    return rendered, rules, diags


def _marker(shape=Qgis.MarkerShape.ArrowHeadFilled, size=12):
    m = QgsSimpleMarkerSymbolLayer(shape, size)
    m.setSizeUnit(Qgis.RenderUnit.Pixels)
    m.setColor(QColor("black"))
    m.setStrokeColor(QColor("black"))
    return QgsMarkerSymbol([m])


LINES = ["LINESTRING(-100 -80, -20 -80, -20 40, 90 90)", "LINESTRING(80 -100, 30 -30)"]


@pytest.mark.parametrize("placements", [
    Qgis.MarkerLinePlacement.FirstVertex,
    Qgis.MarkerLinePlacement.LastVertex,
    Qgis.MarkerLinePlacement.CentralPoint,
    Qgis.MarkerLinePlacement.SegmentCenter,
    Qgis.MarkerLinePlacement.InnerVertices,
    Qgis.MarkerLinePlacement.Vertex,
])
def test_marker_line_positions_match_qgis(plugin, tmp_path, placements):
    layer = _layer("LineString", LINES, str(tmp_path / "l.gpkg"))
    ml = QgsMarkerLineSymbolLayer()
    ml.setPlacements(placements)
    ml.setSubSymbol(_marker())
    if placements in (Qgis.MarkerLinePlacement.FirstVertex, Qgis.MarkerLinePlacement.LastVertex):
        ml.setOffset(6)  # screen offset is exact at line ends
        ml.setOffsetUnit(Qgis.RenderUnit.Pixels)
    else:
        ml.setOffset(5)  # QGIS offsets the line first; exact in map units
        ml.setOffsetUnit(Qgis.RenderUnit.MapUnits)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([ml])))
    reference = ink_mask(render([layer], EXTENT))
    outputs, rules, diags = _export(plugin, layer, tmp_path)
    assert [r.recipe.kind for r in rules] == ["marker_points"]
    assert not diags.by_code("Q2VT_MARKER_PLACEMENT_APPROX")
    ours = ink_mask(render(outputs, EXTENT))
    assert mask_difference(reference, ours) < 0.05


def test_marker_line_on_polygon_outline(plugin, tmp_path):
    layer = _layer("Polygon", ["POLYGON((-80 -80, 80 -80, 80 60, -80 60, -80 -80))"],
                   str(tmp_path / "p.gpkg"))
    ml = QgsMarkerLineSymbolLayer()
    ml.setPlacements(Qgis.MarkerLinePlacement.Vertex)
    ml.setSubSymbol(_marker(Qgis.MarkerShape.Triangle, 10))
    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, ml)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    reference = ink_mask(render([layer], EXTENT))
    outputs, _, _ = _export(plugin, layer, tmp_path)
    assert mask_difference(reference, ink_mask(render(outputs, EXTENT))) < 0.05


def test_hash_line_converted_to_markers(plugin, tmp_path):
    layer = _layer("LineString", LINES, str(tmp_path / "h.gpkg"))
    hl = QgsHashedLineSymbolLayer()
    hl.setPlacements(Qgis.MarkerLinePlacement.Vertex)
    hl.setHashLength(14)
    hl.setHashLengthUnit(Qgis.RenderUnit.Pixels)
    hl.setHashAngle(30)
    hl.subSymbol().symbolLayer(0).setColor(QColor("black"))
    hl.subSymbol().symbolLayer(0).setWidth(2)
    hl.subSymbol().symbolLayer(0).setWidthUnit(Qgis.RenderUnit.Pixels)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([hl])))
    reference = ink_mask(render([layer], EXTENT))
    outputs, _, _ = _export(plugin, layer, tmp_path)
    assert mask_difference(reference, ink_mask(render(outputs, EXTENT))) < 0.08


@pytest.mark.parametrize("angle,offset", [(0, 0), (0, 3), (90, 2)])
def test_map_unit_hatch_matches_qgis(plugin, tmp_path, angle, offset):
    layer = _layer("Polygon", [
        "POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83),(-40 -20, 0 -20, 0 20, -40 20, -40 -20))"],
        str(tmp_path / "hatch.gpkg"))
    lp = QgsLinePatternFillSymbolLayer()
    lp.setLineAngle(angle)
    lp.setDistance(12)
    lp.setDistanceUnit(Qgis.RenderUnit.MapUnits)
    lp.setOffset(offset)
    lp.setOffsetUnit(Qgis.RenderUnit.MapUnits)
    line = lp.subSymbol().symbolLayer(0)
    line.setColor(QColor("black"))
    line.setWidth(1)
    line.setWidthUnit(Qgis.RenderUnit.Pixels)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([lp])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    assert rules[0].recipe.kind == "hatch_lines"
    ours = ink_mask(render(outputs, EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.05


def test_rotated_map_unit_hatch_keeps_angle_and_spacing(plugin, tmp_path):
    layer = _layer("Polygon", ["POLYGON((-90 -90, 90 -90, 90 90, -90 90, -90 -90))"],
                   str(tmp_path / "rot.gpkg"))
    lp = QgsLinePatternFillSymbolLayer()
    lp.setLineAngle(30)
    lp.setDistance(15)
    lp.setDistanceUnit(Qgis.RenderUnit.MapUnits)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([lp])))
    outputs, _, _ = _export(plugin, layer, tmp_path)
    import math
    feats = list(outputs[0].getFeatures())
    assert len(feats) > 5
    normal = (-math.sin(math.radians(30)), math.cos(math.radians(30)))
    offsets = set()
    for f in feats:
        line = f.geometry().asPolyline()
        (x0, y0), (x1, y1) = (line[0].x(), line[0].y()), (line[-1].x(), line[-1].y())
        assert math.degrees(math.atan2(y1 - y0, x1 - x0)) % 180 == pytest.approx(30, abs=1e-6)
        offsets.add(round(normal[0] * x0 + normal[1] * y0, 6))
    steps = sorted(offsets)
    gaps = {round(b - a, 6) for a, b in zip(steps, steps[1:])}
    assert gaps == {15.0}


@pytest.mark.parametrize("disp_x,disp_y,off_x,off_y", [(0, 0, 0, 0), (0, 0, 3, 2), (5, 0, 0, 0), (0, 4, 0, 0), (5, 0, 3, 2)])
def test_map_unit_point_pattern_matches_qgis(plugin, tmp_path, disp_x, disp_y, off_x, off_y):
    from qgis.core import QgsPointPatternFillSymbolLayer
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83))"],
                   str(tmp_path / "pp.gpkg"))
    pp = QgsPointPatternFillSymbolLayer()
    marker = _marker(Qgis.MarkerShape.Square, 3)
    marker.symbolLayer(0).setSizeUnit(Qgis.RenderUnit.MapUnits)
    marker.symbolLayer(0).setStrokeStyle(0)
    pp.setSubSymbol(marker)
    for name, value in (("DistanceX", 12), ("DistanceY", 10), ("DisplacementX", disp_x),
                        ("DisplacementY", disp_y), ("OffsetX", off_x), ("OffsetY", off_y)):
        getattr(pp, f"set{name}")(value)
        getattr(pp, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    pp.setClipMode(Qgis.MarkerClipMode.CentroidWithin)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([pp])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, diags = _export(plugin, layer, tmp_path)
    # Dense at low zooms (texture), points from the zoom where the 10 m
    # spacing reaches 12 px on screen.
    kinds = [r.recipe.kind if r.recipe else None for r in rules]
    assert kinds == [None, "grid_points"]
    assert rules[1].get_attr("o") == rules[0].get_attr("i") + 1
    assert not diags.by_code("Q2VT_PATTERN_APPROXIMATE")
    ours = ink_mask(render(outputs[1:], EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.05


def test_dense_map_unit_point_pattern_uses_per_zoom_textures(plugin, tmp_path):
    from qgis.core import QgsPointPatternFillSymbolLayer
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83))"],
                   str(tmp_path / "pp.gpkg"))
    pp = QgsPointPatternFillSymbolLayer()
    marker = _marker(Qgis.MarkerShape.Square, 0.5)
    marker.symbolLayer(0).setSizeUnit(Qgis.RenderUnit.MapUnits)
    pp.setSubSymbol(marker)
    for name in ("DistanceX", "DistanceY"):
        getattr(pp, f"set{name}")(1.25)
        getattr(pp, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([pp])))
    reset_project(layer)
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener
    from fidelity.diagnostics import DiagnosticCollector
    rules = RulesFlattener(14, 17, str(tmp_path), QgsProcessingFeedback(),
                           DiagnosticCollector()).flatten_all_rules()
    # 1.25 m is below 12 px up to zoom 17: no point features at all.
    assert [r.recipe for r in rules] == [None]

    from q2vt_plugin.src.core import maplibre_converter as mc
    exporter = mc.QgisMapLibreStyleExporter.__new__(mc.QgisMapLibreStyleExporter)
    exporter.pattern_images, exporter.marker_symbols, exporter.marker_counter = {}, {}, 0
    exporter.profile = mc.ExportProfile()
    exporter.context = mc.ConversionContext(DiagnosticCollector())
    exporter.style, exporter.maxzoom = {"layers": []}, 17
    mc.PropertyExtractor.context = exporter.context
    exporter.context.reference_zoom = 14
    exporter._convert_symbol(rules[0].rule.symbol(), "s", "src", "q2vt", 14, 24)
    pattern = exporter.style["layers"][0]["paint"]["fill-pattern"]
    assert pattern[:2] == ["step", ["zoom"]] and pattern[3::2] == [15, 16, 17, 18, 19]
    cells = [exporter.pattern_images[name].img_1x for name in pattern[2::2]]
    assert all(cell.getbbox() is not None for cell in cells)
    # Map-unit textures keep MapLibre's tile-zoom scaling (no screen flag).
    assert "metadata" not in exporter.style["layers"][0]


def test_screen_unit_pattern_fill_is_drawn_at_true_size(plugin):
    """Patterns sized only in screen units are flagged for the patched
    MapLibre (drawn at the real zoom) and rendered at their true size, not
    at the 1/sqrt(2) compromise for stock tile-zoom scaling."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    from q2vt_plugin.src.core import maplibre_converter as mc
    from fidelity.patterns import point_pattern_cell
    from fidelity.diagnostics import DiagnosticCollector
    pp = QgsPointPatternFillSymbolLayer()
    marker = _marker(Qgis.MarkerShape.Square, 1)
    marker.symbolLayer(0).setSizeUnit(Qgis.RenderUnit.Millimeters)
    pp.setSubSymbol(marker)
    for name in ("DistanceX", "DistanceY"):
        getattr(pp, f"set{name}")(4)
        getattr(pp, f"set{name}Unit")(Qgis.RenderUnit.Millimeters)
    exporter = mc.QgisMapLibreStyleExporter.__new__(mc.QgisMapLibreStyleExporter)
    exporter.pattern_images, exporter.marker_symbols, exporter.marker_counter = {}, {}, 0
    exporter.profile = mc.ExportProfile()
    exporter.context = mc.ConversionContext(DiagnosticCollector())
    exporter.style, exporter.maxzoom = {"layers": []}, 17
    mc.PropertyExtractor.context = exporter.context
    exporter.context.reference_zoom = 14
    exporter._convert_symbol(QgsFillSymbol([pp]), "s", "src", "q2vt", 14, 24)
    layer_def = exporter.style["layers"][0]
    assert layer_def["metadata"] == {exporter.SCREEN_PATTERN_FLAG: True}
    cell = exporter.pattern_images[layer_def["paint"]["fill-pattern"]].img_1x
    px = 4 * 96 / 25.4
    width, height, _, _ = point_pattern_cell(px, px, 0, 0)
    assert cell.size == (width, height)
    assert exporter._screen_scale() == exporter.TEXTURE_SCREEN_SCALE  # reset afterwards


@pytest.mark.parametrize("curved,repeated,head_type", [
    (False, False, 0), (True, False, 0), (True, True, 2), (False, True, 2), (True, False, 1)])
def test_map_unit_arrows_match_qgis(plugin, tmp_path, curved, repeated, head_type):
    from qgis.core import QgsArrowSymbolLayer
    layer = _layer("LineString", ["LINESTRING(-100 -80, -20 60, 20 -40, 100 60)"],
                   str(tmp_path / "arrow.gpkg"))
    arrow = QgsArrowSymbolLayer()
    for name, value in (("ArrowWidth", 6), ("ArrowStartWidth", 6), ("HeadLength", 18),
                        ("HeadThickness", 7)):
        getattr(arrow, f"set{name}")(value)
        getattr(arrow, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    arrow.setIsCurved(curved)
    arrow.setIsRepeated(repeated)
    arrow.setHeadType(QgsArrowSymbolLayer.HeadType(head_type))
    arrow.subSymbol().symbolLayer(0).setColor(QColor("black"))
    arrow.subSymbol().symbolLayer(0).setStrokeStyle(0)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([arrow])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, diags = _export(plugin, layer, tmp_path)
    assert [r.recipe.kind for r in rules][0] == "arrow_polygons"
    ours = ink_mask(render(outputs, EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.01


@pytest.mark.parametrize("clip", ["Shape", "CentroidWithin", "CompletelyWithin", "NoClipping"])
def test_point_pattern_clip_modes_match_qgis(plugin, tmp_path, clip):
    from qgis.core import QgsPointPatternFillSymbolLayer
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -80 100, -97 71, -97 -83))"],
                   str(tmp_path / "pp.gpkg"))
    pp = QgsPointPatternFillSymbolLayer()
    marker = _marker(Qgis.MarkerShape.HalfSquare, 4)
    marker.symbolLayer(0).setSizeUnit(Qgis.RenderUnit.MapUnits)
    marker.symbolLayer(0).setStrokeStyle(0)
    pp.setSubSymbol(marker)
    for name, value in (("DistanceX", 15), ("DistanceY", 12)):
        getattr(pp, f"set{name}")(value)
        getattr(pp, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    pp.setClipMode(getattr(Qgis.MarkerClipMode, clip))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([pp])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, diags = _export(plugin, layer, tmp_path)
    grid = [o for o, r in zip(outputs, rules) if r.recipe]
    ours = ink_mask(render(grid, EXTENT, (240, 240)))

    assert mask_difference(reference, ours) < 0.15


def test_svg_fill_without_svg_draws_only_its_stroke(plugin, tmp_path):
    from qgis.core import QgsSVGFillSymbolLayer
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83))"],
                   str(tmp_path / "svg.gpkg"))
    # As loaded from a style with neither an SVG file nor embedded data.
    fill = QgsSVGFillSymbolLayer.create({"svgFile": "", "data": "", "width": "20"})
    fill.setSubSymbol(QgsLineSymbol.createSimple({"color": "black", "width": "0.5"}))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([fill])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    assert reference  # QGIS draws only the fill's stroke sub-symbol
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    assert [r.rule.symbol().type() for r in rules] == [Qgis.SymbolType.Line]
    ours = ink_mask(render(outputs, EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.05


@pytest.mark.parametrize("wkt", [
    "POLYGON((0 0,10 0,10 10,0 10,0 0),(3 3,7 3,7 7,3 7,3 3))",
    "MULTIPOLYGON(((0 0,10 0,10 10,0 10,0 0),(3 3,7 3,7 7,3 7,3 3)))"])
def test_polygon_offset_moves_every_ring_inwards(plugin, wkt):
    from qgis.core import QgsExpression, QgsExpressionContext, QgsFeature, QgsGeometry
    from fidelity import materialize as mat
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt(wkt))
    context = QgsExpressionContext()
    context.setFeature(feature)
    expr = QgsExpression(mat.polygon_offset_expression(
        mat.Recipe("polygon_offset", params=(("offset", 1.0),))))
    result = expr.evaluate(context)
    assert result.asWkt() == "MultiLineString ((1 1, 9 1, 9 9, 1 9, 1 1),(2 2, 8 2, 8 8, 2 8, 2 2))"


@pytest.mark.parametrize("on_surface", [False, True])
def test_centroid_fill_position_matches_qgis(plugin, tmp_path, on_surface):
    from qgis.core import QgsCentroidFillSymbolLayer
    # U-shaped polygon with a hole: exterior centroid, true centroid and
    # point-on-surface are all different points.
    layer = _layer("Polygon", ["POLYGON((-100 -90, 100 -90, 100 90, 40 90, 40 -30, -40 -30, "
                               "-40 90, -100 90, -100 -90),(-80 -70, -60 -70, -60 -50, -80 -50, "
                               "-80 -70))"], str(tmp_path / "cf.gpkg"))
    fill = QgsCentroidFillSymbolLayer()
    fill.setSubSymbol(_marker(Qgis.MarkerShape.Square, 10))
    fill.setPointOnSurface(on_surface)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([fill])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    for output in outputs:  # the styler draws centroid points with the sub-symbol
        output.setRenderer(QgsSingleSymbolRenderer(fill.subSymbol().clone()))
    ours = ink_mask(render(outputs, EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.05


@pytest.mark.parametrize("along,offset,geometry", [
    (0, 0, "line"), (7, 0, "line"), (7, 4, "line"), (0, 0, "polygon"), (5, -3, "polygon"),
    (0, 0, "holed")])
def test_map_unit_interval_markers_match_qgis(plugin, tmp_path, along, offset, geometry):
    if geometry == "line":
        layer = _layer("LineString", LINES, str(tmp_path / "iv.gpkg"))
    elif geometry == "holed":  # the style gallery's polygon
        layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 40, -20 71, -97 40, -97 -83),"
                                   "(-60 -40, -30 -40, -30 -10, -60 -10, -60 -40))"],
                       str(tmp_path / "iv.gpkg"))
    else:
        layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83))"],
                       str(tmp_path / "iv.gpkg"))
    markers = QgsMarkerLineSymbolLayer(True, 17)
    markers.setIntervalUnit(Qgis.RenderUnit.MapUnits)
    markers.setOffsetAlongLine(along)
    markers.setOffsetAlongLineUnit(Qgis.RenderUnit.MapUnits)
    markers.setOffset(offset)
    markers.setOffsetUnit(Qgis.RenderUnit.MapUnits)
    markers.setSubSymbol(_marker(Qgis.MarkerShape.ArrowHeadFilled, 9))
    symbol = QgsLineSymbol([markers]) if geometry == "line" else QgsFillSymbol([markers])
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    exact = [(o, r) for o, r in zip(outputs, rules) if r.recipe is not None]
    assert exact and exact[-1][1].recipe.placements == ("Interval",)
    ours = ink_mask(render([o for o, _ in exact], EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.08


def test_nested_geometry_generators_match_qgis(plugin, tmp_path):
    from qgis.core import QgsGeometryGeneratorSymbolLayer
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83))"],
                   str(tmp_path / "wave.gpkg"))
    inner = QgsGeometryGeneratorSymbolLayer.create({
        "geometryModifier": "triangular_wave($geometry, wavelength:=25, amplitude:=8)",
        "SymbolType": "Line"})
    inner.setSubSymbol(QgsLineSymbol.createSimple({"color": "black", "width": "0.6"}))
    outer = QgsGeometryGeneratorSymbolLayer.create({"geometryModifier": "$geometry",
                                                    "SymbolType": "Line"})
    outer.setSubSymbol(QgsLineSymbol([inner]))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([outer])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, diags = _export(plugin, layer, tmp_path)
    assert len(rules) == 1
    for output, rule in zip(outputs, rules):
        # Line generators are exported as line rules on the generated lines.
        assert rule.pre_generator and rule.rule.symbol().symbolLayer(0).layerType() == "SimpleLine"
        output.setRenderer(QgsSingleSymbolRenderer(rule.rule.symbol().clone()))
    ours = ink_mask(render(outputs, EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.05


@pytest.mark.parametrize("length,before,expected", [
    (40, 10, 18.4), (40, 25, 0.0), (80, 30, 8.1), (80, 50, 0.0)])
def test_interval_marker_angle_averages_like_qgis(plugin, length, before, expected):
    """QGIS averages a marker's direction over ``averageAngleLength`` centred
    on it (+-length / 2); the expected angles were measured on QGIS renders of
    a marker ``before`` units ahead of a right-angle corner."""
    from qgis.core import QgsExpression, QgsExpressionContext, QgsExpressionContextUtils
    from fidelity import materialize as mat
    recipe = mat.interval_points(1000, 200 - before)
    recipe = mat.Recipe(recipe.kind, recipe.placements, recipe.params + (("average", length),))
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt("LINESTRING(0 0, 200 0, 200 -300)"))
    context = QgsExpressionContext([QgsExpressionContextUtils.globalScope()])
    context.setFeature(feature)
    point = QgsExpression(mat.interval_points_expression(recipe)).evaluate(context)
    azimuth = point.constGet().geometryN(0).z() if point.isMultipart() else point.constGet().z()
    # Azimuth 90 runs along the first segment; the corner turns it to 180.
    assert azimuth - 90 == pytest.approx(expected, abs=0.3)


def test_screen_averaged_marker_angles_are_per_zoom(plugin, tmp_path):
    """A 4 mm averaging length covers half as much line at every zoom in:
    one dataset per zoom, each averaging over its own length (Műemléki
    környezet: markers averaged at a far-out zoom tilted along straight
    edges)."""
    layer = _layer("LineString", LINES, str(tmp_path / "pz.gpkg"))
    stroke = QgsMarkerLineSymbolLayer(True, 12)
    stroke.setIntervalUnit(Qgis.RenderUnit.MapUnits)
    stroke.setAverageAngleLength(4)
    stroke.setAverageAngleUnit(Qgis.RenderUnit.Millimeters)
    stroke.setSubSymbol(_marker())
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([stroke])))
    _, rules, _ = _export(plugin, layer, tmp_path)
    averages = {r.get_attr("o"): r.recipe.param("average") for r in rules
                if r.recipe is not None and r.recipe.kind == "marker_points"}
    assert len(averages) > 3 and all(r.get_attr("o") == r.get_attr("i") for r in rules
                                     if r.recipe is not None and r.get_attr("i") < 22)
    for zoom in sorted(averages)[1:]:
        assert averages[zoom] == pytest.approx(averages[zoom - 1] / 2)


def test_marker_line_with_float_noise_offset_is_exported(plugin, tmp_path):
    """Vasúti fővonal: an offset of 5.55e-17 map units (float noise saved in
    the style) built a zero offset curve, which failed the whole dataset."""
    layer = _layer("LineString", LINES, str(tmp_path / "fn.gpkg"))
    stroke = QgsMarkerLineSymbolLayer(True, 12)
    stroke.setIntervalUnit(Qgis.RenderUnit.MapUnits)
    stroke.setOffset(5.55112e-17)
    stroke.setOffsetUnit(Qgis.RenderUnit.MapUnits)
    stroke.setSubSymbol(_marker())
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([stroke])))
    outputs, rules, diags = _export(plugin, layer, tmp_path)
    assert any(r.recipe is not None and r.recipe.kind == "marker_points" for r in rules)
    assert not diags.by_code("Q2VT_RULE_EXPORT_FAILED")


def test_screen_interval_markers_are_placed_per_zoom(plugin, tmp_path):
    """Tervezési terület / Tervezett fasor: a 4 mm interval halves in map
    units at every zoom in; MapLibre's own line placement dropped markers
    at tile edges and ring starts, so positions are materialized per zoom."""
    layer = _layer("LineString", LINES, str(tmp_path / "si.gpkg"))
    stroke = QgsMarkerLineSymbolLayer(True, 4)
    stroke.setIntervalUnit(Qgis.RenderUnit.Millimeters)
    stroke.setSubSymbol(_marker())
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([stroke])))
    _, rules, _ = _export(plugin, layer, tmp_path)
    placed = [r for r in rules if r.recipe is not None and r.recipe.kind == "marker_points"]
    zooms = {r.get_attr("o") for r in placed}
    assert len(zooms) > 3
    # Eighths of a zoom, each with 4 mm (96 dpi, Web Mercator grid) at its
    # middle: within +-4 % of QGIS's spacing anywhere in the band.
    from fidelity.zoom import zoom_to_scale
    top = max(zooms)
    assert len([r for r in placed if r.get_attr("o") == top]) == 8
    for rule in placed:
        band = rule.visibility
        if band.max_zoom is None:
            continue
        middle = (band.min_zoom + band.max_zoom) / 2
        assert band.max_zoom - band.min_zoom == pytest.approx(1 / 8)
        assert rule.recipe.param("interval") == pytest.approx(0.004 * zoom_to_scale(middle), rel=1e-6)
    # Beyond the last tile zoom, the native placement keeps the screen spacing.
    assert any(r.recipe is None and r.visibility is not None
               and r.visibility.min_zoom == top + 1 for r in rules)


@pytest.mark.parametrize("ring_filter", [1, 2])
@pytest.mark.parametrize("kind", ["line", "markers"])
def test_ring_filters_match_qgis(plugin, tmp_path, ring_filter, kind):
    from qgis.core import QgsLineSymbolLayer, QgsSimpleLineSymbolLayer
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83),"
                               "(-40 -40, 10 -40, 10 10, -40 10, -40 -40))"],
                   str(tmp_path / "rf.gpkg"))
    if kind == "line":
        stroke = QgsSimpleLineSymbolLayer(QColor("black"), 1.0)
    else:
        stroke = QgsMarkerLineSymbolLayer(True, 12)
        stroke.setIntervalUnit(Qgis.RenderUnit.MapUnits)
        stroke.setSubSymbol(_marker(Qgis.MarkerShape.Square, 6))
    stroke.setRingFilter(QgsLineSymbolLayer.RenderRingFilter(ring_filter))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([stroke])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    exact = [(o, r) for o, r in zip(outputs, rules) if r.recipe is not None]
    for output, rule in exact:
        if rule.rule.symbol().type() == Qgis.SymbolType.Fill:  # as the styler does
            output.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol(
                [rule.rule.symbol().symbolLayer(0).clone()])))
    ours = ink_mask(render([o for o, _ in exact], EXTENT, (240, 240)))
    # Markers: QGIS averages corner angles over a screen length (4 mm), which a
    # multi-zoom point dataset reproduces at one zoom only.
    assert mask_difference(reference, ours) < (0.06 if kind == "line" else 0.1)


@pytest.mark.parametrize("shape,size,angle,dx,clip", [
    ("Cross2", 80, 0, 40, "Shape"),          # lattice of diagonals (Erdőtelepítés)
    ("Line", 15, 45, 10, "Shape"),           # continuous diagonal lines
    ("Line", 300, 90, 300, "Shape"),         # long horizontal lines
    ("Cross", 12, 0, 20, "CentroidWithin"),  # unclipped crosses
    ("Cross", 12, 0, 20, "NoClipping"),
    ("Cross2", 12, 0, 20, "CompletelyWithin"),
    ("ArrowHead", 10, 30, 20, "Shape"),
])
def test_stroke_marker_patterns_match_qgis(plugin, tmp_path, shape, size, angle, dx, clip):
    from qgis.core import QgsPointPatternFillSymbolLayer
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -80 100, -97 71, -97 -83),"
                               "(-40 -40, 10 -40, 10 10, -40 10, -40 -40))"],
                   str(tmp_path / "sm.gpkg"))
    pp = QgsPointPatternFillSymbolLayer()
    marker = QgsSimpleMarkerSymbolLayer(getattr(Qgis.MarkerShape, shape), size, angle)
    marker.setSizeUnit(Qgis.RenderUnit.MapUnits)
    marker.setStrokeColor(QColor("black"))
    marker.setStrokeWidth(0.4)
    marker.setStrokeWidthUnit(Qgis.RenderUnit.Millimeters)
    pp.setSubSymbol(QgsMarkerSymbol([marker]))
    for name, value in (("DistanceX", dx), ("DistanceY", dx)):
        getattr(pp, f"set{name}")(value)
        getattr(pp, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    pp.setClipMode(getattr(Qgis.MarkerClipMode, clip))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([pp])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    lines = [(o, r) for o, r in zip(outputs, rules) if r.recipe and r.recipe.param("segments")]
    assert lines and lines[0][1].rule.symbol().type() == Qgis.SymbolType.Line
    ours = ink_mask(render([o for o, _ in lines], EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.06


@pytest.mark.parametrize("density", [0.0, 50.0])
def test_random_points_follow_the_qgis_count(plugin, density):
    """``QgsRandomMarkerFillSymbolLayer::render``: ``count`` points, or
    ``ceil(count * area / densityArea)``, inside the polygon (not in holes)."""
    from qgis.core import QgsExpression, QgsExpressionContext
    from fidelity import materialize as mat
    polygon = QgsGeometry.fromWkt("POLYGON((0 0, 100 0, 100 60, 0 60, 0 0),"
                                  "(20 20, 80 20, 80 40, 20 40, 20 20))")
    feature = QgsFeature()
    feature.setGeometry(polygon)
    context = QgsExpressionContext()
    context.setFeature(feature)
    recipe = mat.random_points_recipe(7, density, 12345, "EPSG:3857")
    result = QgsExpression(mat.random_points_expression(recipe)).evaluate(context)
    points = [QgsGeometry(p.clone()) for p in result.constGet()]
    expected = 7 if not density else math.ceil(7 * polygon.area() / density)
    assert len(points) == expected
    assert all(polygon.contains(p) for p in points)
    again = QgsExpression(mat.random_points_expression(recipe)).evaluate(context)
    assert again.asWkt() == result.asWkt()  # seeded: stable between exports


def test_random_marker_fill_is_materialized(plugin, tmp_path):
    from qgis.core import QgsRandomMarkerFillSymbolLayer
    layer = _layer("Polygon", ["POLYGON((-100 -90, 100 -90, 100 90, -100 90, -100 -90))",
                               "POLYGON((-50 -50, -10 -50, -10 -10, -50 -10, -50 -50))"],
                   str(tmp_path / "rnd.gpkg"))
    fill = QgsRandomMarkerFillSymbolLayer(9, Qgis.PointCountMethod.Absolute, 0, 77)
    fill.setSubSymbol(_marker(Qgis.MarkerShape.Circle, 3))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([fill])))
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    points = [o for o, r in zip(outputs, rules) if r.recipe is not None]
    assert len(points) == 1
    parts = sum(len(f.geometry().asGeometryCollection()) for f in points[0].getFeatures())
    assert parts == 2 * 9  # QGIS draws the count per feature


@pytest.mark.parametrize("geometry,offset,ring_filter,dash_offset", [
    ("line", 0, 0, 0), ("line", 4, 0, 3), ("holed", 0, 0, 0), ("holed", 3, 0, 5),
    ("holed", 0, 1, 0)])
def test_map_unit_dashes_match_qgis(plugin, tmp_path, geometry, offset, ring_filter, dash_offset):
    """Qt starts the dash pattern on every line and ring and runs it across
    vertices: the exported dashes are the QGIS dashes."""
    from qgis.PyQt.QtCore import Qt
    if geometry == "line":
        layer = _layer("LineString", LINES, str(tmp_path / "dash.gpkg"))
    else:
        layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 40, -20 71, -97 40, -97 -83),"
                                   "(-60 -40, -30 -40, -30 -10, -60 -10, -60 -40))"],
                       str(tmp_path / "dash.gpkg"))
    line = QgsSimpleLineSymbolLayer(QColor("black"), 2.0)
    line.setWidthUnit(Qgis.RenderUnit.MapUnits)
    line.setUseCustomDashPattern(True)
    line.setCustomDashVector([9.0, 5.0, 2.0, 5.0])
    line.setCustomDashPatternUnit(Qgis.RenderUnit.MapUnits)
    line.setPenCapStyle(Qt.PenCapStyle.FlatCap)
    line.setOffset(offset)
    line.setOffsetUnit(Qgis.RenderUnit.MapUnits)
    line.setDashPatternOffset(dash_offset)
    line.setDashPatternOffsetUnit(Qgis.RenderUnit.MapUnits)
    if ring_filter:
        line.setRingFilter(QgsLineSymbolLayer.RenderRingFilter(ring_filter))
    symbol = QgsLineSymbol([line]) if geometry == "line" else QgsFillSymbol([line])
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    dashed = [o for o, r in zip(outputs, rules) if r.recipe is not None]
    assert len(dashed) == 1 and rules[-1].recipe.kind == "dash_segments"
    for output in dashed:  # the styler draws the outline as lines
        output.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol(
            [rules[-1].rule.symbol().symbolLayer(0).clone()])))
    ours = ink_mask(render(dashed, EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.05


@pytest.mark.parametrize("size,clip,colour_ddp,textured", [
    (15, "Shape", False, True), (7.5, "Shape", False, False), (15, "CentroidWithin", False, False),
    (15, "Shape", True, False)])
def test_point_patterns_of_cell_sized_images_stay_textures(plugin, tmp_path, size, clip,
                                                           colour_ddp, textured):
    """Clipped to the shape, markers as large as their cells tile like a
    texture: points would draw the edge markers whole (sprites are not
    clipped), so the browser pattern (clipped) is kept."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83))"],
                   str(tmp_path / "pp.gpkg"))
    pp = QgsPointPatternFillSymbolLayer()
    marker = _marker(Qgis.MarkerShape.Square, size)
    marker.symbolLayer(0).setSizeUnit(Qgis.RenderUnit.MapUnits)
    if colour_ddp:  # one sprite per value: only points carry it
        from qgis.core import QgsProperty, QgsSymbolLayer
        marker.symbolLayer(0).setDataDefinedProperty(
            QgsSymbolLayer.Property.PropertyFillColor, QgsProperty.fromExpression("'red'"))
    pp.setSubSymbol(marker)
    for name in ("DistanceX", "DistanceY"):
        getattr(pp, f"set{name}")(15)
        getattr(pp, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    pp.setClipMode(getattr(Qgis.MarkerClipMode, clip))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([pp])))
    _, rules, _ = _export(plugin, layer, tmp_path)
    assert (not any(r.recipe for r in rules)) == textured


@pytest.mark.parametrize("text,angle,offset,anchor", [
    ("E", 0, (0, 0), 1), ("Bő", 30, (8, -12), 1), ("g", -50, (0, 5), 0)])
def test_map_unit_font_markers_become_their_glyphs(plugin, tmp_path, text, angle, offset, anchor):
    """A font marker in map units as its outlines (browser text is a 24 px
    distance field, blobby when scaled up)."""
    from qgis.core import QgsFontMarkerSymbolLayer
    from qgis.PyQt.QtCore import QPointF
    layer = _layer("Point", ["POINT(-30 -10)", "POINT(40 50)"], str(tmp_path / "fm.gpkg"))
    marker = QgsFontMarkerSymbolLayer("DejaVu Sans", text, 60)
    marker.setSizeUnit(Qgis.RenderUnit.MapUnits)
    marker.setColor(QColor("black"))
    marker.setAngle(angle)
    marker.setOffset(QPointF(*offset))
    marker.setOffsetUnit(Qgis.RenderUnit.MapUnits)
    marker.setHorizontalAnchorPoint(QgsMarkerSymbol().symbolLayer(0).HorizontalAnchorPoint(anchor))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol([marker])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    glyphs = [o for o, r in zip(outputs, rules) if r.recipe is not None]
    assert len(glyphs) == 1 and rules[-1].recipe.kind == "glyph"
    ours = ink_mask(render(glyphs, EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.05


def test_point_pattern_random_deviation_stays_in_range(plugin):
    """QGIS moves every pattern marker by a uniform ±max deviation (its own
    random sequence): the exported markers deviate within the same range."""
    from qgis.core import QgsExpression, QgsExpressionContext
    from fidelity import materialize as mat
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt("POLYGON((0 0, 200 0, 200 200, 0 200, 0 0))"))
    context = QgsExpressionContext()
    context.setFeature(feature)
    recipe = mat.grid_recipe(10, 10, 0, 0, 0, 0, "EPSG:3857", "feature", deviation=(3.0, 2.0),
                             seed=42)
    expression = mat.grid_expression(recipe)
    first = QgsExpression(expression).evaluate(context)
    assert QgsExpression(expression).evaluate(context).asWkt() == first.asWkt()
    shifts = [(p.x() - round(p.x() / 10) * 10, p.y() - round(p.y() / 10) * 10)
              for p in (part for part in first.constGet())]
    assert len(shifts) > 300
    assert max(abs(x) for x, _ in shifts) <= 3.0 and max(abs(y) for _, y in shifts) <= 2.0
    assert max(abs(x) for x, _ in shifts) > 2.5 and max(abs(y) for _, y in shifts) > 1.6
    mean_x = sum(x for x, _ in shifts) / len(shifts)
    assert abs(mean_x) < 0.5  # uniform around the grid node


@pytest.mark.parametrize("shape,filled", [("Diamond", False), ("Triangle", True),
                                          ("Circle", True)])
def test_shape_clipped_marker_patterns_are_cut_at_the_edge(plugin, tmp_path, shape, filled):
    """"Shape" clipping: QGIS cuts the pattern's markers at the polygon edge;
    closed simple markers are exported as polygons and outlines, clipped."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 40, -20 71, -97 40, -97 -83),"
                               "(-60 -40, -30 -40, -30 -10, -60 -10, -60 -40))"],
                   str(tmp_path / "pp.gpkg"))
    pp = QgsPointPatternFillSymbolLayer()
    marker = QgsSimpleMarkerSymbolLayer(getattr(Qgis.MarkerShape, shape), 10)
    marker.setSizeUnit(Qgis.RenderUnit.MapUnits)
    marker.setColor(QColor(84, 176, 74, 255 if filled else 0))
    marker.setStrokeColor(QColor(112, 168, 0))
    marker.setStrokeWidth(1.2)
    marker.setStrokeWidthUnit(Qgis.RenderUnit.MapUnits)
    pp.setSubSymbol(QgsMarkerSymbol([marker]))
    for name in ("DistanceX", "DistanceY"):
        getattr(pp, f"set{name}")(20)
        getattr(pp, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    pp.setClipMode(Qgis.MarkerClipMode.Shape)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([pp])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    parts = [(o, r) for o, r in zip(outputs, rules) if r.recipe is not None]
    assert {r.recipe.param("fill") for _, r in parts} == ({True, False} if filled else {False})
    ours = ink_mask(render([o for o, _ in parts], EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.12
    # Nothing is drawn outside the polygon (edge markers are cut).
    polygon = QgsGeometry.fromWkt("POLYGON((-97 -83, 53 -83, 53 40, -20 71, -97 40, -97 -83))")
    for output, _ in parts:
        for feature in output.getFeatures():
            assert polygon.buffer(0.01, 4).contains(feature.geometry())


@pytest.mark.parametrize("mode", ["shape", "centroid", "points"])
def test_pattern_pieces_give_the_whole_feature_pattern(plugin, tmp_path, monkeypatch, mode):
    """Large or detailed polygons are cut into pieces (anchored to the whole
    feature) before a pattern grid is built: the same export without pieces
    must give the same pattern."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    from fidelity import materialize as mat
    ring = [(100 * (1 + 0.2 * math.sin(9 * a)) * math.cos(a), 100 * (1 + 0.2 * math.sin(9 * a))
             * math.sin(a)) for a in (2 * math.pi * k / 400 for k in range(400))]
    wkt = "POLYGON((" + ", ".join(f"{x:.3f} {y:.3f}" for x, y in ring + ring[:1]) + "))"

    def export(folder, pieces):
        folder.mkdir()
        layer = _layer("Polygon", [wkt], str(folder / "big.gpkg"))
        pp = QgsPointPatternFillSymbolLayer()
        if mode == "points":
            marker = _marker(Qgis.MarkerShape.Square, 0.6)
            pp.setClipMode(Qgis.MarkerClipMode.CentroidWithin)
        else:
            marker = _marker(Qgis.MarkerShape.Cross, 1.2)
            marker.symbolLayer(0).setStrokeWidth(0.1)
            marker.symbolLayer(0).setStrokeWidthUnit(Qgis.RenderUnit.MapUnits)
            pp.setClipMode(Qgis.MarkerClipMode.Shape if mode == "shape"
                           else Qgis.MarkerClipMode.CentroidWithin)
        marker.symbolLayer(0).setSizeUnit(Qgis.RenderUnit.MapUnits)
        pp.setSubSymbol(marker)
        spacing = 0.7 if mode == "points" else 2.0  # pieces of 100 cells: 70 / 200 m
        for name in ("DistanceX", "DistanceY"):
            getattr(pp, f"set{name}")(spacing)
            getattr(pp, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
        layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([pp])))
        with monkeypatch.context() as patch:
            if not pieces:
                patch.setattr(mat, "grid_splittable", lambda recipe: False)
            outputs, rules, _ = _export(plugin, layer, folder)
        exported = [(o, r) for o, r in zip(outputs, rules) if r.recipe is not None]
        assert len(exported) == 1
        output = exported[0][0]
        base = [o for o, r in zip(outputs, rules) if r.recipe is None]  # the texture zooms
        polygon = next(base[0].getFeatures()).geometry() if base else None
        return output.featureCount(), QgsGeometry.collectGeometry(
            [f.geometry() for f in output.getFeatures()]), polygon

    count, ours, exported_polygon = export(tmp_path / "pieces", True)
    assert count > 4
    if mode == "shape":
        # Clip the unclipped grid of the exported polygon segment by segment.
        from qgis.core import QgsExpression, QgsExpressionContext
        polygon = exported_polygon
        feature = QgsFeature()
        feature.setGeometry(polygon)
        context = QgsExpressionContext()
        context.setFeature(feature)
        recipe = mat.grid_recipe(2.0, 2.0, 0, 0, 0, 0, "EPSG:3857", "feature",
                                 segments=mat.marker_segments("Cross", 1.2),
                                 clip_mode="none")
        grid = QgsExpression(mat.grid_expression(recipe)).evaluate(context)
        clipped = [QgsGeometry(part.clone()).intersection(polygon)
                   for part in grid.constGet()]
        expected = QgsGeometry.unaryUnion([g for g in clipped if not g.isEmpty()]).length()
        assert QgsGeometry.unaryUnion([ours]).length() == pytest.approx(expected, rel=2e-3)
        return
    _, whole, _ = export(tmp_path / "whole", False)
    assert not whole.isEmpty()
    if mode == "points":
        assert {(round(p.x(), 6), round(p.y(), 6)) for p in ours.vertices()} == \
            {(round(p.x(), 6), round(p.y(), 6)) for p in whole.vertices()}
    else:
        # Overlapping arms of neighbouring markers are dissolved per output
        # feature: compare the drawn line work.
        assert QgsGeometry.unaryUnion([ours]).length() == pytest.approx(
            QgsGeometry.unaryUnion([whole]).length(), rel=1e-6)


def test_patterns_over_the_budget_become_textures(plugin, tmp_path, monkeypatch):
    """A pattern that would need more features than the budget is drawn as a
    texture at every zoom and reported, instead of stalling the export or
    being dropped."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    from q2vt_plugin.src.core import materializer as materializer_module
    monkeypatch.setattr(materializer_module.SymbolMaterializer, "MAX_PATTERN_ELEMENTS", 1000)
    layer = _layer("Polygon", ["POLYGON((-100 -100, 100 -100, 100 100, -100 100, -100 -100))"],
                   str(tmp_path / "pp.gpkg"))
    pp = QgsPointPatternFillSymbolLayer()
    marker = _marker(Qgis.MarkerShape.Circle, 1)
    marker.symbolLayer(0).setSizeUnit(Qgis.RenderUnit.MapUnits)
    pp.setSubSymbol(marker)
    for name in ("DistanceX", "DistanceY"):  # 400 m2 / 4 m2 = 10000 markers > 1000
        getattr(pp, f"set{name}")(2)
        getattr(pp, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([pp])))
    _, rules, diags = _export(plugin, layer, tmp_path)
    assert not any(r.recipe for r in rules)
    assert diags.by_code("Q2VT_PATTERN_BUDGET")


def test_map_unit_line_offset_is_the_offset_line(plugin, tmp_path):
    """Gyorsforgalmi út: +-15 m offsets of a 10 m line; MapLibre's
    line-offset crossed itself at sharp corners, the offset line is exported."""
    layer = _layer("LineString", LINES, str(tmp_path / "off.gpkg"))
    line = QgsSimpleLineSymbolLayer(QColor("black"), 3.0)
    line.setWidthUnit(Qgis.RenderUnit.MapUnits)
    line.setOffset(6.0)
    line.setOffsetUnit(Qgis.RenderUnit.MapUnits)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([line])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    assert [r.recipe.kind for r in rules if r.recipe is not None] == ["line_offset"]
    ours = ink_mask(render(outputs, EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.05


def test_wide_map_unit_pattern_strokes_are_cut_at_the_edge(plugin, tmp_path):
    """Csíkozás: 20 m line markers with a 7 m stroke (and a data-defined
    colour): the stroke is exported as polygons clipped to the shape."""
    from qgis.core import QgsPointPatternFillSymbolLayer, QgsProperty, QgsSymbolLayer
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -20 100, -97 71, -97 -83))"],
                   str(tmp_path / "wide.gpkg"))
    marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Line, 20)
    marker.setSizeUnit(Qgis.RenderUnit.MapUnits)
    marker.setStrokeWidth(7)
    marker.setStrokeWidthUnit(Qgis.RenderUnit.MapUnits)
    marker.setStrokeColor(QColor("black"))
    marker.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyStrokeColor,
                                  QgsProperty.fromExpression("'0,0,0,255'"))
    pattern = QgsPointPatternFillSymbolLayer()
    pattern.setSubSymbol(QgsMarkerSymbol([marker]))
    for name, value in (("DistanceX", 15), ("DistanceY", 25)):
        getattr(pattern, f"set{name}")(value)
        getattr(pattern, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    pattern.setClipMode(Qgis.MarkerClipMode.Shape)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([pattern])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, diags = _export(plugin, layer, tmp_path)
    grids = [(o, r) for o, r in zip(outputs, rules) if r.recipe is not None]
    assert grids and all(o.geometryType() == Qgis.GeometryType.Polygon for o, _ in grids)
    assert not diags.by_code("Q2VT_RULE_OUTPUT_EMPTY")
    ours = ink_mask(render([o for o, _ in grids], EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.05


def test_generator_marker_text_is_evaluated_per_generated_part(plugin, tmp_path):
    """Polygon méretezés: a font marker whose character is
    length(geometry_n($geometry, @geometry_part_num)) on segments_to_lines():
    QGIS writes every segment's length; the source-feature value was NULL."""
    from qgis.core import (QgsFontMarkerSymbolLayer, QgsGeometryGeneratorSymbolLayer,
                           QgsProperty, QgsSymbolLayer)
    layer = _layer("Polygon", ["POLYGON((-90 -80, 50 -80, 50 60, -90 -80))"],
                   str(tmp_path / "dim.gpkg"))
    font = QgsFontMarkerSymbolLayer("DejaVu Sans", "A", 4)
    font.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyCharacter, QgsProperty.fromExpression(
        "format_number(length(geometry_n($geometry, @geometry_part_num)), 1)"))
    markers = QgsMarkerLineSymbolLayer(True, 3)
    markers.setPlacements(Qgis.MarkerLinePlacement.CentralPoint)
    markers.setSubSymbol(QgsMarkerSymbol([font]))
    generator = QgsGeometryGeneratorSymbolLayer.create(
        {"geometryModifier": "segments_to_lines($geometry)", "SymbolType": "Line"})
    generator.setSubSymbol(QgsLineSymbol([markers]))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([generator])))
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    texts = set()
    for output, rule in zip(outputs, rules):
        names = [f.name() for f in output.fields() if f.name().startswith("q2vt_property_char")]
        texts |= {feature[name] for feature in output.getFeatures() for name in names}
    assert texts == {"140.0", "198.0"}  # two 140 m legs and the hypotenuse


def test_zero_length_dash_is_merged_into_the_gap(plugin, tmp_path):
    """Felszín alatti vízbázis védőidom: "6;4;6;4;6;4;6;4;0;20" leaves a gap
    for the text markers; the zero dash rejected the whole pattern, and
    MapLibre's dashes (restarting at tile edges) ran over the text. Qt draws
    nothing for the zero dash."""
    layer = _layer("LineString", LINES, str(tmp_path / "zd.gpkg"))
    line = QgsSimpleLineSymbolLayer(QColor("black"), 3.0)
    line.setWidthUnit(Qgis.RenderUnit.MapUnits)
    line.setUseCustomDashPattern(True)
    line.setCustomDashVector([6, 4, 6, 4, 0, 20])
    line.setCustomDashPatternUnit(Qgis.RenderUnit.MapUnits)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([line])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    dashed = [(o, r) for o, r in zip(outputs, rules) if r.recipe is not None]
    assert dashed and dashed[0][1].recipe.param("pattern") == (6.0, 4.0, 6.0, 24.0)
    ours = ink_mask(render([o for o, _ in dashed], EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.05


def test_marker_line_offset_wider_than_a_polygon_is_exported(plugin, tmp_path):
    """Crayon: an inward outline offset wider than a small polygon collapses
    its ring. QGIS draws no markers there; the export failed the dataset
    ("Cannot convert to geometry") and lost the markers of every polygon."""
    layer = _layer("Polygon", ["POLYGON((-100 -100, 60 -100, 60 60, -100 60, -100 -100))",
                               "POLYGON((80 80, 90 80, 90 90, 80 90, 80 80))"],
                   str(tmp_path / "co.gpkg"))
    ml = QgsMarkerLineSymbolLayer(True, 20)
    ml.setIntervalUnit(Qgis.RenderUnit.MapUnits)
    ml.setOffset(12)
    ml.setOffsetUnit(Qgis.RenderUnit.MapUnits)
    ml.setSubSymbol(_marker(Qgis.MarkerShape.Triangle, 8))
    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, ml)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    reference = ink_mask(render([layer], EXTENT))
    outputs, rules, diags = _export(plugin, layer, tmp_path)
    assert not diags.by_code("Q2VT_RULE_EXPORT_FAILED")
    assert any(r.recipe is not None and r.recipe.kind == "marker_points" for r in rules)
    assert mask_difference(reference, ink_mask(render(outputs, EXTENT))) < 0.05


@pytest.mark.parametrize("dashed", [False, True])
def test_line_offset_wider_than_a_polygon_is_exported(plugin, tmp_path, dashed):
    """Outline offsets (plain and map-unit dashes) that collapse a small
    polygon's ring draw nothing there and keep the other polygons."""
    layer = _layer("Polygon", ["POLYGON((-100 -100, 60 -100, 60 60, -100 60, -100 -100))",
                               "POLYGON((80 80, 90 80, 90 90, 80 90, 80 80))"],
                   str(tmp_path / "lo.gpkg"))
    line = QgsSimpleLineSymbolLayer(QColor("black"), 3)
    line.setWidthUnit(Qgis.RenderUnit.MapUnits)
    line.setOffset(12)
    line.setOffsetUnit(Qgis.RenderUnit.MapUnits)
    if dashed:
        line.setUseCustomDashPattern(True)
        line.setCustomDashVector([10, 6])
        line.setCustomDashPatternUnit(Qgis.RenderUnit.MapUnits)
    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, line)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    reference = ink_mask(render([layer], EXTENT))
    outputs, rules, diags = _export(plugin, layer, tmp_path)
    assert not diags.by_code("Q2VT_RULE_EXPORT_FAILED")
    lines = [o for o, r in zip(outputs, rules) if r.recipe is not None]
    assert len(lines) == 1
    for output in lines:  # the styler draws the outline as lines
        output.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol(
            [rules[-1].rule.symbol().symbolLayer(0).clone()])))
    assert mask_difference(reference, ink_mask(render(lines, EXTENT))) < 0.05


@pytest.mark.parametrize("repeat", [0, 30])
def test_labels_of_self_crossing_and_multipart_lines_are_kept(plugin, tmp_path, repeat):
    """Méretvonal felirat on real lines: a label not drawn per part went
    through dissolve + keepnbiggestparts, which nodes a self-crossing line into
    pieces and drops every multi-part feature, with its label. QGIS labels
    each feature once, on its longest part."""
    from qgis.core import QgsPalLayerSettings, QgsVectorLayerSimpleLabeling
    layer = _layer("MultiLineString", [
        "MULTILINESTRING((-100 -100, 0 0, -100 0, 0 -100))",              # crosses itself
        "MULTILINESTRING((20 20, 30 20),(20 40, 110 40, 110 100))",        # longest part 2nd
        "MULTILINESTRING((-100 50, -40 50))"], str(tmp_path / "ml.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({"color": "black"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "id"
    settings.placement = Qgis.LabelPlacement.Line
    settings.repeatDistance = repeat
    settings.labelPerPart = False
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener
    from q2vt_plugin.src.core.rules_exporter import RulesExporter
    from fidelity.diagnostics import DiagnosticCollector
    reset_project(layer)
    diags = DiagnosticCollector()
    rules = RulesFlattener(0, 22, str(tmp_path), QgsProcessingFeedback(), diags).flatten_all_rules()
    (tmp_path / "utils").mkdir()
    outputs, rules = RulesExporter(rules, EXTENT, 0, 22, str(tmp_path / "utils"), 0,
                                   QgsProcessingFeedback(), diagnostics=diags).export()
    assert not diags.by_code("Q2VT_RULE_OUTPUT_EMPTY")
    by_name = {o.name(): o for o in outputs}
    label = [by_name[r.output_dataset] for r in rules if r.get_attr("t") == 1][0]
    geometries = {int(f["q2vt_orig_id"]): f.geometry() for f in label.getFeatures()}
    assert len(geometries) == 3 and label.featureCount() == 3
    if repeat == 0:  # drawn once, at the middle of the longest part
        point = geometries[2].asPoint()
        assert (round(point.x()), round(point.y())) == (95, 40)
    else:  # along the longest part, the crossing line kept whole
        assert geometries[2].length() == pytest.approx(150, abs=0.5)
        assert geometries[1].constGet().numPoints() == 4


def test_replaced_symbol_layer_leaves_no_dangling_wrapper(plugin):
    """changeSymbolLayer() deleted a layer the flattener still referenced; SIP
    then returned that stale wrapper for a new object at the same address
    (QGIS 3.44 / Windows: "'QgsFillSymbol' object has no attribute
    'sizeUnit'"). The replaced layer now belongs to Python and stays valid
    as long as it is referenced."""
    from qgis.PyQt import sip
    from q2vt_plugin.src.core.materializer import replace_symbol_layer
    symbol = QgsLineSymbol([QgsHashedLineSymbolLayer()])
    held = symbol.symbolLayer(0)
    replace_symbol_layer(symbol, QgsMarkerLineSymbolLayer())
    assert symbol.symbolLayer(0).layerType() == "MarkerLine"
    assert sip.ispyowned(held) and not sip.isdeleted(held)
    assert held.layerType() == "HashLine"
