"""Line symbols built from their geometry on screen: arrows (QGIS's arrow
polygons), inner paint effects (strips coloured by QGIS per direction) and
screen-unit line offsets (offset curves per zoom band). Each is checked
against QGIS's own rendering."""

import json
import math
import os
import random
import re
import sys

import pytest
from qgis.core import (Qgis, QgsFeature, QgsFillSymbol, QgsGeometry, QgsLineSymbol,
                       QgsPointXY, QgsProcessingFeedback, QgsRectangle,
                       QgsSimpleFillSymbolLayer, QgsSimpleLineSymbolLayer,
                       QgsSingleSymbolRenderer, QgsVectorLayer)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor

from q2vt_render import ink_mask, mask_difference, render

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_materialize import EXTENT, _layer  # noqa: E402  pylint: disable=wrong-import-position

WORLD = 40075016.68557849
# Sharp tips (MapLibre's line-offset loops there at zooms 15 and 16).
ZIGZAG = "LINESTRING(-100 -60, -90 40, -80 -60, -70 40, -60 -60, 0 -55, 20 30, 60 35, 90 -70)"


def _export(layer, tmp_path, min_zoom, max_zoom):
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener  # pylint: disable=import-error
    from q2vt_plugin.src.core.rules_exporter import RulesExporter  # pylint: disable=import-error
    from fidelity.diagnostics import DiagnosticCollector
    from q2vt_fixtures import reset_project
    reset_project(layer)
    diags = DiagnosticCollector()
    rules = RulesFlattener(min_zoom, max_zoom, str(tmp_path), QgsProcessingFeedback(),
                           diags).flatten_all_rules()
    utils = tmp_path / "utils"
    utils.mkdir()
    layers, rules = RulesExporter(rules, EXTENT, min_zoom, max_zoom, str(utils), 0,
                                  QgsProcessingFeedback(), diagnostics=diags).export()
    by_name = {l.name(): l for l in layers}
    return [(by_name[r.output_dataset], r) for r in rules], diags


def _view(zoom, size=240):
    """Extent of ``size`` pixels around the origin at a MapLibre zoom."""
    half = size / 2 * WORLD / (512 * 2 ** zoom)
    return QgsRectangle(-half, -half, half, half)


def _fill(color="black"):
    return QgsFillSymbol([QgsSimpleFillSymbolLayer(QColor(color), strokeStyle=Qt.PenStyle.NoPen)])


# -- arrows ------------------------------------------------------------------
def test_arrow_polygons_are_qgis_arrows(plugin):
    """fidelity.arrows (straightArrow / curvedArrow / renderPolyline) gives
    the polygons QGIS fills: random arrows of every kind, pixel for pixel."""
    from qgis.core import QgsArrowSymbolLayer
    from fidelity.arrows import arrow_polygons
    rng = random.Random(7)
    size, mm = 300, 96 / 25.4
    for _ in range(25):
        curved, repeated = rng.random() < 0.6, rng.random() < 0.6
        head_type, arrow_type = rng.choice([0, 0, 1, 2]), rng.choice([0, 0, 1, 2])
        points = [(rng.uniform(30, 270), rng.uniform(30, 270)) for _ in range(rng.randint(2, 8))]
        start, width = rng.uniform(0, 3), rng.uniform(0.5, 4)
        length, thickness = rng.uniform(1, 6), rng.uniform(1, 5)
        source = QgsVectorLayer("LineString?crs=EPSG:3857", "a", "memory")
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in points]))
        source.dataProvider().addFeatures([feature])
        arrow = QgsArrowSymbolLayer()
        arrow.setIsCurved(curved)
        arrow.setIsRepeated(repeated)
        arrow.setArrowStartWidth(start)
        arrow.setArrowWidth(width)
        arrow.setHeadLength(length)
        arrow.setHeadThickness(thickness)
        arrow.setHeadType(QgsArrowSymbolLayer.HeadType(head_type))
        arrow.setArrowType(QgsArrowSymbolLayer.ArrowType(arrow_type))
        arrow.setSubSymbol(_fill())
        source.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([arrow])))
        ours = QgsVectorLayer("MultiPolygon?crs=EPSG:3857", "p", "memory")
        for polygon in arrow_polygons([(x, size - y) for x, y in points], curved, repeated,
                                      start * mm, width * mm, length * mm, thickness * mm,
                                      head_type, arrow_type, 0.0):
            geometry = QgsGeometry.fromPolygonXY(
                [[QgsPointXY(x, size - y) for x, y in polygon]]).makeValid()
            geometry.convertGeometryCollectionToSubclass(Qgis.GeometryType.Polygon)
            geometry.convertToMultiType()
            shape = QgsFeature()
            shape.setGeometry(geometry)
            assert ours.dataProvider().addFeatures([shape])[0]
        ours.setRenderer(QgsSingleSymbolRenderer(_fill()))
        extent = QgsRectangle(0, 0, size, size)
        reference = ink_mask(render([source], extent, (size, size)))
        drawn = ink_mask(render([ours], extent, (size, size)))
        # Antialiased edges of thin arcs may tip over the ink threshold.
        assert reference and mask_difference(reference, drawn) < 0.03, \
            (curved, repeated, head_type, arrow_type)


def test_arrow_skeletons_drawn_as_line_and_head_markers_are_qgis_arrows(plugin):
    """fidelity.arrows.arrow_skeletons (arrows past the last tiles' geometry
    grid, SymbolMaterializer._native_arrow): the body's centre line drawn
    with the body's width and a triangle marker turned along the line at
    every tip cover what QGIS fills, curved arrows and half arrows too."""
    from qgis.core import QgsArrowSymbolLayer, QgsEllipseSymbolLayer, QgsField, QgsMarkerSymbol, \
        QgsProperty, QgsSymbolLayer
    from qgis.PyQt.QtCore import QPointF, QVariant
    from fidelity.arrows import arrow_skeletons
    rng = random.Random(11)
    size, mm = 300, 96 / 25.4
    shapes = {1: QgsEllipseSymbolLayer.Shape.RightHalfTriangle,
              2: QgsEllipseSymbolLayer.Shape.LeftHalfTriangle}
    for _ in range(25):
        curved, repeated = rng.random() < 0.6, rng.random() < 0.6
        head_type, arrow_type = rng.choice([0, 0, 1, 2]), rng.choice([0, 0, 1, 2])
        points = [(rng.uniform(30, 270), rng.uniform(30, 270)) for _ in range(rng.randint(2, 6))]
        width, length = rng.uniform(1, 3), rng.uniform(2, 5)
        thickness = rng.uniform(width / 2 + 0.5, 4)
        source = QgsVectorLayer("LineString?crs=EPSG:3857", "a", "memory")
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(*p) for p in points]))
        source.dataProvider().addFeatures([feature])
        arrow = QgsArrowSymbolLayer()
        arrow.setIsCurved(curved)
        arrow.setIsRepeated(repeated)
        arrow.setArrowStartWidth(width)
        arrow.setArrowWidth(width)
        arrow.setHeadLength(length)
        arrow.setHeadThickness(thickness)
        arrow.setHeadType(QgsArrowSymbolLayer.HeadType(head_type))
        arrow.setArrowType(QgsArrowSymbolLayer.ArrowType(arrow_type))
        arrow.setSubSymbol(_fill())
        source.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([arrow])))
        bodies, heads = arrow_skeletons([(x, size - y) for x, y in points], curved, repeated,
                                        width * mm, width * mm, length * mm, head_type,
                                        arrow_type, 0.0)
        lines = QgsVectorLayer("LineString?crs=EPSG:3857", "l", "memory")
        for body in bodies:
            shape = QgsFeature()
            shape.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(x, size - y) for x, y in body]))
            lines.dataProvider().addFeatures([shape])
        line = QgsSimpleLineSymbolLayer(QColor("black"), width * mm / (2 if arrow_type else 1))
        line.setWidthUnit(Qgis.RenderUnit.MapUnits)
        line.setPenCapStyle(Qt.PenCapStyle.FlatCap)
        lines.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([line])))
        tips = QgsVectorLayer("Point?crs=EPSG:3857", "h", "memory")
        tips.dataProvider().addAttributes([QgsField("angle", QVariant.Double),
                                           QgsField("back", QVariant.Int)])
        tips.updateFields()
        for (x, y), (dx, dy), back in heads:
            shape = QgsFeature(tips.fields())
            shape.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, size - y)))
            shape["angle"] = math.degrees(math.atan2(dx, -dy))
            shape["back"] = int(back)
            tips.dataProvider().addFeatures([shape])
        head = QgsEllipseSymbolLayer()
        head.setShape(shapes.get(arrow_type, QgsEllipseSymbolLayer.Shape.Triangle))
        if arrow_type:
            # A head pointing back: the other half (one rule each in the export).
            names = {1: "right_half_triangle", 2: "left_half_triangle"}
            head.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyName, QgsProperty.fromExpression(
                f"if(\"back\", '{names[3 - arrow_type]}', '{names[arrow_type]}')"))
        head.setSymbolWidth(2 * thickness * mm)
        head.setSymbolHeight(length * mm)
        head.setSymbolWidthUnit(Qgis.RenderUnit.MapUnits)
        head.setSymbolHeightUnit(Qgis.RenderUnit.MapUnits)
        head.setColor(QColor("black"))
        head.setStrokeStyle(Qt.PenStyle.NoPen)
        head.setOffset(QPointF(0, length * mm / 2))
        head.setOffsetUnit(Qgis.RenderUnit.MapUnits)
        head.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyAngle,
                                    QgsProperty.fromField("angle"))
        tips.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol([head])))
        extent = QgsRectangle(0, 0, size, size)
        reference = ink_mask(render([source], extent, (size, size)))
        drawn = ink_mask(render([tips, lines], extent, (size, size)))
        # Straight-sided heads (QGIS bends a curved arrow's head sides).
        assert reference and mask_difference(reference, drawn) < 0.06, \
            (curved, repeated, head_type, arrow_type)


def test_arrow_shadow_is_covered_by_later_arrows(plugin):
    """QGIS fills each arrow with all fill layers before the next one: the
    shadow of a later arrow hides an earlier arrow (painter order)."""
    from q2vt_plugin.src.core.rules_exporter import RulesExporter  # pylint: disable=import-error
    first = QgsGeometry.fromWkt("POLYGON((0 0, 10 0, 10 10, 0 10, 0 0))")
    second = QgsGeometry.fromWkt("POLYGON((12 2, 22 2, 22 12, 12 12, 12 2))")
    shifts = ((-3.0, 0.0), (0.0, 0.0))  # shadow 3 units left, then the fill
    shadow = RulesExporter._painter_visible([first, second], shifts, 0)
    top = RulesExporter._painter_visible([first, second], shifts, 1)
    # Drawn: shadow 1 (x -3..7), fill 1 (0..10), shadow 2 (9..19), fill 2.
    assert top[0].area() == pytest.approx(100.0 - 8.0)  # x 9..10 under shadow 2
    assert top[1].area() == pytest.approx(100.0)
    assert shadow[0].area() == pytest.approx(30.0)  # x -3..0 beside fill 1
    assert shadow[1].area() == pytest.approx(30.0)  # x 9..12 beside fill 2


def test_repeated_arrows_keep_nearly_collinear_vertices(plugin, tmp_path):
    """QGIS draws one arrow per segment from the stored vertices; the base
    layer's simplification must not merge nearly collinear segments."""
    from qgis.core import QgsArrowSymbolLayer
    wkt = "LINESTRING(-100 0, -60 0.001, -20 0, 20 0.001, 60 0, 100 0.001)"
    layer = _layer("LineString", [wkt], str(tmp_path / "arrows.gpkg"))
    arrow = QgsArrowSymbolLayer()
    arrow.setIsCurved(False)
    arrow.setIsRepeated(True)
    for name, value in (("ArrowWidth", 4), ("ArrowStartWidth", 4), ("HeadLength", 12),
                        ("HeadThickness", 8)):
        getattr(arrow, f"set{name}")(value)
        getattr(arrow, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    arrow.setSubSymbol(_fill())
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([arrow])))
    reference = ink_mask(render([layer], EXTENT, (240, 240)))
    outputs, _ = _export(layer, tmp_path, 15, 16)
    for out, rule in outputs:
        out.setRenderer(QgsSingleSymbolRenderer(rule.rule.symbol().clone()))
    assert [r.recipe.kind for _, r in outputs] == ["arrow_polygons"]
    assert next(iter(outputs))[0].featureCount() == 5  # one arrow per segment
    ours = ink_mask(render([o for o, _ in outputs], EXTENT, (240, 240)))
    assert mask_difference(reference, ours) < 0.02


# -- inner effects -------------------------------------------------------------
def _embossed_line(color="#e4a9a9", width=3.6):
    from qgis.core import QgsApplication, QgsDrawSourceEffect, QgsEffectStack
    line = QgsSimpleLineSymbolLayer(QColor(color), width)
    shadow = QgsApplication.paintEffectRegistry().createEffect("innerShadow", {
        "blend_mode": "13", "blur_level": "2.645", "blur_unit": "MM", "color": "0,0,0,255",
        "enabled": "1", "offset_angle": "135", "offset_distance": "2", "offset_unit": "MM",
        "opacity": "1"})
    stack = QgsEffectStack()
    stack.appendEffect(QgsDrawSourceEffect())
    stack.appendEffect(shadow)
    line.setPaintEffect(stack)
    return line


def test_inner_shadow_strips_are_coloured_like_qgis(plugin):
    """Across a straight line each strip has QGIS's colour at its offset; the
    lit and the shaded side differ, and turn with the line's direction."""
    from fidelity import line_effects as fx
    line = _embossed_line()
    width = 3.6 * 96 / 25.4
    spec = fx.inner_effect_spec(line, width)
    assert spec["buckets"] == 36 and len(spec["strips"]) == math.ceil(width)
    assert len(spec["caps"]) == 36 and sorted(spec["order"]) == list(range(len(spec["strips"])))

    def level(rgba):
        return sum(float(v) for v in rgba[5:-1].split(",")[:3])
    east, west = spec["colors"][0], spec["colors"][18]
    # Light from the upper left (135 degrees): going east the left (upper)
    # edge is in shadow, going west the same screen edge is the right one.
    assert level(east[0]) < level(east[-1]) - 100
    assert level(west[-1]) < level(west[0]) - 100
    assert abs(level(east[0]) - level(west[-1])) < 20


def test_inner_shadow_line_exports_strips_by_direction(plugin, tmp_path):
    """An embossed line becomes runs by screen direction drawn as an ends
    layer and strips coloured per direction; nothing is reported missing."""
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    layer = _layer("LineString", [ZIGZAG], str(tmp_path / "emboss.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([_embossed_line()])))
    reset_project(layer)
    out = tmp_path / "out"
    out.mkdir()
    exporter = QGIS2VectorTiles(min_zoom=15, max_zoom=16, extent=EXTENT, output_dir=str(out),
                                serve=False)
    result = exporter.convert_project_to_vector_tiles()
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    lines = [l for l in style["layers"] if l["type"] == "line"]
    ends = [l for l in lines if l["id"].endswith("_ends")]
    strips = [l for l in lines if re.search(r"_in\d+$", l["id"])]
    tiles = [l for l in ends if l["minzoom"] < 17]  # (and per overzoom from 17)
    assert len(tiles) == 2 and len(ends) > 2  # one per zoom
    assert len(strips) == len(ends) * math.ceil(3.6 * 96 / 25.4)
    assert all(s["paint"]["line-color"][0] == "match" for s in strips)
    assert {s["paint"].get("line-offset", 0) for s in strips} != {0}
    report = json.load(open(os.path.join(result, "fidelity_report.json"), encoding="utf-8"))
    assert not [d for d in report["diagnostics"] if d["code"] == "Q2VT_UNSUPPORTED_EFFECT"]


# -- screen-unit offsets -------------------------------------------------------
COMB = "POLYGON((-80 -60, 80 -60, 80 60, 2 60, 2 -10, -2 -10, -2 60, -80 60, -80 -60))"


@pytest.mark.parametrize("offset", [1.6, -1.6])
def test_screen_polygon_outline_offset_is_qgis_buffered_ring_per_band(plugin, tmp_path, offset):
    """A millimetre offset of a polygon outline (a band along the edge) is
    exported as each ring buffered like QGIS, per eighth of a zoom: MapLibre's
    line-offset overlapped at every vertex (a translucent band blended twice)
    and did not bridge the 4 m inlet, narrower than twice the offset."""
    layer = _layer("Polygon", [COMB], str(tmp_path / "comb.gpkg"))
    line = QgsSimpleLineSymbolLayer(QColor(90, 168, 100), 3.2)
    line.setOffset(offset)
    line.setPenJoinStyle(Qt.PenJoinStyle.RoundJoin)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([line])))
    outputs, _ = _export(layer, tmp_path, 15, 16)
    bands = [(o, r) for o, r in outputs if r.recipe is not None]
    assert {r.recipe.kind for _, r in bands} == {"polygon_offset"}
    within = [(o, r) for o, r in bands if r.recipe.param("rhr") is None]
    assert len(within) == 16 and len(bands) - len(within) <= 1
    for out, rule in within[::5]:
        assert rule.rule.symbol().symbolLayer(0).offset() == 0
        zoom = (rule.visibility.min_zoom + rule.visibility.max_zoom) / 2
        assert rule.recipe.param("offset") == pytest.approx(
            offset / 1000 * 295828763.7957775 / 2 ** zoom, rel=1e-3)
        # The exported rings are lines: drawn with the band's line layer.
        out.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([rule.rule.symbol().symbolLayer(0).clone()])))
        view = _view(zoom)
        reference = ink_mask(render([layer], view, (240, 240)))
        ours = ink_mask(render([out], view, (240, 240)))
        assert mask_difference(reference, ours) < 0.08, zoom


def test_screen_offset_is_the_qgis_offset_curve_per_band(plugin, tmp_path):
    """A 3 mm offset of a zigzag line (loops in MapLibre's line-offset) is
    exported as offset curves per eighth of a zoom, each drawn like QGIS at
    its band's scale."""
    layer = _layer("LineString", [ZIGZAG], str(tmp_path / "offset.gpkg"))
    line = QgsSimpleLineSymbolLayer(QColor("black"), 0.4)
    line.setOffset(3.0)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([line])))
    outputs, _ = _export(layer, tmp_path, 15, 16)
    bands = [(o, r) for o, r in outputs if r.recipe is not None]
    assert {r.recipe.kind for _, r in bands} == {"line_offset"}
    assert len(bands) == 16  # two zooms in eighths (corners loop at both)
    for out, rule in bands[::5]:
        zoom = (rule.visibility.min_zoom + rule.visibility.max_zoom) / 2
        offset = rule.recipe.param("offset")
        assert offset == pytest.approx(3.0 / 1000 * 295828763.7957775 / 2 ** zoom, rel=1e-3)
        out.setRenderer(QgsSingleSymbolRenderer(rule.rule.symbol().clone()))
        view = _view(zoom)
        reference = ink_mask(render([layer], view, (240, 240)))
        ours = ink_mask(render([out], view, (240, 240)))
        assert mask_difference(reference, ours) < 0.08, zoom


@pytest.mark.parametrize("kind", ["LineString", "LineAtEdge", "Polygon", "PolygonWithHole"])
def test_screen_offsets_from_zoom_zero_pass_strict_mode(plugin, tmp_path, kind):
    """Exported from zoom 0 (the Publish defaults), a 1.5 mm offset is
    hundreds of km at the first zooms: offset curves there lie outside the
    4 km export extent and every eighth-of-a-zoom band reported a rule
    missing from the map (Strict mode failed). Those zooms are drawn with
    the browser's line offset, mitred like QGIS's offset curve; the offset
    curves start where the offset may reach into the extent. A line at the
    extent's edge offset outwards, or rings collapsed by an inward offset,
    are not drawn inside the extent by QGIS either: no loss reported, and
    no native rings drawn where QGIS's rings collapse."""
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    cx, cy = 2121000.0, 6021000.0

    def ring(points):
        return "(" + ", ".join(f"{cx + x} {cy + y}" for x, y in points) + ")"
    zigzag = [(-300, -100), (-150, 50), (0, -100), (150, 50), (300, -100)]
    if kind == "LineAtEdge":  # upwards, 60-100 m inside the right edge, offset to the right
        wkt = "LINESTRING" + ring([(1900, -300), (1940, -150), (1900, 0), (1940, 150), (1900, 300)])
    elif kind == "LineString":
        wkt = "LINESTRING" + ring(zigzag)
    else:
        wkt = "POLYGON(" + ring(zigzag + [(300, 200), (-300, 200), (-300, -100)])
        if kind == "PolygonWithHole":
            wkt += ", " + ring([(-50, 100), (50, 100), (50, 150), (-50, 150), (-50, 100)])
        wkt += ")"
    polygon = kind.startswith("Polygon")
    layer = _layer("Polygon" if polygon else "LineString", [wkt], str(tmp_path / "offset.gpkg"))
    line = QgsSimpleLineSymbolLayer(QColor("red"), 0.6)
    line.setOffset(1.5)
    layer.setRenderer(QgsSingleSymbolRenderer(
        QgsFillSymbol([line]) if polygon else QgsLineSymbol([line])))
    reset_project(layer)
    out = tmp_path / "out"
    out.mkdir()
    extent = QgsRectangle(cx - 2000, cy - 2000, cx + 2000, cy + 2000)
    exporter = QGIS2VectorTiles(min_zoom=0, max_zoom=16, extent=extent, output_dir=str(out),
                                feedback=QgsProcessingFeedback(), serve=False, fidelity_mode=1)
    export_dir = exporter.convert_project_to_vector_tiles()  # Strict: raises on a loss
    assert not [d for d in exporter.diagnostics.items if d.code == "Q2VT_RULE_OUTPUT_EMPTY"]
    with open(os.path.join(export_dir, "style", "style.json"), encoding="utf-8") as handle:
        style = json.load(handle)
    lines = [l for l in style["layers"] if l["type"] == "line"]
    native = [l for l in lines if "line-offset" in l["paint"]]
    bands = [l for l in lines if "line-offset" not in l["paint"]]
    # 1.5 mm = 5.67 px is at most the 4 km extent's diagonal from zoom 7 on.
    first = math.ceil(math.log2(1.5 * 96 / 25.4 * WORLD / 512 / math.hypot(4000, 4000)))
    assert first == 7
    assert {l["layout"]["line-join"] for l in native} == {"miter"}
    if kind == "Polygon":
        # The rings buffered inwards to nothing below 11.75: QGIS draws
        # none there, and nor does the browser.
        assert min(l.get("minzoom", 0) for l in lines) == 11.75
        return
    early = [l for l in native if l.get("minzoom", 0) == 0]  # lines, holes
    assert [l["maxzoom"] for l in early] == [first]
    assert min(l["minzoom"] for l in bands) >= first


@pytest.mark.parametrize("kind", ["Interval", "Vertex", "Hashes", "PolygonVertex",
                                  "PolygonInterval", "Shapeburst"])
def test_screen_offset_markers_from_zoom_zero_pass_strict_mode(plugin, tmp_path, kind):
    """Marker and hash lines with a 1.5 mm offset, and a 2 mm shapeburst,
    exported from zoom 0: their positions (bands) per zoom lie on an offset
    line hundreds of km away at the first zooms, wholly outside the 4 km
    extent (one rule missing from the map per zoom or band, Strict mode
    failed), and the shapeburst's per-step polygons were too small for a
    tile unit (source layers without tiles). Until the distance is at most
    half the extent's side the markers sit on the line, offset on screen
    (rings an inward offset collapses keep none), and the shapeburst is one
    band set."""
    from qgis.core import (QgsHashedLineSymbolLayer, QgsMarkerLineSymbolLayer,  # pylint: disable=import-outside-toplevel
                           QgsMarkerSymbol, QgsShapeburstFillSymbolLayer,
                           QgsSimpleMarkerSymbolLayer)
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    cx, cy = 2121000.0, 6021000.0

    def ring(points):
        return "(" + ", ".join(f"{cx + x} {cy + y}" for x, y in points) + ")"
    zigzag = [(-300, -100), (-150, 50), (0, -100), (150, 50), (300, -100)]
    polygon = kind.startswith("Polygon") or kind == "Shapeburst"
    if polygon:
        wkt = "POLYGON(" + ring(zigzag + [(300, 200), (-300, 200), (-300, -100)]) + ", " + \
            ring([(-50, 100), (50, 100), (50, 150), (-50, 150), (-50, 100)]) + ")"
    else:
        wkt = "LINESTRING" + ring(zigzag)
    layer = _layer("Polygon" if polygon else "LineString", [wkt], str(tmp_path / "markers.gpkg"))
    if kind == "Shapeburst":
        symbol_layer = QgsShapeburstFillSymbolLayer(QColor("red"), QColor("white"))
        symbol_layer.setUseWholeShape(False)
        symbol_layer.setMaxDistance(2.0)
        symbol_layer.setDistanceUnit(Qgis.RenderUnit.Millimeters)
    else:
        if kind == "Hashes":
            symbol_layer = QgsHashedLineSymbolLayer()
        else:
            symbol_layer = QgsMarkerLineSymbolLayer()
            symbol_layer.setSubSymbol(QgsMarkerSymbol([QgsSimpleMarkerSymbolLayer(
                Qgis.MarkerShape.Circle, 1.2)]))
        symbol_layer.setInterval(3.0)
        if kind.endswith("Vertex"):
            symbol_layer.setPlacements(Qgis.MarkerLinePlacement.Vertex)
        symbol_layer.setOffset(1.5)
    layer.setRenderer(QgsSingleSymbolRenderer(
        QgsFillSymbol([symbol_layer]) if polygon else QgsLineSymbol([symbol_layer])))
    reset_project(layer)
    out = tmp_path / "out"
    out.mkdir()
    extent = QgsRectangle(cx - 2000, cy - 2000, cx + 2000, cy + 2000)
    exporter = QGIS2VectorTiles(min_zoom=0, max_zoom=16, extent=extent, output_dir=str(out),
                                feedback=QgsProcessingFeedback(), serve=False, fidelity_mode=1)
    export_dir = exporter.convert_project_to_vector_tiles()  # Strict: raises on a loss
    assert not [d for d in exporter.diagnostics.items
                if d.code in ("Q2VT_RULE_OUTPUT_EMPTY", "Q2VT_SOURCE_LAYER_MISSING")]
    with open(os.path.join(export_dir, "style", "style.json"), encoding="utf-8") as handle:
        drawn = [l for l in json.load(handle)["layers"] if l.get("source-layer")]
    # 1.5 mm = 5.67 px (the shapeburst's 2 mm = 7.56 px) is at most half
    # the 4 km extent's side from zoom 8 (9) on.
    distance = 2.0 if kind == "Shapeburst" else 1.5
    first = math.ceil(math.log2(distance * 96 / 25.4 * WORLD / 512 / 2000))
    assert first == (9 if kind == "Shapeburst" else 8)
    early = [l for l in drawn if l.get("minzoom", 0) < first]
    assert early and max(l["maxzoom"] for l in early) == first
    if kind.endswith("Vertex") or kind == "Shapeburst":  # one rule (band set) below them
        assert {(l.get("minzoom", 0), l["maxzoom"]) for l in early} == {(0, first)}
