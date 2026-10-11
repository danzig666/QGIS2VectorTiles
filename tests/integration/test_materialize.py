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
from qgis.PyQt.QtCore import QPointF, QVariant, Qt
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


def _hash_line(placements):
    hl = QgsHashedLineSymbolLayer()
    hl.setPlacements(placements)
    hl.setHashLength(14)
    hl.setHashLengthUnit(Qgis.RenderUnit.Pixels)
    hl.subSymbol().symbolLayer(0).setColor(QColor("black"))
    hl.subSymbol().symbolLayer(0).setWidth(2)
    hl.subSymbol().symbolLayer(0).setWidthUnit(Qgis.RenderUnit.Pixels)
    return hl


def test_hash_line_keeps_a_data_defined_hash_angle_and_length(plugin, tmp_path):
    """Hash angle and length read from a field were replaced by the static
    values when the hashed line became a marker line."""
    from qgis.core import QgsProperty, QgsSymbolLayer
    layer = _layer("LineString", LINES, str(tmp_path / "hd.gpkg"))
    hl = _hash_line(Qgis.MarkerLinePlacement.Interval)
    hl.setInterval(25)
    hl.setIntervalUnit(Qgis.RenderUnit.MapUnits)
    hl.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyLineAngle,
                              QgsProperty.fromExpression('"id" * 20 + 40'))
    hl.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyLineDistance,
                              QgsProperty.fromExpression('"id" * 6 + 22'))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([hl])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, diags = _export(plugin, layer, tmp_path)
    exact = [(o, r) for o, r in zip(outputs, rules) if r.recipe is not None]
    assert exact and all(r.recipe.kind == "marker_points" for _, r in exact)
    ours = ink_mask(render([o for o, _ in exact], EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.1
    # The browser scales the hash image with its length, stroke included.
    assert any("Data-defined hash length" in d.message
               for d in diags.by_code("Q2VT_MARKER_PLACEMENT_APPROX"))


def test_hash_line_keeps_a_data_defined_interval(plugin, tmp_path):
    """A data-defined interval on a hashed line was placed exactly at the
    static interval, without a report; it now takes the marker line's path
    for data-defined intervals (browser spacing, reported)."""
    from qgis.core import QgsProperty, QgsSymbolLayer
    layer = _layer("LineString", LINES, str(tmp_path / "hi.gpkg"))
    hl = _hash_line(Qgis.MarkerLinePlacement.Interval)
    hl.setInterval(3)
    hl.setIntervalUnit(Qgis.RenderUnit.MapUnits)
    hl.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyInterval,
                              QgsProperty.fromExpression('"id" + 7'))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([hl])))
    _, rules, diags = _export(plugin, layer, tmp_path)
    assert not [r for r in rules if r.recipe is not None and r.recipe.kind == "marker_points"]
    assert any("Data-defined interval" in d.message for d in diags.by_code("Q2VT_MARKER_PLACEMENT_APPROX"))
    converted = rules[0].rule.symbol().symbolLayer(0)
    assert converted.layerType() == "MarkerLine"
    assert converted.dataDefinedProperties().isActive(QgsSymbolLayer.Property.PropertyInterval)


@pytest.mark.parametrize("ring_filter", [1, 2])
def test_hash_line_vertices_keep_the_ring_filter(plugin, tmp_path, ring_filter):
    """Vertex hashes on the exterior ring only (or the holes only) were
    drawn on every ring."""
    from qgis.core import QgsLineSymbolLayer
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83),"
                               "(-40 -40, 10 -40, 10 10, -40 10, -40 -40))"],
                   str(tmp_path / "hr.gpkg"))
    hl = _hash_line(Qgis.MarkerLinePlacement.Vertex)
    hl.setRingFilter(QgsLineSymbolLayer.RenderRingFilter(ring_filter))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([hl])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    exact = [(o, r) for o, r in zip(outputs, rules) if r.recipe is not None]
    assert exact and all(r.recipe.param("ring_filter") == ring_filter for _, r in exact)
    ours = ink_mask(render([o for o, _ in exact], EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.1


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


def _export_zooms(plugin, layer, tmp_path, low, high, extent):
    """Flatten and export ``layer`` for zooms ``low``-``high`` within ``extent``."""
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener
    from q2vt_plugin.src.core.rules_exporter import RulesExporter
    from fidelity.diagnostics import DiagnosticCollector
    reset_project(layer)
    diags = DiagnosticCollector()
    rules = RulesFlattener(low, high, str(tmp_path), QgsProcessingFeedback(), diags).flatten_all_rules()
    utils = tmp_path / f"utils{low}_{high}"
    utils.mkdir()
    layers, rules = RulesExporter(rules, extent, low, high, str(utils), 0, QgsProcessingFeedback(),
                                  diagnostics=diags).export()
    by_name = {l.name(): l for l in layers}
    return [by_name[rule.output_dataset] for rule in rules], rules, diags


def _map_unit_hatch(angle, distance):
    lp = QgsLinePatternFillSymbolLayer()
    lp.setLineAngle(angle)
    lp.setDistance(distance)
    lp.setDistanceUnit(Qgis.RenderUnit.MapUnits)
    return QgsFillSymbol([lp])


@pytest.mark.parametrize("angle", [135, 120])
def test_hatch_line_touching_a_corner_keeps_the_hatch(plugin, tmp_path, angle):
    """At 90-180 degrees the first hatch line passes through the bounding box
    corner the hatch is anchored at: on a rectangle it touches the polygon
    there, the clip added a point, and the mixed collection could not be
    written to the line dataset: the rectangle lost its whole hatch."""
    x, y = 2110000.0, 6030000.0
    wkts = [f"POLYGON(({x0} {y}, {x0 + w} {y}, {x0 + w} {y + h}, {x0} {y + h}, {x0} {y}))"
            for x0, w, h in ((x, 300, 200), (x + 1000, 301, 204), (x + 2000, 300, 200))]
    layer = _layer("Polygon", wkts, str(tmp_path / "corner.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(_map_unit_hatch(angle, 9)))
    outputs, rules, diags = _export_zooms(plugin, layer, tmp_path, 0, 16,
                                          QgsRectangle(x - 100, y - 100, x + 2400, y + 300))
    assert [r.recipe.kind for r in rules] == ["hatch_lines"]
    hatch = {}  # length per rectangle (1000 m apart)
    for feature in outputs[0].getFeatures():
        assert feature.geometry().type() == Qgis.GeometryType.Line
        index = round((feature.geometry().boundingBox().xMinimum() - x) / 1000)
        hatch[index] = hatch.get(index, 0.0) + feature.geometry().length()
    assert sorted(hatch) == [0, 1, 2]
    for index, (w, h) in enumerate(((300, 200), (301, 204), (300, 200))):
        assert hatch[index] == pytest.approx(w * h / 9, rel=0.03)
    assert not diags.by_code("Q2VT_RULE_OUTPUT_EMPTY")


@pytest.mark.parametrize("angle", [315, -45])
def test_hatch_first_chunk_touching_a_corner_keeps_the_hatch(plugin, tmp_path, angle):
    """At 270-360 degrees the line through the anchor corner is the first one
    clipped: when its chunk of lines only touched the polygon there, the
    collected hatch took that point's type and lost every line."""
    wkts = [f"POLYGON(({x} {y}, {x + w} {y}, {x + w} {y + h}, {x} {y + h}, {x} {y}))"
            for x, y, w, h in ((2042854, 6077878, 32, 1899), (2044854, 6077878, 19, 1865))]
    layer = _layer("Polygon", wkts, str(tmp_path / "first.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(_map_unit_hatch(angle, 9)))
    outputs, rules, diags = _export_zooms(plugin, layer, tmp_path, 0, 16,
                                          QgsRectangle(2042000, 6077000, 2046000, 6080000))
    assert [r.recipe.kind for r in rules] == ["hatch_lines"]
    hatch = {}
    for feature in outputs[0].getFeatures():
        geometry = feature.geometry()
        assert geometry.type() == Qgis.GeometryType.Line
        assert not any(part.isEmpty() for part in geometry.constParts())
        index = round((geometry.boundingBox().xMinimum() - 2042854) / 2000)
        hatch[index] = hatch.get(index, 0.0) + geometry.length()
    for index, (w, h) in enumerate(((32, 1899), (19, 1865))):
        assert hatch.get(index, 0.0) == pytest.approx(w * h / 9, rel=0.03)
    assert not diags.by_code("Q2VT_RULE_OUTPUT_EMPTY")


def test_hatch_of_a_large_polygon_is_kept(plugin, tmp_path):
    """A polygon needing more than 20,000 hatch lines got no hatch at all,
    unreported (the per-feature cap returned NULL)."""
    x, y = 2110000.0, 6030000.0
    side = 75000.0  # 5 m spacing: about 21,000 lines
    layer = _layer("Polygon", [f"POLYGON(({x} {y}, {x + side} {y}, {x + side} {y + side}, "
                               f"{x} {y + side}, {x} {y}))"], str(tmp_path / "big.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(_map_unit_hatch(45, 5)))
    view = QgsRectangle(x + 1000, y + 1000, x + 1500, y + 1500)
    outputs, rules, diags = _export_zooms(plugin, layer, tmp_path, 0, 16, view)
    assert [r.recipe.kind for r in rules] == ["hatch_lines"]
    hatch = QgsGeometry.unaryUnion([f.geometry() for f in outputs[0].getFeatures()])
    inside = hatch.intersection(QgsGeometry.fromRect(view))
    # 500 x 500 m at 5 m spacing: 100 diagonals' worth of lines
    assert inside.length() == pytest.approx(500 * 500 / 5, rel=0.02)
    assert not diags.by_code("Q2VT_PATTERN_BUDGET")


def test_hatch_over_the_budget_is_a_texture(plugin, tmp_path, monkeypatch):
    """Hatch lines past the output budget: drawn as a texture and reported,
    never dropped."""
    from q2vt_plugin.src.core import materializer as materializer_module
    monkeypatch.setattr(materializer_module.SymbolMaterializer, "MAX_PATTERN_ELEMENTS", 1000)
    layer = _layer("Polygon", ["POLYGON((-100 -100, 100 -100, 100 100, -100 100, -100 -100))"],
                   str(tmp_path / "hb.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(_map_unit_hatch(30, 0.2)))  # about 1,400 lines
    _, rules, diags = _export(plugin, layer, tmp_path)
    assert not any(r.recipe for r in rules)
    assert diags.by_code("Q2VT_PATTERN_BUDGET")


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
    # Up to the web map's last zoom (22): scaled with the map, the markers'
    # hairline stroke would grow with it.
    assert pattern[:2] == ["step", ["zoom"]] and pattern[3::2] == list(range(15, 23))
    cells = [exporter.pattern_images[name].img_1x for name in pattern[2::2]]
    assert all(cell.getbbox() is not None for cell in cells)
    # Map-unit textures keep MapLibre's tile-zoom scaling (no screen flag),
    # scaled with the map above the last one; they start at each feature's
    # anchor ("Align pattern to: Feature").
    assert exporter.style["layers"][0]["metadata"] == {exporter.PATTERN_ANCHOR_FLAG: "feature",
                                                       exporter.PATTERN_ZOOM_FLAG: 22}


def test_map_unit_pattern_textures_reach_the_last_zooms_of_a_map_from_zoom_0(plugin, tmp_path):
    """A map exported from zoom 0 (the Publish window's default): the
    per-zoom textures are capped at 12; they were counted from zoom 0, so
    zoom 11's texture - a sub-pixel tint of nothing - was drawn at every
    zoom above it and the fill vanished. The highest zooms up to tile
    zoom + 3 get them, and the zooms of the web map above (the markers'
    hairline stroke would grow with the map)."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83))"],
                   str(tmp_path / "pp.gpkg"))
    pp = QgsPointPatternFillSymbolLayer()
    marker = _marker(Qgis.MarkerShape.Square, 3)
    marker.symbolLayer(0).setSizeUnit(Qgis.RenderUnit.MapUnits)
    pp.setSubSymbol(marker)
    for name in ("DistanceX", "DistanceY"):
        getattr(pp, f"set{name}")(5)
        getattr(pp, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([pp])))
    reset_project(layer)
    from q2vt_plugin.src.core import maplibre_converter as mc
    from fidelity.diagnostics import DiagnosticCollector
    exporter = mc.QgisMapLibreStyleExporter.__new__(mc.QgisMapLibreStyleExporter)
    exporter.pattern_images, exporter.marker_symbols, exporter.marker_counter = {}, {}, 0
    exporter.profile = mc.ExportProfile()
    exporter.context = mc.ConversionContext(DiagnosticCollector())
    exporter.style, exporter.maxzoom = {"layers": []}, 16
    mc.PropertyExtractor.context = exporter.context
    exporter.context.reference_zoom = 0
    exporter._convert_symbol(layer.renderer().symbol(), "s", "src", "q2vt", 0, 24)
    pattern = exporter.style["layers"][0]["paint"]["fill-pattern"]
    zooms = pattern[3::2]
    assert len(pattern[2::2]) == exporter.MAX_PATTERN_ZOOMS + 4  # 19-22 on top
    assert zooms == list(range(8, 23))  # up to the view's zoom 22, the first below
    cell = exporter.pattern_images[pattern[-1]].img_1x  # zoom 22: squares 3 m in 5 m cells
    assert cell.getbbox() is not None and cell.size[0] == pytest.approx(2 * 5 / 0.0373, abs=3)


@pytest.mark.parametrize("stroke_unit", [Qgis.RenderUnit.MapUnits, Qgis.RenderUnit.Millimeters])
def test_coarse_map_unit_pattern_textures_stop_before_outgrowing_the_sprite(plugin, tmp_path,
                                                                            stroke_unit):
    """A tiling map-unit pattern (60 m squares 60 m apart) exported from
    zoom 0 to 16: its cell doubles with every zoom, and the overzoom
    textures up to zoom 22 made the sprite renderer give up (a cell
    thousands of px wide), failing the export. The overzoom textures stop
    under MAX_OVERZOOM_CELL_PX; the last one serves the zooms above. All
    in map units, the textures stop at max zoom + 2 and the patched MapLibre
    scales the last one with the map; with a millimetre stroke (which would
    grow with it) they go on while they fit."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    layer = _layer("Polygon", ["POLYGON((-500 -500, 500 -500, 500 500, -500 500, -500 -500))"],
                   str(tmp_path / "tiles.gpkg"))
    pp = QgsPointPatternFillSymbolLayer()
    marker = _marker(Qgis.MarkerShape.Square, 60)
    marker.symbolLayer(0).setSizeUnit(Qgis.RenderUnit.MapUnits)
    marker.symbolLayer(0).setStrokeWidth(3 if stroke_unit == Qgis.RenderUnit.MapUnits else 0.5)
    marker.symbolLayer(0).setStrokeWidthUnit(stroke_unit)
    pp.setSubSymbol(marker)
    for name in ("DistanceX", "DistanceY"):
        getattr(pp, f"set{name}")(60)
        getattr(pp, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([pp])))
    reset_project(layer)
    from q2vt_plugin.src.core import maplibre_converter as mc
    from fidelity.diagnostics import DiagnosticCollector
    exporter = mc.QgisMapLibreStyleExporter.__new__(mc.QgisMapLibreStyleExporter)
    exporter.pattern_images, exporter.marker_symbols, exporter.marker_counter = {}, {}, 0
    exporter.profile = mc.ExportProfile()
    exporter.context = mc.ConversionContext(DiagnosticCollector())
    exporter.style, exporter.maxzoom = {"layers": []}, 16
    mc.PropertyExtractor.context = exporter.context
    exporter.context.reference_zoom = 0
    exporter._convert_symbol(layer.renderer().symbol(), "s", "src", "q2vt", 0, 24)
    pattern = exporter.style["layers"][0]["paint"]["fill-pattern"]
    zooms = pattern[3::2]
    # The exported zooms' textures as before (up to maxzoom + 2), then the
    # overzoom ones while they fit.
    assert zooms[:11] == list(range(8, 19))
    if stroke_unit == Qgis.RenderUnit.MapUnits:
        assert zooms[-1] == 18
        assert exporter.style["layers"][0]["metadata"][exporter.PATTERN_ZOOM_FLAG] == 18
    else:
        assert 18 < zooms[-1] < 22
    cells = [exporter.pattern_images[name].img_2x for name in pattern[2::2]]
    assert max(cells[-1].size) <= exporter.MAX_OVERZOOM_CELL_PX
    assert set(exporter.pattern_images) == set(pattern[2::2])  # none left over


def test_screen_unit_pattern_fill_is_drawn_at_true_size(plugin):
    """Patterns sized only in screen units are flagged for the patched
    MapLibre (drawn at the real zoom) and rendered at their true size, not
    at the 1/sqrt(2) compromise for stock tile-zoom scaling."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    from q2vt_plugin.src.core import maplibre_converter as mc
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
    assert layer_def["metadata"] == {exporter.SCREEN_PATTERN_FLAG: True,
                                     exporter.PATTERN_ANCHOR_FLAG: "feature"}
    cell = exporter.pattern_images[layer_def["paint"]["fill-pattern"]].img_1x
    px = 4 * 96 / 25.4
    # QGIS's texture brush: two spacings, truncated to whole pixels.
    assert cell.size == (int(2 * px), int(2 * px))
    assert exporter._screen_scale() == exporter.TEXTURE_SCREEN_SCALE  # reset afterwards


def _style_exporter(mc, maxzoom=16, reference_zoom=0, **profile):
    """A style converter for one symbol, as for a map exported from zoom 0
    (the Publish window's default) to ``maxzoom``."""
    from fidelity.diagnostics import DiagnosticCollector
    exporter = mc.QgisMapLibreStyleExporter.__new__(mc.QgisMapLibreStyleExporter)
    exporter.pattern_images, exporter.marker_symbols, exporter.marker_counter = {}, {}, 0
    exporter.profile = mc.ExportProfile(**profile)
    exporter.context = mc.ConversionContext(DiagnosticCollector())
    exporter.style, exporter.maxzoom = {"layers": []}, maxzoom
    mc.PropertyExtractor.context = exporter.context
    exporter.context.reference_zoom = reference_zoom
    return exporter


def _hatch(distance, distance_unit, width, width_unit, viewport):
    lp = QgsLinePatternFillSymbolLayer()
    lp.setLineAngle(45)
    lp.setDistance(distance)
    lp.setDistanceUnit(distance_unit)
    line = lp.subSymbol().symbolLayer(0)
    line.setColor(QColor("red"))
    line.setWidth(width)
    line.setWidthUnit(width_unit)
    if viewport:
        lp.setCoordinateReference(Qgis.SymbolCoordinateReference.Viewport)
    return lp


def _map_marker_point_pattern():
    """Markers 1.5 m wide, 4 mm apart; the marker keeps its mm stroke
    width, so its layer mixes units (outputUnit() is Unknown)."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    pp = QgsPointPatternFillSymbolLayer()
    marker = _marker(Qgis.MarkerShape.Circle, 1.5)
    marker.symbolLayer(0).setSizeUnit(Qgis.RenderUnit.MapUnits)
    marker.symbolLayer(0).setStrokeWidthUnit(Qgis.RenderUnit.Millimeters)
    pp.setSubSymbol(marker)
    for name in ("DistanceX", "DistanceY"):
        getattr(pp, f"set{name}")(4)
        getattr(pp, f"set{name}Unit")(Qgis.RenderUnit.Millimeters)
    return pp


MU, MM = Qgis.RenderUnit.MapUnits, Qgis.RenderUnit.Millimeters


@pytest.mark.parametrize("make", [
    lambda: _hatch(3, MU, 0.4, MM, True),  # viewport hatch, map-unit spacing
    lambda: _hatch(3, MM, 0.5, MU, False),  # mm spacing, map-unit line width
    lambda: _hatch(3, MM, 0.5, MU, True),
    _map_marker_point_pattern,  # map-unit markers in a mm grid
], ids=["viewport-hatch", "hatch-map-width", "viewport-hatch-map-width", "point-map-marker"])
def test_patterns_with_any_map_unit_part_get_a_texture_per_zoom(plugin, make):
    """Map-unit lengths the old check missed (a hatch's distanceUnit, a
    sub-symbol layer mixing units): the pattern became one screen texture
    with its map-unit parts drawn at the style's first zoom, 0 from the
    Publish window - map-unit markers 2e-5 px (an empty texture), a viewport
    hatch a fixed cropped preview, a hatch width a constant hairline.
    A map-unit spacing scales with the map (and above its last texture);
    a screen-unit spacing stays exact, only the marker or line width steps
    per zoom (drawn for the middle of the zoom)."""
    from q2vt_plugin.src.core import maplibre_converter as mc
    exporter = _style_exporter(mc)
    layer = make()
    kind = layer.layerType()
    screen_spacing = layer.distanceUnit() == MM if kind == "LinePatternFill" else True
    assert exporter._pattern_uses_map_units(layer)
    symbol = QgsFillSymbol([layer])
    exporter._convert_symbol(symbol, "s", "src", "q2vt", 0, 24)
    layer_def = exporter.style["layers"][0]
    pattern = layer_def["paint"]["fill-pattern"]
    # Up to the web map's last zoom: scaled with the map, their mm line
    # width or map-unit marker in a mm grid would grow with it.
    assert pattern[:2] == ["step", ["zoom"]] and pattern[-2] == 22
    metadata = layer_def.get("metadata", {})
    if screen_spacing:
        assert metadata.get(exporter.SCREEN_PATTERN_FLAG) is True
        assert exporter.PATTERN_ZOOM_FLAG not in metadata
    else:
        assert exporter.SCREEN_PATTERN_FLAG not in metadata
        assert metadata[exporter.PATTERN_ZOOM_FLAG] == 22
    assert not exporter.context.diagnostics.by_code("Q2VT_PATTERN_APPROXIMATE")
    names = dict(zip(pattern[3::2], pattern[4::2]))
    alpha = exporter.pattern_images[names[18]].img_1x.getchannel("A")
    assert alpha.getextrema()[1] > 200
    if kind == "PointPatternFill":
        # Dots 1.5 m wide at zoom 18.5 (the middle of the texture's zoom) in
        # an exact 4 mm grid.
        coverage = sum(1 for a in alpha.getdata() if a > 128) / (alpha.width * alpha.height)
        dot = 1.5 * 512 * 2 ** 18.5 / 40075016.69
        grid = 4 * 96 / 25.4
        assert coverage == pytest.approx(math.pi / 4 * dot ** 2 / grid ** 2, rel=0.25)
        assert alpha.width == pytest.approx(grid, abs=1)


def _map_unit_point_pattern(spacing, size, stroke=False):
    """Squares and spacing in map units; without a (hairline) stroke the
    whole pattern scales with the map."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    pp = QgsPointPatternFillSymbolLayer()
    marker = _marker(Qgis.MarkerShape.Square, size)
    marker.symbolLayer(0).setSizeUnit(Qgis.RenderUnit.MapUnits)
    if not stroke:
        marker.symbolLayer(0).setStrokeStyle(0)  # Qt.NoPen
    pp.setSubSymbol(marker)
    for name in ("DistanceX", "DistanceY"):
        getattr(pp, f"set{name}")(spacing)
        getattr(pp, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    return QgsFillSymbol([pp])


def test_map_unit_pattern_textures_stop_at_the_size_budgets(plugin):
    """Map-unit textures above tile zoom + 3 (the last one scaled with the
    map would do, only less sharp) stop at half MAX_PATTERN_TEXTURE_PX,
    the others at MAX_PATTERN_TEXTURE_PX: the one below serves the zooms
    above, with the layer's PATTERN_ZOOM_FLAG."""
    from q2vt_plugin.src.core import maplibre_converter as mc
    symbol = _map_unit_point_pattern(40, 10)
    exporter = _style_exporter(mc, view_max_zoom=22.0)
    exporter._convert_symbol(symbol, "s", "src", "q2vt", 0, 24)
    assert exporter.style["layers"][0]["paint"]["fill-pattern"][-2] == 18
    assert exporter.style["layers"][0]["metadata"][exporter.PATTERN_ZOOM_FLAG] == 18
    exporter = _style_exporter(mc, maxzoom=18, view_max_zoom=22.0)
    exporter._convert_symbol(symbol, "s", "src", "q2vt", 0, 24)
    layer_def = exporter.style["layers"][0]
    pattern = layer_def["paint"]["fill-pattern"]
    # Two 40 m cells: 268 px at zoom 19, 536 px at zoom 20.
    assert pattern[-2] == 19
    assert layer_def["metadata"][exporter.PATTERN_ZOOM_FLAG] == 19
    assert exporter.MAX_PATTERN_TEXTURE_PX <= 512  # 1024 px in the 2x sprite
    assert max(exporter.pattern_images[pattern[-1]].img_1x.size) <= exporter.MAX_PATTERN_TEXTURE_PX
    assert len(exporter.pattern_images) == len(pattern[2::2])
    # A hairline stroke would grow with the map: textures up to the view's
    # last zoom, here up to MAX_PATTERN_TEXTURE_PX.
    exporter = _style_exporter(mc, view_max_zoom=22.0)
    exporter._convert_symbol(_map_unit_point_pattern(40, 10, stroke=True), "s", "src", "q2vt", 0, 24)
    assert exporter.style["layers"][0]["paint"]["fill-pattern"][-2] == 19
    assert exporter.style["layers"][0]["metadata"][exporter.PATTERN_ZOOM_FLAG] == 19


def test_patterns_that_scale_with_the_map_are_told_apart(plugin):
    """Only patterns whose every drawn length is in map units scale with the
    map above their last texture; a mm line width, a hairline or mm marker
    stroke would grow with it."""
    from q2vt_plugin.src.core import maplibre_converter as mc
    from qgis.core import QgsRasterFillSymbolLayer, QgsSVGFillSymbolLayer
    has_screen = mc.QgisMapLibreStyleExporter._pattern_has_screen_lengths
    raster = QgsRasterFillSymbolLayer("")
    assert has_screen(raster)  # width 0: the image's pixel size
    raster.setWidth(80)
    raster.setWidthUnit(MU)
    assert not has_screen(raster)
    svg = QgsSVGFillSymbolLayer("")
    svg.setPatternWidth(20)
    svg.setPatternWidthUnit(MU)
    assert has_screen(svg)  # 0.2 mm SVG stroke
    svg.setSvgStrokeWidthUnit(MU)
    assert not has_screen(svg)
    assert has_screen(_hatch(3, MU, 0.4, MM, True))
    assert not has_screen(_hatch(3, MU, 0.4, MU, True))
    assert has_screen(_map_unit_point_pattern(40, 10, stroke=True).symbolLayer(0))
    assert not has_screen(_map_unit_point_pattern(40, 10).symbolLayer(0))


def test_map_unit_pattern_textures_of_a_style_fit_the_sprite_budget(plugin, monkeypatch):
    """Per-zoom textures of many map-unit patterns: with textures up to
    zoom 22, ten large cells made a 2x sprite of 38 Mpx, over the canvas
    limit of mobile browsers (the sprite does not load: no icon or pattern
    is drawn). Past MAX_PATTERN_SPRITE_PX each pattern keeps the textures
    it has, at least one."""
    from q2vt_plugin.src.core import maplibre_converter as mc
    exporter = _style_exporter(mc, view_max_zoom=22.0)
    for i in range(10):
        exporter._convert_symbol(_map_unit_point_pattern(60 + 5 * i, 20), f"s{i}", "src", "q2vt", 0, 24)
    area = sum(img.img_1x.size[0] * img.img_1x.size[1] for img in exporter.pattern_images.values())
    assert area <= exporter.MAX_PATTERN_SPRITE_PX
    assert 4 * exporter.MAX_PATTERN_SPRITE_PX <= 8_000_000  # 2x sprite, half the iOS limit
    patterns = [layer["paint"]["fill-pattern"] for layer in exporter.style["layers"]]
    assert all(p[-2] == 18 for p in patterns)
    # A lower budget: the later patterns stop earlier, none is lost.
    monkeypatch.setattr(mc.QgisMapLibreStyleExporter, "MAX_PATTERN_SPRITE_PX", area // 2)
    exporter = _style_exporter(mc, view_max_zoom=22.0)
    for i in range(10):
        exporter._convert_symbol(_map_unit_point_pattern(60 + 5 * i, 20), f"s{i}", "src", "q2vt", 0, 24)
    area = sum(img.img_1x.size[0] * img.img_1x.size[1] for img in exporter.pattern_images.values())
    assert area <= exporter.MAX_PATTERN_SPRITE_PX
    layers = exporter.style["layers"]
    assert len(layers) == 10 and all("fill-pattern" in layer["paint"] for layer in layers)
    last = [layer["metadata"][exporter.PATTERN_ZOOM_FLAG] for layer in layers]
    assert last[0] == 18 and last[-1] < 18


@pytest.mark.parametrize("size,zooms,stops_below", [(60, (14, 19), 22), (440, (0, 16), 19)])
def test_screen_grid_of_huge_map_unit_markers_exports(plugin, size, zooms, stops_below):
    """Squares of 60 m (or 440 m) in a 10 mm grid: their per-zoom textures
    go up to the web map's last zoom, and the marker outgrew the sprite
    renderer (4096 px) below tile zoom + 3: the whole export failed. The
    textures stop at the last marker that can be drawn (the cells are
    covered there), which serves the zooms above."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    from q2vt_plugin.src.core import maplibre_converter as mc
    pp = QgsPointPatternFillSymbolLayer()
    marker = _marker(Qgis.MarkerShape.Square, size)
    marker.symbolLayer(0).setSizeUnit(MU)
    marker.symbolLayer(0).setStrokeStyle(Qt.PenStyle.NoPen)
    pp.setSubSymbol(marker)
    for name in ("DistanceX", "DistanceY"):
        getattr(pp, f"set{name}")(10)
        getattr(pp, f"set{name}Unit")(MM)
    exporter = _style_exporter(mc, maxzoom=zooms[1], reference_zoom=zooms[0])
    exporter._convert_symbol(QgsFillSymbol([pp]), "s", "src", "q2vt", zooms[0], 24)
    layer_def = exporter.style["layers"][0]
    pattern = layer_def["paint"]["fill-pattern"]
    assert pattern[:2] == ["step", ["zoom"]] and pattern[-2] < stops_below
    assert layer_def["metadata"][exporter.SCREEN_PATTERN_FLAG] is True
    grid = 10 * 96 / 25.4
    cell = exporter.pattern_images[pattern[-1]].img_1x
    assert cell.size[0] == pytest.approx(grid, abs=1)
    assert cell.getchannel("A").getextrema() == (255, 255)  # covered by the marker
    assert any("stops growing" in d.message
               for d in exporter.context.diagnostics.by_code("Q2VT_PATTERN_MAP_UNITS"))


def test_zero_map_unit_lengths_keep_a_screen_pattern_exact(plugin):
    """A 4 mm grid of 1.5 mm squares whose (zero) displacement has map units
    as its unit: counted as a map-unit pattern by its units alone, it got a
    texture per zoom scaled with the map (its spacing 0.75x-1.33x of QGIS's
    between zooms). QGIS draws nothing of it in map units: one exact screen
    texture. Zero offsets and widths count as none too."""
    from qgis.core import (QgsPointPatternFillSymbolLayer, QgsRasterFillSymbolLayer,
                           QgsLinePatternFillSymbolLayer as LinePattern)
    from q2vt_plugin.src.core import maplibre_converter as mc
    pp = QgsPointPatternFillSymbolLayer()
    marker = _marker(Qgis.MarkerShape.Square, 1.5)
    marker.symbolLayer(0).setSizeUnit(MM)
    marker.symbolLayer(0).setOffsetUnit(MU)  # zero offset
    pp.setSubSymbol(marker)
    for name in ("DistanceX", "DistanceY"):
        getattr(pp, f"set{name}")(4)
        getattr(pp, f"set{name}Unit")(MM)
    for name in ("DisplacementX", "DisplacementY", "OffsetX", "OffsetY"):
        getattr(pp, f"set{name}Unit")(MU)
    symbol = QgsFillSymbol([pp])
    pp, marker = symbol.symbolLayer(0), pp.subSymbol()
    exporter = _style_exporter(mc)
    assert not exporter._pattern_uses_map_units(pp)
    assert not exporter._pattern_spacing_in_map_units(pp)
    exporter._convert_symbol(symbol, "s", "src", "q2vt", 0, 24)
    layer_def = exporter.style["layers"][0]
    assert isinstance(layer_def["paint"]["fill-pattern"], str)
    assert layer_def["metadata"][exporter.SCREEN_PATTERN_FLAG] is True
    assert exporter.PATTERN_ZOOM_FLAG not in layer_def["metadata"]
    # A displacement that is not zero is drawn in map units.
    pp.setDisplacementX(2)
    assert exporter._pattern_uses_map_units(pp) and exporter._pattern_spacing_in_map_units(pp)
    pp.setDisplacementX(0)
    marker.symbolLayer(0).setOffset(QPointF(0, 1))
    assert exporter._pattern_uses_map_units(pp) and not exporter._pattern_spacing_in_map_units(pp)
    # Width 0: the image's own pixel size, whatever its unit.
    raster = QgsRasterFillSymbolLayer("")
    raster.setWidthUnit(MU)
    raster.setOffsetUnit(MU)
    assert not exporter._pattern_uses_map_units(raster)
    raster.setWidth(80)
    assert exporter._pattern_uses_map_units(raster)
    # A hatch with a zero map-unit offset and a mm line; a map-unit hairline
    # (width 0) is one device pixel.
    hatch = _hatch(3, MM, 0.4, MM, False)
    assert isinstance(hatch, LinePattern)
    hatch.setOffsetUnit(MU)
    assert not exporter._pattern_uses_map_units(hatch)
    hatch.subSymbol().symbolLayer(0).setWidth(0)
    hatch.subSymbol().symbolLayer(0).setWidthUnit(MU)
    assert not exporter._pattern_uses_map_units(hatch)


@pytest.mark.parametrize("min_zoom,max_zoom,first,last", [
    (15.8525, 17.1744, 15, 17),  # a rule's 1:5000-1:2000, inside the tile zooms
    (18.1744, 19.9114, 18, 19),  # 1:1000-1:300, only on overzoomed tiles
])
def test_map_unit_texture_of_a_fractional_scale_limit_is_drawn_for_its_tile_zoom(
        plugin, min_zoom, max_zoom, first, last):
    """MapLibre draws fill-pattern in the pixels of the tile's integer zoom:
    the first texture of a rule shown from 1:5000 (zoom 15.85) was rendered
    for zoom 15.85 and drawn in zoom 15's pixels, 1.8x too large up to
    zoom 16; the last one's PATTERN_ZOOM_FLAG (18.17) was not a zoom whose
    pixels MapLibre draws in. Each texture is rendered for the integer zoom
    it is drawn in."""
    from q2vt_plugin.src.core import maplibre_converter as mc
    symbol = _map_unit_point_pattern(12, 4.8)
    exporter = _style_exporter(mc)
    exporter._convert_symbol(symbol, "s", "src", "q2vt", min_zoom, max_zoom)
    layer_def = exporter.style["layers"][0]
    pattern = layer_def["paint"]["fill-pattern"]
    assert pattern[:2] == ["step", ["zoom"]] and pattern[3::2] == list(range(first + 1, last + 1))
    assert layer_def["metadata"][exporter.PATTERN_ZOOM_FLAG] == last
    assert isinstance(layer_def["metadata"][exporter.PATTERN_ZOOM_FLAG], int)
    # 12 m cells at the first texture's integer zoom (fill-pattern starts
    # at the rule's own zoom).
    cell = exporter.pattern_images[pattern[2]].img_1x
    assert cell.size[0] == pytest.approx(12 * 512 * 2 ** first / 40075016.69, abs=1)


def test_fine_map_unit_hatch_gets_sharp_textures_above_tile_zoom_3(plugin):
    """A hatch of line markers 5 m apart with a 0.1 m stroke, all in map
    units, exported 0-16: its last texture (zoom 18, a sub-pixel stroke)
    was magnified up to the web map's zoom 22, a wide pale band where QGIS
    draws a thin line. Textures go on above tile zoom + 3 while their cells
    are small; they have a budget of their own, so the textures of the
    exported zooms of the patterns after keep theirs."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    from q2vt_plugin.src.core import maplibre_converter as mc

    def hatch(spacing):
        pp = QgsPointPatternFillSymbolLayer()
        marker = _marker(Qgis.MarkerShape.Line, 7)
        line = marker.symbolLayer(0)
        line.setSizeUnit(MU)
        line.setAngle(45)
        line.setStrokeColor(QColor("red"))
        line.setStrokeWidth(0.1)
        line.setStrokeWidthUnit(MU)
        pp.setSubSymbol(marker)
        for name in ("DistanceX", "DistanceY"):
            getattr(pp, f"set{name}")(spacing)
            getattr(pp, f"set{name}Unit")(MU)
        return QgsFillSymbol([pp])

    exporter = _style_exporter(mc, view_max_zoom=22.0)
    exporter._convert_symbol(hatch(5), "s", "src", "q2vt", 0, 24)
    layer_def = exporter.style["layers"][0]
    pattern = layer_def["paint"]["fill-pattern"]
    assert exporter.SCREEN_PATTERN_FLAG not in layer_def["metadata"]
    # 17 px cells at zoom 18, 134 px at 21; 268 px at 22 is over half
    # MAX_PATTERN_TEXTURE_PX: zoom 21's texture is scaled there.
    assert pattern[3::2] == list(range(8, 22))
    assert layer_def["metadata"][exporter.PATTERN_ZOOM_FLAG] == 21
    names = dict(zip(pattern[3::2], pattern[4::2]))
    for zoom in (19, 20, 21):
        cell = exporter.pattern_images[names[zoom]].img_1x
        assert cell.size[0] == pytest.approx(5 * 512 * 2 ** zoom / 40075016.69, abs=1)
    # The stroke: 0.7 px in zoom 18's 2x texture (pale, antialiased), which
    # was magnified 16x at zoom 22; 2.7 px in zoom 21's.
    alpha = [exporter.pattern_images[names[zoom]].img_2x.getchannel("A").getextrema()[1]
             for zoom in (18, 21)]
    assert alpha[0] < 200 < alpha[1]
    # Many such hatches: the overzoom textures stop at their own budget,
    # the exported zooms' textures of a pattern converted after are whole.
    for i in range(12):
        exporter._convert_symbol(hatch(5 + 0.2 * i), f"h{i}", "src", "q2vt", 0, 24)
    exporter._convert_symbol(_map_unit_point_pattern(60, 20), "coarse", "src", "q2vt", 0, 24)
    layers = exporter.style["layers"]
    assert exporter._overzoom_pattern_px <= exporter.MAX_OVERZOOM_PATTERN_SPRITE_PX
    assert min(layer["metadata"][exporter.PATTERN_ZOOM_FLAG] for layer in layers) == 18
    assert layers[-1]["paint"]["fill-pattern"][-2] == 18


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


def test_arrow_outline_is_a_line_on_the_arrow_rings(plugin, tmp_path):
    """QGIS strokes the arrow's fill outline centred on the arrow's edge with
    its own width: exported as a line on the arrow polygons' rings, above the
    fill (a fill-outline-color is one device pixel whatever the width)."""
    from qgis.core import QgsArrowSymbolLayer
    from q2vt_plugin.src.core import maplibre_converter as mc  # pylint: disable=import-error
    from fidelity.diagnostics import DiagnosticCollector
    layer = _layer("LineString", ["LINESTRING(-100 -80, -20 60, 20 -40, 100 60)"],
                   str(tmp_path / "arrow.gpkg"))
    arrow = QgsArrowSymbolLayer()
    for name, value in (("ArrowWidth", 10), ("ArrowStartWidth", 10), ("HeadLength", 24),
                        ("HeadThickness", 12)):
        getattr(arrow, f"set{name}")(value)
        getattr(arrow, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    # One arrow per line: repeated arrows overlap at the vertices, where QGIS
    # covers an arrow's outline with the next arrow's fill (outlines are drawn
    # above all fills, as for polygon outlines).
    arrow.setIsCurved(True)
    arrow.setIsRepeated(False)
    fill = arrow.subSymbol().symbolLayer(0)
    fill.setColor(QColor("white"))
    fill.setStrokeColor(QColor("black"))
    fill.setStrokeWidth(4)
    fill.setStrokeWidthUnit(Qgis.RenderUnit.MapUnits)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([arrow])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _ = _export(plugin, layer, tmp_path)

    assert [r.get_attr("c") for r in rules] == [2, 1]  # the outline above the fill
    assert all(r.recipe.kind == "arrow_polygons" for r in rules)
    assert rules[0].rule.symbol().symbolLayer(0).strokeStyle() == 0  # Qt.NoPen
    line = rules[1].rule.symbol()
    assert line.type() == Qgis.SymbolType.Line
    assert line.symbolLayer(0).layerType() == "SimpleLine"
    assert line.symbolLayer(0).width() == 4
    assert line.symbolLayer(0).widthUnit() == Qgis.RenderUnit.MapUnits
    assert outputs[1].geometryType() == Qgis.GeometryType.Line
    ours = ink_mask(render(outputs[::-1], EXTENT, (240, 240)))  # first layer on top
    assert mask_difference(reference, ours) < 0.01

    exporter = mc.QgisMapLibreStyleExporter.__new__(mc.QgisMapLibreStyleExporter)
    exporter.pattern_images, exporter.marker_symbols, exporter.marker_counter = {}, {}, 0
    exporter.profile = mc.ExportProfile()
    exporter.context = mc.ConversionContext(DiagnosticCollector())
    exporter.style, exporter.maxzoom = {"layers": []}, 17
    mc.PropertyExtractor.context = exporter.context
    exporter.context.reference_zoom = 14
    for index, rule in enumerate(rules):
        exporter._convert_symbol(rule.rule.symbol(), f"s{index}", "src", "q2vt", 0, 22)
    layer_defs = exporter.style["layers"]
    assert [d["type"] for d in layer_defs] == ["fill", "line"]
    assert "fill-outline-color" not in layer_defs[0]["paint"]


@pytest.mark.parametrize("unit", [Qgis.RenderUnit.MapUnits, Qgis.RenderUnit.Millimeters])
def test_arrow_fill_offset_grows_with_the_map_in_map_units(plugin, tmp_path, unit):
    """An arrow fill's offset becomes a viewport translate. In map units it
    was one pixel value taken at the style's first zoom: 0.0002 px for a
    12 m offset exported from zoom 0, so the shifted fill (a drop shadow)
    stayed under the arrow at every zoom. It doubles with every zoom now."""
    from qgis.core import QgsArrowSymbolLayer
    from q2vt_plugin.src.core import maplibre_converter as mc  # pylint: disable=import-error
    from qgis.PyQt.QtCore import QPointF
    layer = _layer("LineString", ["LINESTRING(-100 0, 100 0)"], str(tmp_path / "arrow.gpkg"))
    arrow = QgsArrowSymbolLayer()
    for name, value in (("ArrowWidth", 20), ("ArrowStartWidth", 20), ("HeadLength", 50),
                        ("HeadThickness", 30)):
        getattr(arrow, f"set{name}")(value)
        getattr(arrow, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    arrow.setIsCurved(False)
    fill = arrow.subSymbol().symbolLayer(0)
    fill.setStrokeStyle(0)
    fill.setOffset(QPointF(12, -6))
    fill.setOffsetUnit(unit)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([arrow])))
    _, rules, _ = _export(plugin, layer, tmp_path)
    assert rules[0].recipe.kind == "arrow_polygons" and rules[0].translate[:2] == (12, -6)

    exporter = _style_exporter(mc)
    layer_defs = [{"type": "fill", "paint": {}}]
    exporter._apply_translate(layer_defs, rules[0].translate)
    translate = layer_defs[0]["paint"]["fill-translate"]
    assert layer_defs[0]["paint"]["fill-translate-anchor"] == "viewport"
    if unit == Qgis.RenderUnit.Millimeters:
        assert translate == pytest.approx([12 * 96 / 25.4, -6 * 96 / 25.4])
        return
    assert translate[:3] == ["interpolate", ["exponential", 2], ["zoom"]]
    from fidelity import expressions as ex
    # Each axis as a zoom curve of its own (stops: zoom, ["literal", [x, y]]).
    axes = [translate[:3] + [item if index % 2 == 0 else item[1][axis]
                             for index, item in enumerate(translate[3:])] for axis in (0, 1)]
    for zoom in (0, 16, 19, 22):
        px = 512 * 2 ** zoom / 40075016.68557849
        value = [ex.evaluate_zoom_curve(curve, zoom) for curve in axes]
        assert value == pytest.approx([12 * px, -6 * px], rel=1e-3, abs=1e-5)


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


def test_a_rotated_map_unit_svg_fill_is_reported(plugin, tmp_path):
    """Map-unit SVG fills are a grid of SVG markers: a rotated fill turns each
    SVG but not the grid (QGIS turns the whole texture). Unreported before."""
    from qgis.core import QgsSVGFillSymbolLayer
    svg = tmp_path / "dot.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
                   '<rect x="2" y="4" width="6" height="2" fill="black"/></svg>')
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83))"],
                   str(tmp_path / "rsvg.gpkg"))
    fill = QgsSVGFillSymbolLayer(str(svg), 20, 30)
    fill.setPatternWidthUnit(Qgis.RenderUnit.MapUnits)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([fill])))
    _, _, diags = _export(plugin, layer, tmp_path)
    assert any("Rotated SVG fill" in d.message for d in diags.by_code("Q2VT_PATTERN_APPROXIMATE"))


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


@pytest.mark.parametrize("wkt", [
    "POLYGON((0 0,10 0,10 10,0 10,0 0),(3 3,7 3,7 7,3 7,3 3))",
    "POLYGON((0 0,0 10,10 10,10 0,0 0),(3 3,3 7,7 7,7 3,3 3))",
    "MULTIPOLYGON(((0 0,10 0,10 10,0 10,0 0),(3 3,7 3,7 7,3 7,3 3)))"])
def test_screen_offset_rings_have_the_interior_on_their_right(plugin, wkt):
    """Screen-unit polygon offsets stay native (MapLibre line-offset, positive
    = right of the line): exterior clockwise, holes counter-clockwise (map y
    up) put the feature's interior on the right, where QGIS offsets inward."""
    from qgis.core import QgsExpression, QgsExpressionContext, QgsFeature, QgsGeometry
    from fidelity import materialize as mat
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt(wkt))
    context = QgsExpressionContext()
    context.setFeature(feature)
    expr = QgsExpression(mat.polygon_offset_expression(
        mat.Recipe("polygon_offset", params=(("rhr", True),))))
    assert expr.evaluate(context).asWkt() == \
        "MultiLineString ((0 0, 0 10, 10 10, 10 0, 0 0),(3 3, 7 3, 7 7, 3 7, 3 3))"


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
    (0, 0, "line"), (7, 0, "line"), (7, 4, "line"), (-6, 0, "line"), (-25, 4, "line"),
    (0, 0, "polygon"), (5, -3, "polygon"), (0, 0, "holed")])
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
    longest = max(QgsGeometry.fromWkt(wkt).length() for wkt in LINES)
    for rule in placed:
        band = rule.visibility
        if band.max_zoom is None:
            continue
        middle = (band.min_zoom + band.max_zoom) / 2
        if band.max_zoom - band.min_zoom == 1.0:
            # No line is 4 mm long, even at the zoom's end: one marker a
            # line at its start, one placement for the zoom.
            assert longest < 0.004 * zoom_to_scale(band.max_zoom)
            continue
        assert band.max_zoom - band.min_zoom == pytest.approx(1 / 8)
        assert rule.recipe.param("interval") == pytest.approx(0.004 * zoom_to_scale(middle), rel=1e-6)
    # Beyond the last tile zoom, the native placement keeps the screen spacing.
    assert any(r.recipe is None and r.visibility is not None
               and r.visibility.min_zoom == top + 1 for r in rules)


def test_a_data_defined_interval_is_reported(plugin, tmp_path):
    """An interval read from a field cannot be placed per zoom: MapLibre
    spaces those markers itself. That used to happen without a report."""
    from qgis.core import QgsProperty, QgsSymbolLayer
    layer = _layer("LineString", LINES, str(tmp_path / "dd.gpkg"))
    stroke = QgsMarkerLineSymbolLayer(True, 4)
    stroke.setIntervalUnit(Qgis.RenderUnit.Millimeters)
    stroke.setSubSymbol(_marker())
    stroke.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyInterval, QgsProperty.fromExpression('"id" + 3'))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([stroke])))
    _, rules, diags = _export(plugin, layer, tmp_path)
    assert not [r for r in rules if r.recipe is not None and r.recipe.kind == "marker_points"]
    assert any("Data-defined interval" in d.message for d in diags.by_code("Q2VT_MARKER_PLACEMENT_APPROX"))


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


# Two polygons sharing an edge, the first with a hole near its side.
INSIDE_POLYGONS = ["POLYGON((-100 -70, 0 -70, 0 60, -100 60, -100 -70),"
                   "(-80 -30, -30 -30, -30 20, -80 20, -80 -30))",
                   "POLYGON((0 -70, 90 -70, 90 60, 0 60, 0 -70))"]


def _inside_line(width, unit, offset=0.0, ring_filter=0, pen=None):
    from qgis.core import QgsLineSymbolLayer
    line = QgsSimpleLineSymbolLayer(QColor(0, 0, 255, 140), width)
    line.setWidthUnit(unit)
    line.setOffset(offset)
    line.setOffsetUnit(unit)
    line.setDrawInsidePolygon(True)
    line.setRingFilter(QgsLineSymbolLayer.RenderRingFilter(ring_filter))
    if pen is not None:
        line.setPenStyle(pen)
    return line


@pytest.mark.parametrize("offset,ring_filter", [(0.0, 0), (5.0, 0), (-3.0, 0), (0.0, 1)])
def test_map_unit_line_drawn_inside_polygon_matches_qgis(plugin, tmp_path, offset, ring_filter):
    """"Draw line only inside polygon": QGIS clips the stroke to the polygon,
    so only the band inside it shows, sharp at the corners. The whole stroke
    was drawn, half of it over the neighbouring polygon."""
    layer = _layer("Polygon", INSIDE_POLYGONS, str(tmp_path / "in.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol(
        [_inside_line(16, Qgis.RenderUnit.MapUnits, offset, ring_filter)])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    assert [r.recipe.kind for r in rules] == ["inside_band"]
    for output, rule in zip(outputs, rules):
        output.setRenderer(QgsSingleSymbolRenderer(rule.rule.symbol().clone()))
    ours = ink_mask(render(outputs, EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.03


# A concave L with a hole, a part with a spike and a notch, and a sliver
# narrower than twice the offset used below.
INSIDE_SHAPES = ["POLYGON((-110 -100, -10 -100, -10 -30, -60 -30, -60 100, -110 100, -110 -100),"
                 "(-95 -85, -75 -85, -75 30, -95 30, -95 -85))",
                 "POLYGON((10 -100, 100 -100, 100 90, 80 10, 55 90, 10 90, 10 -100))",
                 "POLYGON((10 100, 100 100, 100 110, 10 110, 10 100))"]


@pytest.mark.parametrize("offset,join", [(6.0, Qt.PenJoinStyle.BevelJoin),
                                         (6.0, Qt.PenJoinStyle.RoundJoin),
                                         (-3.0, Qt.PenJoinStyle.BevelJoin)])
def test_line_drawn_inside_polygon_with_an_offset_matches_qgis(plugin, tmp_path, offset, join):
    """With an offset QGIS strokes each ring's offset ring (mitred buffer)
    with the pen's join and then clips: the join cuts the corners of an
    inward offset ring, and a part narrower than twice the offset gets no
    stroke. A band at a distance from the rings filled corners sharp and
    narrow parts whole."""
    layer = _layer("Polygon", INSIDE_SHAPES, str(tmp_path / "io.gpkg"))
    line = _inside_line(16, Qgis.RenderUnit.MapUnits, offset)
    line.setPenJoinStyle(join)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([line])))
    reference = ink_mask(render([layer], EXTENT, (480, 480)))
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    assert [r.recipe.kind for r in rules] == ["inside_band"]
    ours = ink_mask(render(outputs, EXTENT, (480, 480)))
    assert mask_difference(reference, ours) < 0.01


def test_line_drawn_inside_polygon_keeps_only_polygons(plugin):
    """Where a band piece touches the part along an edge, the intersection is
    a collection with lines, which the tiles dropped: the feature lost its
    whole band at that zoom."""
    from qgis.core import QgsExpression, QgsExpressionContext, QgsFeature, QgsGeometry, QgsWkbTypes
    from fidelity import materialize as mat
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt(
        "POLYGON((-160 -100, -20 -100, -20 -20, -90 -20, -90 90, -160 90, -160 -100),"
        "(-140 -80, -110 -80, -110 40, -140 40, -140 -80))"))
    context = QgsExpressionContext()
    context.setFeature(feature)
    width = 2.420816752525332  # a quarter of it outside the rings
    expr = QgsExpression(mat.inside_band_expression(mat.inside_band(
        -0.75 * width, 0.25 * width, 0, "bevel", "EPSG:3857")))
    result = expr.evaluate(context)
    assert QgsWkbTypes.geometryType(result.wkbType()) == Qgis.GeometryType.Polygon
    assert result.area() == pytest.approx(width / 4 * 2 * (140 + 190 + 30 + 120), rel=0.02)


def test_screen_line_drawn_inside_polygon_is_its_band_per_zoom(plugin, tmp_path):
    """A 4 mm stroke drawn only inside the polygon: its 2 mm inner band, as
    polygons per eighth of a zoom (QGIS-rendered here at one band's zoom),
    and beyond the last tiles a 2 mm line 1 mm inside the rings. Zooms
    where the whole layer is under a pixel get no (empty) band dataset."""
    from fidelity.units import EARTH_CIRCUMFERENCE
    layer = _layer("Polygon", INSIDE_POLYGONS, str(tmp_path / "is.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol(
        [_inside_line(4, Qgis.RenderUnit.Millimeters)])))
    zoom, size = 17.4375, 300  # the middle of a band
    half = EARTH_CIRCUMFERENCE / (512 * 2 ** zoom) * size / 2
    extent = QgsRectangle(-half, -half, half, half)
    reference = ink_mask(render([layer], extent, (size, size)))
    outputs, rules, _ = _export(plugin, layer, tmp_path)
    bands = [(o, r) for o, r in zip(outputs, rules) if r.recipe.kind == "inside_band"]
    top = max(r.get_attr("o") for _, r in bands)
    first = min(r.get_attr("o") for _, r in bands)
    # 190 map units: over a pixel (153 m at zoom 9) from the end of zoom 8 on.
    assert top > 17 and first == 8 and {r.get_attr("o") for _, r in bands} == set(range(first, top + 1))
    shown = [(o, r) for o, r in bands
             if r.visibility.min_zoom <= zoom < r.visibility.max_zoom]
    assert len(shown) == 1
    output, rule = shown[0]
    output.setRenderer(QgsSingleSymbolRenderer(rule.rule.symbol().clone()))
    assert mask_difference(reference, ink_mask(render([output], extent, (size, size)))) < 0.05
    over = [r for r in rules if r.recipe.kind == "polygon_offset"]
    assert len(over) == 1 and over[0].visibility.min_zoom == top + 1 and over[0].recipe.param("rhr")
    line = over[0].rule.symbol().symbolLayer(0)
    assert (line.width(), line.offset(), line.drawInsidePolygon()) == (2.0, 1.0, False)


def test_mixed_unit_line_drawn_inside_polygon_is_its_band_per_zoom(plugin, tmp_path):
    """A 4 mm stroke 5 map units inside the rings: the offset and the width
    converted each in its own unit per band. The whole stroke was drawn."""
    from fidelity.units import EARTH_CIRCUMFERENCE
    layer = _layer("Polygon", INSIDE_SHAPES, str(tmp_path / "im.gpkg"))
    line = _inside_line(4, Qgis.RenderUnit.Millimeters, 5.0)
    line.setOffsetUnit(Qgis.RenderUnit.MapUnits)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([line])))
    zoom, size = 17.0625, 360  # the middle of a band
    half = EARTH_CIRCUMFERENCE / (512 * 2 ** zoom) * size / 2
    extent = QgsRectangle(-half, -half, half, half)
    reference = ink_mask(render([layer], extent, (size, size)))
    outputs, rules, diags = _export(plugin, layer, tmp_path)
    shown = [(o, r) for o, r in zip(outputs, rules) if r.recipe.kind == "inside_band"
             and r.visibility.min_zoom <= zoom < r.visibility.max_zoom]
    assert len(shown) == 1
    output, rule = shown[0]
    output.setRenderer(QgsSingleSymbolRenderer(rule.rule.symbol().clone()))
    assert mask_difference(reference, ink_mask(render([output], extent, (size, size)))) < 0.02
    # Beyond the last tiles the stroke cannot be narrowed in one unit.
    assert any("different units" in d.message for d in diags.by_code("Q2VT_SYMBOL_APPROXIMATE"))


def test_screen_line_drawn_inside_polygon_with_an_offset_is_reported_beyond_the_tiles(plugin, tmp_path):
    """Beyond the last tiles a stroke inside the rings is a mitred line: QGIS
    cuts the offset ring's corners with the pen join and draws nothing on
    parts narrower than twice the offset, which is reported."""
    layer = _layer("Polygon", INSIDE_POLYGONS, str(tmp_path / "ij.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol(
        [_inside_line(4, Qgis.RenderUnit.Millimeters, 1.5)])))
    _, rules, diags = _export(plugin, layer, tmp_path)
    over = [r for r in rules if r.recipe.kind == "polygon_offset"]
    assert len(over) == 1
    line = over[0].rule.symbol().symbolLayer(0)
    assert (line.width(), line.offset()) == (3.5, 1.75)
    assert line.penJoinStyle() == Qt.PenJoinStyle.MiterJoin
    assert any("beyond zoom" in d.message for d in diags.by_code("Q2VT_SYMBOL_APPROXIMATE"))


def test_dashed_line_drawn_inside_polygon_keeps_its_dash_lengths(plugin, tmp_path):
    """Dashes stay a line: the band's half width at its middle, with Qt's
    dash pattern (multiples of the pen width) kept at the full width's
    lengths."""
    layer = _layer("Polygon", INSIDE_POLYGONS, str(tmp_path / "id.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol(
        [_inside_line(4, Qgis.RenderUnit.Millimeters, pen=Qt.PenStyle.DashLine)])))
    _, rules, _ = _export(plugin, layer, tmp_path)
    assert rules and all(r.recipe.kind == "polygon_offset" for r in rules)
    line = rules[0].rule.symbol().symbolLayer(0)
    assert (line.width(), line.drawInsidePolygon(), line.useCustomDashPattern()) == (2.0, False, True)
    assert list(line.customDashVector()) == [16.0, 8.0]


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


def test_limited_map_unit_font_marker_stays_a_sprite(plugin, tmp_path):
    """A map-unit font marker with a minimum and maximum size: its glyph
    outlines grew with the map past the maximum (4x too large when zoomed
    in); the sprite marker applies the limits."""
    from qgis.core import QgsFontMarkerSymbolLayer, QgsMapUnitScale
    layer = _layer("Point", ["POINT(-30 -10)"], str(tmp_path / "fm.gpkg"))
    marker = QgsFontMarkerSymbolLayer("DejaVu Sans", "A", 10)
    marker.setSizeUnit(Qgis.RenderUnit.MapUnits)
    limits = QgsMapUnitScale()
    limits.minSizeMMEnabled, limits.minSizeMM = True, 1.0
    limits.maxSizeMMEnabled, limits.maxSizeMM = True, 4.0
    marker.setSizeMapUnitScale(limits)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol([marker])))
    _, rules, _ = _export(plugin, layer, tmp_path)
    assert rules and not any(r.recipe for r in rules)
    assert rules[0].rule.symbol().symbolLayer(0).layerType() == "FontMarker"


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


def test_screen_point_pattern_markers_are_whole_and_in_qgis_order(plugin, tmp_path, monkeypatch):
    """A screen-unit point pattern drawn marker by marker ("centroid
    within"): whole markers on a grid from the feature's top-left corner,
    the row on the top edge included, written column by column from the
    left, each column from the top (QGIS's drawing order, later markers on
    top), also when the polygon is cut into pieces."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener
    from q2vt_plugin.src.core.rules_exporter import RulesExporter
    from fidelity import materialize as mat
    from fidelity.diagnostics import DiagnosticCollector
    from fidelity.model import ZoomInterval
    monkeypatch.setattr(mat, "PIECE_CELLS", 4)  # several pieces
    layer = _layer("Polygon", ["POLYGON((-100 -90, 95 -90, 95 80, -100 80, -100 -90))"],
                   str(tmp_path / "p.gpkg"))
    pp = QgsPointPatternFillSymbolLayer()
    marker = _marker(Qgis.MarkerShape.Square, 14)
    pp.setSubSymbol(marker)
    for name in ("DistanceX", "DistanceY"):
        getattr(pp, f"set{name}")(10)
        getattr(pp, f"set{name}Unit")(Qgis.RenderUnit.Pixels)
    pp.setClipMode(Qgis.MarkerClipMode.CentroidWithin)
    pp.setCoordinateReference(Qgis.SymbolCoordinateReference.Feature)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([pp])))
    reset_project(layer)
    diags = DiagnosticCollector()
    rules = RulesFlattener(16, 16, str(tmp_path), QgsProcessingFeedback(), diags).flatten_all_rules()
    # Beyond the archive's last zoom: the true-size texture.
    over = [r for r in rules if r.recipe is None]
    assert len(over) == 1 and over[0].visibility == ZoomInterval(17.0, None)
    rules = [r for r in rules if r.recipe is not None]
    assert len(rules) == 8 and all(r.recipe.param("ordered") for r in rules)  # eighths of z16
    assert max(r.visibility.max_zoom for r in rules) == 17.0
    assert {r.z_order for r in rules} == {"source"}
    utils = tmp_path / "utils"
    utils.mkdir()
    layers, rules = RulesExporter(rules, EXTENT, 16, 16, str(utils), 0, QgsProcessingFeedback(),
                                  diagnostics=diags).export()
    assert len(layers) == 8
    for output in layers:
        points = [f.geometry().asPoint() for f in output.getFeatures()]
        xs = sorted({round(p.x(), 6) for p in points})
        ys = sorted({round(p.y(), 6) for p in points}, reverse=True)
        assert len(xs) > 8 and len(ys) > 8 and len(points) == len(xs) * len(ys)
        assert xs[0] == -100 and ys[0] == 80  # the left and top edges' markers
        assert [(round(p.x(), 6), round(p.y(), 6)) for p in points] == \
            [(x, y) for x in xs for y in ys]


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


def _budget_symbol(kind):
    from qgis.core import QgsArrowSymbolLayer, QgsShapeburstFillSymbolLayer
    if kind == "polygon_offset":
        line = QgsSimpleLineSymbolLayer(QColor("red"), 0.6)
        line.setOffset(0.5)
        line.setOffsetUnit(Qgis.RenderUnit.Millimeters)
        return QgsFillSymbol([line])
    if kind == "shapeburst":
        burst = QgsShapeburstFillSymbolLayer(QColor("black"), QColor("white"))
        burst.setUseWholeShape(False)
        burst.setMaxDistance(2)
        burst.setDistanceUnit(Qgis.RenderUnit.Millimeters)
        return QgsFillSymbol([burst])
    arrow = QgsArrowSymbolLayer()
    for name in ("ArrowWidth", "ArrowStartWidth", "HeadLength", "HeadThickness"):
        getattr(arrow, f"set{name}")(1.5)
        getattr(arrow, f"set{name}Unit")(Qgis.RenderUnit.Millimeters)
    arrow.setIsRepeated(False)
    return QgsLineSymbol([arrow])


def _zoom_rules(tmp_path, low, high):
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener
    from fidelity.diagnostics import DiagnosticCollector
    diags = DiagnosticCollector()
    rules = RulesFlattener(low, high, str(tmp_path), QgsProcessingFeedback(), diags).flatten_all_rules()
    return rules, diags


def _band_key(rule):
    visible = rule.visibility
    return (rule.get_attr("o"), rule.get_attr("i"), rule.get_attr("b"),
            visible and (visible.min_zoom, visible.max_zoom),
            rule.recipe and (rule.recipe.kind, rule.recipe.params))


@pytest.mark.parametrize("kind", ["polygon_offset", "shapeburst", "arrow"])
def test_a_map_from_zoom_0_keeps_the_exact_last_zooms(plugin, tmp_path, monkeypatch, kind):
    """Per-zoom output budgets counted every exported zoom alike: from zoom 0
    (the Publish window's default) the low zooms, where a layer is a few
    pixels, used up the budget, and the last zooms lost their exact offsets
    (drawn with the browser's line offset) or sub-zoom bands. The last zooms
    now get what an export of only them gets."""
    from q2vt_plugin.src.core import materializer as materializer_module
    if kind == "arrow":
        wkts = [f"LINESTRING({x} 0, {x + 30} 40, {x + 60} 0)" for x in range(-100, 100, 50)]
        layer = _layer("LineString", wkts, str(tmp_path / f"{kind}.gpkg"))
        per_zoom = len(wkts)  # arrows per zoom step
    else:
        # Shapeburst: a 2 mm inset fits inside at 13-16. Offset: 0.5 mm
        # inwards is wider than the squares below zoom 13 (no outline).
        size = 150 if kind == "shapeburst" else 30
        wkts = [f"POLYGON(({x} 0, {x + size} 0, {x + size} {size}, {x} {size}, {x} 0))"
                for x in range(0, 800, 200)]
        layer = _layer("Polygon", wkts, str(tmp_path / f"{kind}.gpkg"))
        per_zoom = 5 * len(wkts)  # vertices per zoom step
    layer.setRenderer(QgsSingleSymbolRenderer(_budget_symbol(kind)))
    reset_project(layer)
    budget = per_zoom * 8  # the last four zooms with two steps; not every zoom with one
    if kind == "shapeburst":
        # Bands per zoom step at 13-16: every band of every feature; below
        # them a feature a few pixels wide has few. The last four zooms fit
        # with four steps and the zooms below with two; every zoom counted
        # alike (as before) only with one.
        rules, _ = _zoom_rules(tmp_path, 16, 16)
        per_zoom = len(wkts) * len(rules[0].recipe.param("colors"))
        budget = int(per_zoom * 22.1)
    monkeypatch.setattr(materializer_module.SymbolMaterializer, "MAX_PATTERN_ELEMENTS", budget)
    narrow, narrow_diags = _zoom_rules(tmp_path, 13, 16)
    wide, wide_diags = _zoom_rules(tmp_path, 0, 16)
    # The exported zooms (the zooms past the last tiles, "v", share what is
    # left of the budget, which the zooms below take in the wide export).
    top = sorted(repr(_band_key(r)) for r in narrow if r.get_attr("v") is None)
    assert len(top) > 4
    assert not [d for d in narrow_diags.by_code("Q2VT_PATTERN_BUDGET")
                if "past the last tile zoom" not in d.message]
    assert sorted(repr(_band_key(r)) for r in wide
                  if r.get_attr("o") >= 13 and r.get_attr("v") is None) == top
    if kind == "polygon_offset":  # no outline (QGIS draws none), no budget spent
        assert not [r for r in wide if r.get_attr("o") < 13]
        assert not wide_diags.by_code("Q2VT_PATTERN_BUDGET")


def _offset_squares(tmp_path, sizes):
    wkts = [f"POLYGON(({x} 0, {x + size} 0, {x + size} {size}, {x} {size}, {x} 0))"
            for x, size in zip(range(0, 10000 * len(sizes), 10000), sizes)]
    layer = _layer("Polygon", wkts, str(tmp_path / "squares.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(_budget_symbol("polygon_offset")))
    reset_project(layer)


def test_polygon_offset_over_the_budget_is_native_only_where_drawn(plugin, tmp_path, monkeypatch):
    """Past the budget the browser's line offset draws the lower zooms; it
    must not draw at the zooms where every ring's inward offset is empty
    (QGIS draws nothing there, the browser drew loops)."""
    from q2vt_plugin.src.core import materializer as materializer_module
    # 0.5 mm inwards: the 30 m squares have an outline from zoom 13, the
    # 2,000 m one from zoom 7.
    _offset_squares(tmp_path, [30, 30, 30, 30, 2000])
    monkeypatch.setattr(materializer_module.SymbolMaterializer, "MAX_PATTERN_ELEMENTS", 100)
    rules, diags = _zoom_rules(tmp_path, 0, 16)
    native = [r for r in rules if dict(r.recipe.params).get("rhr") and r.get_attr("o") < 16]
    assert len(native) == 1
    assert (native[0].get_attr("o"), native[0].get_attr("i")) == (7, 12)
    assert (native[0].visibility.min_zoom, native[0].visibility.max_zoom) == (7.0, 13.0)
    assert sorted({r.get_attr("o") for r in rules if r not in native}) == [13, 14, 15, 16]
    assert "below zoom 13" in diags.by_code("Q2VT_PATTERN_BUDGET")[0].message


def test_polygon_offset_budget_keeps_the_uniform_steps_of_a_narrow_export(plugin, tmp_path,
                                                                          monkeypatch):
    """Giving the last zooms finer steps must not leave the zooms below with
    fewer than one step count for every zoom gives (14-19: two per zoom)."""
    from q2vt_plugin.src.core import materializer as materializer_module
    _offset_squares(tmp_path, [40] * 10)  # 5 vertices each, drawn at 14-19
    per_zoom = 50
    monkeypatch.setattr(materializer_module.SymbolMaterializer, "MAX_PATTERN_ELEMENTS",
                        int(per_zoom * 18.99))  # 6 zooms x 2 steps, not x 4
    rules, diags = _zoom_rules(tmp_path, 14, 19)
    exact = [r for r in rules if not dict(r.recipe.params).get("rhr")]
    steps = {zoom: sum(r.get_attr("o") == zoom for r in exact) for zoom in range(14, 20)}
    assert steps == {zoom: 2 for zoom in range(14, 20)}
    assert not diags.by_code("Q2VT_PATTERN_BUDGET")


@pytest.mark.parametrize("budget, exact_zooms", [(350, [14, 15, 16, 17, 18, 19]), (10, [])])
def test_polygon_offset_overzoom_has_its_own_dataset(plugin, tmp_path, monkeypatch, budget,
                                                     exact_zooms):
    """With one sub-zoom step at the last zoom, its exact rule and the
    overzoom rule (browser's line offset) had the same dataset name: one
    style layer id twice, both shown only from the overzoom on (no outline
    at the last zoom, a double one past it). Fully native: one rule from
    the first zoom on, overzoom included."""
    from q2vt_plugin.src.core import materializer as materializer_module
    _offset_squares(tmp_path, [40] * 10)  # 50 vertices per zoom step at 14-19
    monkeypatch.setattr(materializer_module.SymbolMaterializer, "MAX_PATTERN_ELEMENTS", budget)
    rules, _ = _zoom_rules(tmp_path, 14, 19)
    names = [r.output_dataset for r in rules]
    assert len(set(names)) == len(names)
    exact = [r for r in rules if not dict(r.recipe.params).get("rhr")]
    assert sorted(r.get_attr("o") for r in exact) == exact_zooms  # one step each
    native = [(r.visibility.min_zoom, r.visibility.max_zoom) for r in rules
              if dict(r.recipe.params).get("rhr")]
    if exact_zooms:
        last = next(r for r in exact if r.get_attr("o") == 19)
        assert (last.visibility.min_zoom, last.visibility.max_zoom) == (19.0, 20.0)
        assert native == [(20.0, None)]
    else:
        assert native == [(14.0, None)]


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


def _limits(kind):
    from qgis.core import QgsMapUnitScale
    if kind == "range":
        return QgsMapUnitScale(50000, 5000)
    limits = QgsMapUnitScale()
    limits.minSizeMMEnabled, limits.minSizeMM = True, 1.0
    limits.maxSizeMMEnabled, limits.maxSizeMM = True, 4.0
    return limits


def _qgis_px(value, kind, zoom):
    """QgsRenderContext::convertToPainterUnits of a limited map-unit length
    (Web Mercator project, 96 dpi)."""
    from fidelity.zoom import scale_to_zoom
    if kind == "range":
        zoom = min(max(zoom, scale_to_zoom(50000)), scale_to_zoom(5000))
    px = value * 512 * 2 ** zoom / 40075016.68557849
    return min(max(px, 96 / 25.4), 4 * 96 / 25.4) if kind == "mm" else px


def _limited_spacing(plugin, layer, recipe_param, getter, zooms, kind, out_dir):
    """The spacing the export draws at each zoom (one visible component,
    materialized in map units or kept in millimetres) against QGIS's."""
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener
    from fidelity.diagnostics import DiagnosticCollector
    reset_project(layer)
    rules = RulesFlattener(0, 16, str(out_dir), QgsProcessingFeedback(),
                           DiagnosticCollector()).flatten_all_rules()
    for zoom in zooms:
        visible = [r for r in rules if r.visibility.contains(zoom)
                   and (r.recipe is None or r.recipe.param(recipe_param) is not None)]
        assert len(visible) == 1, (zoom, [r.output_dataset for r in visible])
        rule = visible[0]
        if rule.recipe is not None and recipe_param == "interval" and \
                rule.visibility.max_zoom - rule.visibility.min_zoom == 1.0 and \
                max(f.geometry().length() for f in layer.getFeatures()) < \
                rule.recipe.param("interval") / 2 ** 0.5:
            # One placement a zoom where no line is an interval long (at the
            # zoom's end too): one marker a line, whatever the spacing.
            continue
        if rule.recipe is not None:
            drawn = rule.recipe.param(recipe_param) * 512 * 2 ** zoom / 40075016.68557849
        else:
            # A texture: in millimetres, or in plain map units (no limits).
            symbol_layer = rule.rule.symbol().symbolLayer(0)
            scale = getattr(symbol_layer, f"{getter}MapUnitScale")()
            assert not (scale.minScale or scale.maxScale or scale.minSizeMMEnabled
                        or scale.maxSizeMMEnabled)
            drawn = getattr(symbol_layer, getter)()
            if getattr(symbol_layer, f"{getter}Unit")() == Qgis.RenderUnit.Millimeters:
                drawn *= 96 / 25.4
            else:
                drawn *= 512 * 2 ** zoom / 40075016.68557849
        # Screen spacings are materialized per eighth of a zoom (+-4.5 %).
        assert drawn == pytest.approx(_qgis_px(15, kind, zoom), rel=0.05), (zoom, rule.output_dataset)


@pytest.mark.parametrize("kind", ["mm", "range"])
def test_map_unit_hatch_spacing_limits_match_qgis(plugin, tmp_path, kind):
    """A map-unit hatch with min/max spacing or a scale range: QGIS keeps the
    spacing between the limits; the hatch kept the plain map spacing (5x
    too sparse when zoomed in, a smear when zoomed out)."""
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83))"],
                   str(tmp_path / "hatch.gpkg"))
    lp = QgsLinePatternFillSymbolLayer()
    lp.setLineAngle(45)
    lp.setDistance(15)
    lp.setDistanceUnit(Qgis.RenderUnit.MapUnits)
    lp.setDistanceMapUnitScale(_limits(kind))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([lp])))
    _limited_spacing(plugin, layer, "spacing", "distance",
                     (6, 12.2, 14.5, 15.5, 16.2, 16.5, 17.5, 19, 22), kind, tmp_path)


@pytest.mark.parametrize("kind", ["mm", "range"])
def test_map_unit_marker_interval_limits_match_qgis(plugin, tmp_path, kind):
    """A map-unit marker interval with min/max size or a scale range: the
    markers were spaced by the plain map interval at every zoom."""
    layer = _layer("LineString", LINES, str(tmp_path / "ml.gpkg"))
    ml = QgsMarkerLineSymbolLayer()
    ml.setPlacements(Qgis.MarkerLinePlacement.Interval)
    ml.setInterval(15)
    ml.setIntervalUnit(Qgis.RenderUnit.MapUnits)
    ml.setIntervalMapUnitScale(_limits(kind))
    ml.setSubSymbol(_marker(Qgis.MarkerShape.Triangle, 6))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([ml])))
    _limited_spacing(plugin, layer, "interval", "interval",
                     (6, 12.2, 14.5, 15.5, 16.2, 16.5, 17.5, 19, 22), kind, tmp_path)


def test_point_pattern_distance_limits_match_qgis(plugin, tmp_path):
    """A point pattern drawn marker by marker (clip mode "centroid within")
    with min/max distances: QGIS limits both distances, the grid kept the
    plain map distances; beyond the archive's last zoom the screen grid
    stays a true-size texture."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83))"],
                   str(tmp_path / "pp.gpkg"))
    pp = QgsPointPatternFillSymbolLayer()
    for name in ("DistanceX", "DistanceY"):
        getattr(pp, f"set{name}")(15)
        getattr(pp, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
        getattr(pp, f"set{name}MapUnitScale")(_limits("mm"))
    pp.setClipMode(Qgis.MarkerClipMode.CentroidWithin)
    pp.setSubSymbol(_marker(Qgis.MarkerShape.Circle, 3))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([pp])))
    _limited_spacing(plugin, layer, "dx", "distanceX", (12.2, 15.5, 16.5, 17.5, 19, 22), "mm",
                     tmp_path)


def _pattern_exporter(symbol, low, maxzoom=16):
    """The fill layer converted from ``low`` to zoom 24 (archive up to
    ``maxzoom``)."""
    from q2vt_plugin.src.core import maplibre_converter as mc
    from fidelity.diagnostics import DiagnosticCollector
    exporter = mc.QgisMapLibreStyleExporter.__new__(mc.QgisMapLibreStyleExporter)
    exporter.pattern_images, exporter.marker_symbols, exporter.marker_counter = {}, {}, 0
    exporter.profile = mc.ExportProfile()
    exporter.context = mc.ConversionContext(DiagnosticCollector())
    exporter.style, exporter.maxzoom = {"layers": []}, maxzoom
    mc.PropertyExtractor.context = exporter.context
    exporter.context.reference_zoom = low
    exporter._convert_symbol(symbol, "s", "src", "q2vt", low, 24)
    return exporter


def test_screen_spaced_hatch_lines_in_map_units_grow_per_zoom(plugin):
    """A hatch spaced in millimetres (e.g. a map-unit spacing clamped to its
    maximum) with map-unit lines: one texture froze the line width at its
    first zoom (a thin hatch where QGIS fills the polygon when zoomed in).
    True-size textures per zoom up to 22, lines drawn at mid-zoom width."""
    lp = QgsLinePatternFillSymbolLayer()
    lp.setLineAngle(45)
    lp.setDistance(4)
    lp.setDistanceUnit(Qgis.RenderUnit.Millimeters)
    line = QgsSimpleLineSymbolLayer(QColor("black"), 1.7)
    line.setWidthUnit(Qgis.RenderUnit.MapUnits)
    lp.setSubSymbol(QgsLineSymbol([line]))
    exporter = _pattern_exporter(QgsFillSymbol([lp]), 16.25)
    layer = exporter.style["layers"][0]
    pattern = layer["paint"]["fill-pattern"]
    assert pattern[:2] == ["step", ["zoom"]] and pattern[3::2] == [17, 18, 19, 20, 21, 22]
    assert layer["metadata"][exporter.SCREEN_PATTERN_FLAG] is True
    spacing = 4 * 96 / 25.4
    for zoom, name in zip([16.25] + pattern[3::2], pattern[2::2]):
        middle = (zoom + math.floor(zoom) + 1) / 2
        width = 1.7 * 512 * 2 ** middle / 40075016.68557849
        cell = exporter.pattern_images[name].img_1x
        alpha = cell.getchannel("A")
        ink = sum(alpha.getdata()) / 255.0 / (cell.size[0] * cell.size[1])
        assert ink == pytest.approx(min(1.0, width / spacing), abs=0.08), zoom


def test_point_pattern_rows_in_map_units_follow_the_map_in_overzoom(plugin):
    """A point pattern spaced in millimetres across and map units down (QGIS
    clamps only the distance across when it draws the pattern brush): the
    textures stopped at max zoom + 3, so the rows stopped spreading apart
    and the overzoomed map was far too dense."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    pp = QgsPointPatternFillSymbolLayer()
    pp.setSubSymbol(_marker(Qgis.MarkerShape.Circle, 1))
    pp.setDistanceX(4)
    pp.setDistanceXUnit(Qgis.RenderUnit.Millimeters)
    pp.setDistanceY(15)
    pp.setDistanceYUnit(Qgis.RenderUnit.MapUnits)
    exporter = _pattern_exporter(QgsFillSymbol([pp]), 16.25)
    layer = exporter.style["layers"][0]
    pattern = layer["paint"]["fill-pattern"]
    # One texture per zoom past max zoom + 3 while a cell stays within the
    # texture size cap (z22's 804 px rows are over it); above the last one
    # the patched MapLibre scales it with the map (q2vt:pattern-zoom).
    assert pattern[3::2] == [17, 18, 19, 20, 21]
    assert layer["metadata"][exporter.PATTERN_ZOOM_FLAG] == 21
    assert max(exporter.pattern_images[pattern[-1]].img_1x.size) <= exporter.MAX_PATTERN_TEXTURE_PX
    for zoom, name in zip(pattern[3::2], pattern[4::2]):
        height = exporter.pattern_images[name].img_1x.size[1]
        assert height == pytest.approx(15 * 512 * 2 ** zoom / 40075016.68557849, rel=0.05), zoom


def _percent_pattern(unit, offset, displacement, spacing, clip):
    from qgis.core import QgsPointPatternFillSymbolLayer
    pp = QgsPointPatternFillSymbolLayer()
    marker = _marker(Qgis.MarkerShape.Square, 3)
    marker.symbolLayer(0).setSizeUnit(unit)
    marker.symbolLayer(0).setStrokeStyle(0)
    pp.setSubSymbol(marker)
    for name in ("DistanceX", "DistanceY"):
        getattr(pp, f"set{name}")(spacing)
        getattr(pp, f"set{name}Unit")(unit)
    for name, (value, value_unit) in (("OffsetX", offset), ("OffsetY", offset),
                                      ("DisplacementX", displacement)):
        getattr(pp, f"set{name}")(value)
        getattr(pp, f"set{name}Unit")(value_unit)
    pp.setClipMode(clip)
    return QgsFillSymbol([pp])


@pytest.mark.parametrize("unit,spacing,clip", [
    (Qgis.RenderUnit.MapUnits, 20, "CentroidWithin"), (Qgis.RenderUnit.MapUnits, 20, "Shape"),
    (Qgis.RenderUnit.Millimeters, 8, "CentroidWithin")])
def test_percentage_pattern_offsets_are_of_the_spacing(plugin, tmp_path, unit, spacing, clip):
    """QgsPointPatternFillSymbolLayer: a Percentage offset or displacement is
    that share of the grid spacing, the same grid as the absolute value
    (materialized grids; the screen-unit "Shape" texture is checked in
    test_sprites)."""
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener
    from fidelity.diagnostics import DiagnosticCollector
    pct = Qgis.RenderUnit.Percentage
    recipes = []
    for index, (offset, displacement) in enumerate((((50, pct), (50, pct)),
                                                    ((spacing / 2, unit), (spacing / 2, unit)))):
        layer = _layer("Polygon", ["POLYGON((-100 -90, 95 -90, 95 80, -100 80, -100 -90))"],
                       str(tmp_path / f"pp{index}.gpkg"))
        layer.setRenderer(QgsSingleSymbolRenderer(_percent_pattern(
            unit, offset, displacement, spacing, getattr(Qgis.MarkerClipMode, clip))))
        reset_project(layer)
        diags = DiagnosticCollector()
        rules = RulesFlattener(0, 16, str(tmp_path), QgsProcessingFeedback(),
                               diags).flatten_all_rules()
        recipes.append([r.recipe for r in rules if r.recipe])
        # (past the last tile zoom the pattern is a texture: reported apart)
        assert not [d for d in diags.by_code("Q2VT_PATTERN_APPROXIMATE")
                    if not d.message.startswith("Past the last tile zoom")] or clip == "Shape"
    assert recipes[0] and recipes[0] == recipes[1]
    params = dict(recipes[0][-1].params)
    assert params["disp_x"] and params["off_x"] and params["off_y"]


@pytest.mark.parametrize("unit", [Qgis.RenderUnit.MapUnits, Qgis.RenderUnit.MetersInMapUnits])
def test_map_unit_fill_offset_moves_the_polygon(plugin, tmp_path, unit):
    """QgsSimpleFillSymbolLayer::renderPolygon shifts the polygon and its
    outline by the offset in any unit (x right, y down); map units (and
    meters at scale in a metre CRS) move the exported geometry, so the
    shift is exact at every zoom, overzoom included, with no tile seams."""
    from qgis.core import QgsSimpleFillSymbolLayer
    from qgis.PyQt.QtCore import QPointF
    layer = _layer("Polygon", ["POLYGON((-90 -80, 60 -80, 60 50, -90 50, -90 -80))"],
                   str(tmp_path / "fill.gpkg"))
    fill = QgsSimpleFillSymbolLayer(QColor("red"), strokeColor=QColor("black"),
                                    strokeWidth=0.8)
    fill.setOffset(QPointF(30, 20))
    fill.setOffsetUnit(unit)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([fill])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _diags = _export(plugin, layer, tmp_path)
    assert sorted(r.get_attr("c") for r in rules) == [1, 2]  # fill + its outline
    for output, rule in zip(outputs, rules):
        assert rule.shift == pytest.approx((30, -20))
        assert not rule.translate
        box = output.extent()
        assert (box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()) == \
            pytest.approx((-60, -100, 90, 30), abs=1e-6)
        symbol_layer = rule.rule.symbol().symbolLayer(0)
        if rule.get_attr("c") == 2:
            assert symbol_layer.offset() == QPointF()
    assert mask_difference(reference, ink_mask(render(outputs, EXTENT, (240, 240)))) < 0.05


@pytest.mark.parametrize("width,unit,dashed", [
    (2.0, Qgis.RenderUnit.Millimeters, False),
    (6.0, Qgis.RenderUnit.MapUnits, False),
    (1.2, Qgis.RenderUnit.Millimeters, True),
])
def test_generator_fill_stroke_is_a_line_on_the_generated_polygons(plugin, tmp_path, width,
                                                                   unit, dashed):
    """A geometry generator drawn with a fill symbol: each sub-symbol layer
    is a rule on the generated polygons, and a simple fill's stroke is a
    line with its width, unit and dashes on their rings (MapLibre's
    fill-outline is a one-pixel hairline)."""
    from qgis.core import QgsGeometryGeneratorSymbolLayer, QgsSimpleFillSymbolLayer
    from qgis.PyQt.QtCore import Qt
    pen = Qt.PenStyle.DashLine if dashed else Qt.PenStyle.SolidLine
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83))"],
                   str(tmp_path / "gen.gpkg"))
    inner = QgsSimpleFillSymbolLayer(QColor(255, 230, 200), Qt.BrushStyle.SolidPattern,
                                     QColor(200, 0, 0), pen, width)
    inner.setStrokeWidthUnit(unit)
    generator = QgsGeometryGeneratorSymbolLayer.create({
        "geometryModifier": "buffer($geometry, -15)", "SymbolType": "Fill"})
    generator.setSubSymbol(QgsFillSymbol([inner]))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([generator])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _diags = _export(plugin, layer, tmp_path)
    kinds = sorted((r.get_attr("c"), r.rule.symbol().symbolLayer(0).layerType()) for r in rules)
    assert kinds == [(1, "SimpleLine"), (2, "SimpleFill")]
    for output, rule in zip(outputs, rules):
        assert rule.pre_generator
        box = output.extent()  # the generated (buffered) polygon
        assert (box.xMinimum(), box.yMaximum()) == pytest.approx((-82, 56), abs=1e-6)
        if rule.get_attr("c") == 1:
            line = rule.rule.symbol().symbolLayer(0)
            assert (line.width(), line.widthUnit(), line.penStyle()) == (width, unit, pen)
            # Drawn on the rings as the style draws it (a line symbol).
            output.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([line.clone()])))
        else:
            assert rule.rule.symbol().symbolLayer(0).strokeStyle() == Qt.PenStyle.NoPen
    assert mask_difference(reference, ink_mask(render(outputs, EXTENT, (240, 240)))) < 0.05


def test_generator_fill_line_and_centroid_layers_are_drawn(plugin, tmp_path):
    """A fill generator's sub-symbol layers are drawn like a fill layer's:
    a line layer (an outline) on the generated polygons' rings, a centroid
    fill as markers at their centroids."""
    from qgis.core import QgsCentroidFillSymbolLayer, QgsGeometryGeneratorSymbolLayer
    from qgis.core import QgsSimpleFillSymbolLayer
    from qgis.PyQt.QtCore import Qt
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83))"],
                   str(tmp_path / "gen.gpkg"))
    fill = QgsSimpleFillSymbolLayer(QColor(255, 230, 200), Qt.BrushStyle.SolidPattern,
                                    QColor("black"), Qt.PenStyle.NoPen)
    outline = QgsSimpleLineSymbolLayer(QColor(200, 0, 0), 1.5)
    centroid = QgsCentroidFillSymbolLayer()
    centroid.setSubSymbol(_marker(Qgis.MarkerShape.Square, 10))
    generator = QgsGeometryGeneratorSymbolLayer.create({
        "geometryModifier": "buffer($geometry, -15)", "SymbolType": "Fill"})
    generator.setSubSymbol(QgsFillSymbol([fill, outline, centroid]))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([generator])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, diags = _export(plugin, layer, tmp_path)
    kinds = sorted((r.get_attr("c"), r.rule.symbol().symbolLayer(0).layerType()) for r in rules)
    assert kinds == [(0, "CentroidFill"), (1, "SimpleLine"), (2, "SimpleFill")]
    for output, rule in zip(outputs, rules):
        symbol_layer = rule.rule.symbol().symbolLayer(0)
        if rule.get_attr("c") == 0:  # the styler draws the centroids with the sub-symbol
            output.setRenderer(QgsSingleSymbolRenderer(symbol_layer.subSymbol().clone()))
        elif rule.get_attr("c") == 1:
            output.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([symbol_layer.clone()])))
    assert mask_difference(reference, ink_mask(render(outputs, EXTENT, (240, 240)))) < 0.05


def test_generator_fill_coerces_lines_to_polygons(plugin, tmp_path):
    """QgsGeometryGeneratorSymbolLayer coerces a fill generator's line output
    to polygons (each part closed into a ring), as QgsGeometry::coerceToType."""
    from qgis.core import QgsGeometryGeneratorSymbolLayer, QgsSimpleFillSymbolLayer
    from qgis.PyQt.QtCore import Qt
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83))"],
                   str(tmp_path / "gen.gpkg"))
    inner = QgsSimpleFillSymbolLayer(QColor(255, 230, 200), Qt.BrushStyle.SolidPattern,
                                     QColor(200, 0, 0), Qt.PenStyle.SolidLine, 1.0)
    generator = QgsGeometryGeneratorSymbolLayer.create({
        "geometryModifier": "make_line(make_point(-90, -70), make_point(40, -70), "
                            "make_point(40, 60))", "SymbolType": "Fill"})
    generator.setSubSymbol(QgsFillSymbol([inner]))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([generator])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _diags = _export(plugin, layer, tmp_path)
    assert sorted(r.get_attr("c") for r in rules) == [1, 2]
    for output, rule in zip(outputs, rules):
        geometry = next(output.getFeatures()).geometry()
        if rule.get_attr("c") == 2:  # the closed polygon, not the line
            assert geometry.type() == Qgis.GeometryType.Polygon
            assert geometry.area() == pytest.approx(130 * 130 / 2)
        else:
            line = rule.rule.symbol().symbolLayer(0)
            output.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([line.clone()])))
    assert mask_difference(reference, ink_mask(render(outputs, EXTENT, (240, 240)))) < 0.05


def test_generator_fill_patterns_are_materialized(plugin, tmp_path):
    """A map-unit hatch inside a fill generator is exported as hatch lines
    on the generated polygons (not one texture frozen at the first zoom)."""
    from qgis.core import QgsGeometryGeneratorSymbolLayer
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83))"],
                   str(tmp_path / "hatch.gpkg"))
    hatch = QgsLinePatternFillSymbolLayer()
    hatch.setLineAngle(45)
    hatch.setDistance(8)
    hatch.setDistanceUnit(Qgis.RenderUnit.MapUnits)
    hatch.subSymbol().symbolLayer(0).setWidth(1.5)
    hatch.subSymbol().symbolLayer(0).setWidthUnit(Qgis.RenderUnit.MapUnits)
    generator = QgsGeometryGeneratorSymbolLayer.create({
        "geometryModifier": "buffer($geometry, -15)", "SymbolType": "Fill"})
    generator.setSubSymbol(QgsFillSymbol([hatch]))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([generator])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _diags = _export(plugin, layer, tmp_path)
    hatched = [(o, r) for o, r in zip(outputs, rules) if r.recipe is not None]
    assert hatched and all(r.recipe.kind == "hatch_lines" and r.pre_generator for _, r in hatched)
    ours = ink_mask(render([o for o, _ in hatched], EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.1


def _generator_fill(expression, stroke_width=2.0):
    from qgis.core import QgsGeometryGeneratorSymbolLayer, QgsSimpleFillSymbolLayer
    from qgis.PyQt.QtCore import Qt
    inner = QgsSimpleFillSymbolLayer(QColor(255, 230, 200), Qt.BrushStyle.SolidPattern,
                                     QColor(200, 0, 0), Qt.PenStyle.SolidLine, stroke_width)
    generator = QgsGeometryGeneratorSymbolLayer.create({
        "geometryModifier": expression, "SymbolType": "Fill"})
    generator.setSubSymbol(QgsFillSymbol([inner]))
    return QgsFillSymbol([generator])


def _draw_outlines_as_lines(outputs, rules):
    """The styler draws a split stroke (class 1) as a line on the rings."""
    for output, rule in zip(outputs, rules):
        if rule.get_attr("c") == 1:
            line = rule.rule.symbol().symbolLayer(0).clone()
            output.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([line])))


@pytest.mark.parametrize("kind", ["Fill", "Line"])
def test_generator_with_map_scale_is_drawn_at_every_zoom(plugin, tmp_path, kind):
    """A geometry generator whose expression reads @map_scale is exported
    per zoom with that zoom's scale (the generated polygons follow the
    zoom, as in QGIS), not with a NULL scale that generates nothing. A line
    sub-symbol draws the generated polygons' rings."""
    from qgis.core import QgsGeometryGeneratorSymbolLayer
    from q2vt_plugin.src.utils.zoom_levels import ZoomLevels
    layer = _layer("Polygon", ["POLYGON((-97 -83, 53 -83, 53 71, -97 71, -97 -83))"],
                   str(tmp_path / "gen.gpkg"))
    expression = "buffer($geometry, -@map_scale/400)"
    if kind == "Fill":
        symbol = _generator_fill(expression)
    else:
        generator = QgsGeometryGeneratorSymbolLayer.create({
            "geometryModifier": expression, "SymbolType": "Line"})
        generator.setSubSymbol(QgsLineSymbol([QgsSimpleLineSymbolLayer(QColor(200, 0, 0), 2.0)]))
        symbol = QgsFillSymbol([generator])
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    outputs, rules, _diags = _export(plugin, layer, tmp_path)
    generated = [(o, r) for o, r in zip(outputs, rules)
                 if r.get_attr("c") == (2 if kind == "Fill" else 1)]
    zooms = sorted(r.get_attr("o") for _, r in generated)
    # The 150 x 154 m polygon vanishes when the inward buffer passes 75 m.
    first = min(z for z in range(23) if ZoomLevels.zoom_to_scale(z) / 400 < 75)
    assert first > 10 and zooms == list(range(first, first + len(zooms))) and zooms[-1] >= 21
    for output, rule in generated:
        assert rule.get_attr("o") == rule.get_attr("i")
        assert rule.pre_generator.startswith("with_variable('map_scale'")
        inset = ZoomLevels.zoom_to_scale(rule.get_attr("o")) / 400
        box = output.extent()
        assert (box.xMinimum(), box.yMaximum()) == pytest.approx((-97 + inset, 71 - inset),
                                                                 abs=1e-6)


@pytest.mark.parametrize("renderer", ["merged", "inverted"])
def test_generator_fill_draws_on_the_merged_or_inverted_geometry(plugin, tmp_path, renderer):
    """QgsMergedFeatureRenderer / QgsInvertedPolygonRenderer render the
    symbol, its geometry generator included, on the merged (inverted)
    geometry: two touching parcels shrink as one, and an inverted area's
    holes grow."""
    from qgis.core import QgsInvertedPolygonRenderer, QgsMergedFeatureRenderer
    kind = QgsMergedFeatureRenderer if renderer == "merged" else QgsInvertedPolygonRenderer
    layer = _layer("Polygon", ["POLYGON((-90 -60, 0 -60, 0 60, -90 60, -90 -60))",
                               "POLYGON((0 -60, 90 -60, 90 60, 0 60, 0 -60))"],
                   str(tmp_path / f"{renderer}.gpkg"))
    layer.setRenderer(kind(QgsSingleSymbolRenderer(_generator_fill("buffer($geometry, -15)"))))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, rules, _diags = _export(plugin, layer, tmp_path)
    assert sorted(r.get_attr("c") for r in rules) == [1, 2]
    fill = next(o for o, r in zip(outputs, rules) if r.get_attr("c") == 2)
    geometries = [f.geometry() for f in fill.getFeatures()]
    if renderer == "merged":
        assert len(geometries) == 1
        assert geometries[0].area() == pytest.approx(150 * 90)
    else:  # the hole is the merged parcel grown by 15 m (rounded corners)
        hole = QgsGeometry.fromWkt("POLYGON((-90 -60, 90 -60, 90 60, -90 60, -90 -60))")
        assert not any(g.intersects(hole.buffer(14, 16)) for g in geometries)
    _draw_outlines_as_lines(outputs, rules)
    assert mask_difference(reference, ink_mask(render(outputs, EXTENT, (240, 240)))) < 0.05


@pytest.mark.parametrize("limits,shifted", [
    ({}, True), ({"ddp": True}, True), ({"max_mm": 100000.0}, True),
    ({"max_mm": 8.0}, False), ({"min_scale": 50000.0}, False)])
def test_map_unit_fill_offset_shift_or_curve(plugin, tmp_path, limits, shifted):
    """A map-unit fill offset moves the exported polygon (a translate is cut
    at the tile edges when overzoomed) unless its map unit scale limits it
    within the exported zooms (then a zoom-curve translate draws it). A
    data-defined offset uses its static value (the converter reports it)."""
    from qgis.core import QgsMapUnitScale, QgsProperty, QgsSimpleFillSymbolLayer, QgsSymbolLayer
    from qgis.PyQt.QtCore import QPointF
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener
    from fidelity.diagnostics import DiagnosticCollector
    layer = _layer("Polygon", ["POLYGON((-90 -80, 60 -80, 60 50, -90 50, -90 -80))"],
                   str(tmp_path / "fill.gpkg"))
    fill = QgsSimpleFillSymbolLayer(QColor("red"), strokeColor=QColor("black"), strokeWidth=0.8)
    fill.setOffset(QPointF(30, 20))
    fill.setOffsetUnit(Qgis.RenderUnit.MapUnits)
    scale = QgsMapUnitScale()
    if "max_mm" in limits:
        scale.maxSizeMMEnabled, scale.maxSizeMM = True, limits["max_mm"]
    scale.minScale = limits.get("min_scale", 0.0)
    fill.setOffsetMapUnitScale(scale)
    if limits.get("ddp"):
        fill.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyOffset,
                                    QgsProperty.fromExpression("'5,5'"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([fill])))
    reset_project(layer)
    diags = DiagnosticCollector()
    rules = RulesFlattener(10, 16, str(tmp_path), QgsProcessingFeedback(),
                           diags).flatten_all_rules()
    assert sorted(r.get_attr("c") for r in rules) == [1, 2]
    assert bool(diags.by_code("Q2VT_SYMBOL_APPROXIMATE")) == (not shifted)
    for rule in rules:
        if shifted:
            assert rule.shift == pytest.approx((30, -20)) and not rule.translate
        else:
            assert not rule.shift
            assert rule.translate or rule.rule.symbol().symbolLayer(0).offset() == QPointF(30, 20)
