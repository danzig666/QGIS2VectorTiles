"""Materialized components render like the original QGIS symbol.

Each test renders the original layer with QGIS and the exported dataset(s)
with the converted symbol(s), also with QGIS, and compares inked pixels.
"""

import pytest
from qgis.core import (Qgis, QgsFeature, QgsField, QgsFillSymbol, QgsGeometry,
                       QgsHashedLineSymbolLayer, QgsLinePatternFillSymbolLayer, QgsLineSymbol,
                       QgsMarkerLineSymbolLayer, QgsMarkerSymbol, QgsProcessingFeedback,
                       QgsRectangle, QgsSimpleMarkerSymbolLayer, QgsSingleSymbolRenderer,
                       QgsVectorLayer)
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
    assert rules[0].recipe.kind == "grid_points"
    assert not diags.by_code("Q2VT_PATTERN_APPROXIMATE")
    ours = ink_mask(render(outputs, EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.05
