"""Screen sizes materialized as geometry past the last tile zoom.

A map exported with tiles 0-16 (the Publish window's default) is viewed up to
zoom 22: the browser overzooms the zoom-16 tiles. Sizes in mm/px converted to
map units per zoom must go on per zoom there, or the geometry made for zoom
16 doubles on screen at every zoom in.
"""

import math

import pytest
from qgis.core import (Qgis, QgsArrowSymbolLayer, QgsDrawSourceEffect, QgsEffectStack,
                       QgsFeature, QgsField, QgsFillSymbol, QgsGeometry, QgsLineSymbol,
                       QgsMarkerLineSymbolLayer, QgsMarkerSymbol, QgsOuterGlowEffect,
                       QgsPointPatternFillSymbolLayer, QgsProcessingFeedback,
                       QgsShapeburstFillSymbolLayer, QgsSimpleLineSymbolLayer,
                       QgsSimpleMarkerSymbolLayer, QgsSingleSymbolRenderer,
                       QgsVectorFieldSymbolLayer, QgsVectorLayer)
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor

from q2vt_fixtures import reset_project, to_geopackage

MM = Qgis.RenderUnit.Millimeters
X, Y = 2120000.0, 6020000.0
SQUARE = f"POLYGON(({X} {Y}, {X + 40} {Y}, {X + 40} {Y + 40}, {X} {Y + 40}, {X} {Y}))"
LINE = f"LINESTRING({X} {Y}, {X + 300} {Y + 20}, {X + 600} {Y})"


def _layer(kind, wkt, path, fields=None, values=None):
    layer = QgsVectorLayer(f"{kind}?crs=EPSG:3857", "src", "memory")
    layer.dataProvider().addAttributes([QgsField(f, QVariant.Double) for f in fields or []])
    layer.updateFields()
    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromWkt(wkt))
    if values:
        feature.setAttributes(values)
    layer.dataProvider().addFeatures([feature])
    return to_geopackage(layer, path)


def _flatten(tmp_path, layer, low=0, high=16, **kwargs):
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener
    from fidelity.diagnostics import DiagnosticCollector
    reset_project(layer)
    diags = DiagnosticCollector()
    rules = RulesFlattener(low, high, str(tmp_path), QgsProcessingFeedback(), diags,
                           **kwargs).flatten_all_rules()
    return rules, diags


def _at(rules, zoom):
    """The rules drawn at ``zoom``."""
    return [r for r in rules if r.visibility is not None and r.visibility.contains(zoom)]


def _per_mm(zoom):
    """EPSG:3857 metres per screen mm at ``zoom``."""
    from fidelity.zoom import zoom_to_scale
    return zoom_to_scale(zoom) / 1000.0


def test_screen_unit_arrows_keep_their_width_past_the_last_tiles(plugin, tmp_path):
    arrow = QgsArrowSymbolLayer()
    arrow.setIsCurved(False)
    for setter in ("setArrowWidth", "setArrowStartWidth"):
        getattr(arrow, setter)(1.5)
        getattr(arrow, setter + "Unit")(MM)
    arrow.setHeadLength(4)
    arrow.setHeadLengthUnit(MM)
    arrow.setHeadThickness(2)
    arrow.setHeadThicknessUnit(MM)
    arrow.subSymbol().symbolLayer(0).setStrokeStyle(Qt.PenStyle.NoPen)

    def flatten(*zooms):
        layer = _layer("LineString", LINE, str(tmp_path / f"a{len(zooms)}.gpkg"))
        layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([arrow.clone()])))
        return _flatten(tmp_path, layer, *zooms)[0]
    rules = flatten()
    # An eighth of a zoom in the tiles' range (within +-4.5 % of QGIS's
    # 1.5 mm), a quarter past it (+-9 %).
    for zoom, rel in ((16.5, 0.045), (17.5, 0.095), (19.06, 0.095), (19.94, 0.095)):
        drawn = _at(rules, zoom)
        assert len(drawn) == 1 and drawn[0].recipe.kind == "arrow_polygons"
        width = drawn[0].recipe.param("sizes")[1]
        assert width == pytest.approx(1.5 * _per_mm(zoom), rel=rel)
        assert drawn[0].get_attr("o") == drawn[0].get_attr("i") == min(int(zoom), 16)
    # From zoom 20 the body gets narrower than two units of the zoom-16
    # tiles' geometry grid (4 px at zoom 21): per zoom, its centre line drawn
    # with its width and a head marker at the tip.
    for zoom in (20.5, 21.0, 22.0):
        drawn = _at(rules, zoom)
        assert sorted(r.recipe.param("part") for r in drawn) == ["body", "heads"]
        assert {r.get_attr("v") for r in drawn} == {int(zoom)}
        body = next(r for r in drawn if r.recipe.param("part") == "body")
        line = body.rule.symbol().symbolLayer(0)
        assert line.layerType() == "SimpleLine" and line.widthUnit() == MM
        assert line.width() == pytest.approx(1.5)
        # The body ends at the head's base at the end of the zoom.
        assert body.recipe.param("sizes")[2] == pytest.approx(4 * _per_mm(int(zoom) + 1))
        head = next(r for r in drawn if r.recipe.param("part") == "heads")
        marker = head.rule.symbol().symbolLayer(0)
        assert marker.layerType() == "EllipseMarker"
        assert (marker.symbolWidth(), marker.symbolHeight()) == pytest.approx((4, 4))
        assert (marker.offset().x(), marker.offset().y()) == pytest.approx((0, 2))
    assert all(r.visibility.max_zoom is None for r in _at(rules, 22.0))
    # Exported to zoom 19: the overzoom starts at 20 (nothing changes below).
    narrow = flatten(14, 19)
    assert [r.get_attr("v") for r in _at(narrow, 19.5)] == [None]
    assert _at(narrow, 20.5)[0].get_attr("v") == 20


def test_arrow_output_budget_goes_to_the_tiles_zooms_first(plugin, tmp_path, monkeypatch):
    """The band steps of the exported zooms are chosen from those zooms
    alone; the overzoom gets what is left of the output budget."""
    from q2vt_plugin.src.core.materializer import SymbolMaterializer
    arrow = QgsArrowSymbolLayer()
    arrow.setIsCurved(False)
    arrow.setIsRepeated(False)

    def bands(budget):
        monkeypatch.setattr(SymbolMaterializer, "MAX_PATTERN_ELEMENTS", budget)
        layer = _layer("LineString", LINE, str(tmp_path / f"b{budget}.gpkg"))
        layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([arrow.clone()])))
        rules = _flatten(tmp_path, layer)[0]
        count = {}
        for rule in rules:
            if rule.recipe is not None and rule.get_attr("c") == 2:
                zoom = SymbolMaterializer.rule_zoom(rule)
                count[zoom] = count.get(zoom, 0) + 1
        return count, rules
    # One arrow, zooms 0-16 in eight bands each (136): room for two bands at
    # the two overzooms before the 1 mm body reaches the grid (zoom 19).
    count, rules = bands(17 * 8 + 2 * 2)
    assert [count[z] for z in range(17)] == [8] * 17
    assert [count.get(z) for z in (17, 18, 19)] == [2, 2, None]
    assert {r.recipe.param("part") for r in _at(rules, 19.5)} == {"body", "heads"}
    # No room left: the overzoom is drawn natively from zoom 17.
    count, rules = bands(17 * 8)
    assert [count[z] for z in range(17)] == [8] * 17 and 17 not in count
    assert {r.recipe.param("part") for r in _at(rules, 17.5)} == {"body", "heads"}


def test_curved_arrow_past_the_grid_follows_its_arc(plugin, tmp_path):
    """QGIS's default arrow (curved, 1 mm) far past the last tiles: the
    body's centre line along the arc (fidelity/arrows.py arrow_skeletons,
    checked against QGIS in test_line_shapes.py), not the source line."""
    from qgis.core import QgsRectangle
    from q2vt_plugin.src.core.rules_exporter import RulesExporter
    from fidelity.diagnostics import DiagnosticCollector
    layer = _layer("LineString", LINE, str(tmp_path / "c.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([QgsArrowSymbolLayer()])))
    rules, diags = _flatten(tmp_path, layer)
    assert diags.by_code("Q2VT_SYMBOL_APPROXIMATE")
    # The default fill's outline under the fill's colour: two lines.
    drawn = [r for r in _at(rules, 21.5) if r.recipe.param("part") == "body"]
    assert len(drawn) == 2 and drawn[0].recipe == drawn[1].recipe
    assert drawn[0].recipe.param("curved")
    utils = tmp_path / "utils"
    utils.mkdir()
    extent = QgsRectangle(X - 100, Y - 100, X + 700, Y + 100)
    layers, _ = RulesExporter(drawn[:1], extent, 0, 16, str(utils), 0, QgsProcessingFeedback(),
                              diagnostics=DiagnosticCollector()).export()
    lines = [f.geometry() for f in layers[0].getFeatures()]
    assert len(lines) == 1
    # The arc through the line's three vertices bulges 20 m: its middle is
    # on the circle, far from the source's straight chords.
    centre = lines[0].interpolate(lines[0].length() / 2).asPoint()
    assert centre.y() - Y == pytest.approx(20, abs=0.5)
    # Chords within a unit of the zoom-16 tiles' geometry grid (0.3 m).
    assert 10 < len(list(lines[0].vertices())) < 40


def test_screen_point_pattern_past_the_last_tiles_is_the_native_pattern(plugin, tmp_path):
    marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, 1.6)
    marker.setSizeUnit(MM)
    pattern = QgsPointPatternFillSymbolLayer()
    for name in ("DistanceX", "DistanceY"):
        getattr(pattern, f"set{name}")(4)
        getattr(pattern, f"set{name}Unit")(MM)
    pattern.setSubSymbol(QgsMarkerSymbol([marker]))
    pattern.setClipMode(Qgis.MarkerClipMode.CentroidWithin)
    layer = _layer("Polygon", SQUARE, str(tmp_path / "p.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([pattern])))
    rules, diags = _flatten(tmp_path, layer)
    drawn = _at(rules, 16.9)
    assert len(drawn) == 1 and drawn[0].recipe.kind == "grid_points"
    assert drawn[0].recipe.param("dx") == pytest.approx(4 * _per_mm(16.9375), rel=1e-6)
    for zoom in (17.0, 19.5, 22.0):
        # A grid per zoom would need 4x the markers of the zoom before in
        # every tile: the browser's screen-size pattern keeps the spacing.
        drawn = _at(rules, zoom)
        assert len(drawn) == 1 and drawn[0].recipe is None
        assert drawn[0].rule.symbol().symbolLayer(0).layerType() == "PointPatternFill"
    assert diags.by_code("Q2VT_PATTERN_APPROXIMATE")


def test_screen_unit_shapeburst_keeps_its_width_past_the_last_tiles(plugin, tmp_path):
    burst = QgsShapeburstFillSymbolLayer()
    burst.setUseWholeShape(False)
    burst.setMaxDistance(3)
    burst.setDistanceUnit(MM)
    burst.setColor(QColor("blue"))
    burst.setColor2(QColor("yellow"))

    def flatten(distance):
        burst.setMaxDistance(distance)
        layer = _layer("Polygon", SQUARE, str(tmp_path / f"s{distance}.gpkg"))
        layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([burst.clone()])))
        return _flatten(tmp_path, layer)
    rules, _ = flatten(3)
    # A quarter of a zoom in the tiles' range (+-9 %), half a zoom past it.
    for zoom, rel in ((16.6, 0.1), (19.1, 0.2), (21.9, 0.2)):
        drawn = _at(rules, zoom)
        assert len(drawn) == 1
        distance = drawn[0].recipe.param("bands")[0].param("distance")
        assert distance == pytest.approx(3 * _per_mm(zoom), rel=rel)
    # 1 mm is narrower than a unit of the zoom-16 tiles' geometry grid from
    # zoom 21 (8 px at zoom 22): the inner colour, without bands.
    rules, diags = flatten(1)
    assert _at(rules, 20.9)[0].recipe.kind == "color_bands"
    for zoom in (21.0, 22.0):
        drawn = _at(rules, zoom)
        assert len(drawn) == 1 and drawn[0].recipe is None
        fill = drawn[0].rule.symbol().symbolLayer(0)
        assert fill.layerType() == "SimpleFill" and fill.color() == QColor("yellow")
    assert diags.by_code("Q2VT_GRADIENT_APPROXIMATE")


def test_screen_offset_vertex_markers_keep_their_offset_past_the_last_tiles(plugin, tmp_path):
    marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, 2.5)
    marker.setSizeUnit(MM)
    line = QgsMarkerLineSymbolLayer()
    line.setPlacements(Qgis.MarkerLinePlacement.Vertex)
    line.setSubSymbol(QgsMarkerSymbol([marker]))
    line.setOffset(2)
    line.setOffsetUnit(MM)
    layer = _layer("Polygon", SQUARE, str(tmp_path / "v.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([line])))
    rules, _ = _flatten(tmp_path, layer)
    for zoom in (16.5, 18.5, 21.5):
        drawn = _at(rules, zoom)
        assert len(drawn) == 1 and drawn[0].recipe.kind == "marker_points"
        assert drawn[0].recipe.param("offset") == pytest.approx(2 * _per_mm(zoom), rel=1e-6)


def test_outer_glow_is_simplified_for_every_viewed_zoom(plugin, tmp_path):
    simple = QgsSimpleLineSymbolLayer(QColor("black"), 0.5)
    stack = QgsEffectStack()
    glow = QgsOuterGlowEffect()
    glow.setSpread(2)
    stack.appendEffect(glow)
    stack.appendEffect(QgsDrawSourceEffect())
    simple.setPaintEffect(stack)
    layer = _layer("LineString", LINE, str(tmp_path / "g.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([simple])))
    rules, _ = _flatten(tmp_path, layer)

    def tolerance(zoom):
        return next(r.recipe.param("tolerance") for r in _at(rules, zoom)
                    if r.recipe is not None and r.recipe.kind == "simplified")
    assert tolerance(17.5) == pytest.approx(tolerance(16.5) / 2)
    assert tolerance(19.5) == pytest.approx(tolerance(16.5) / 8)
    # Past the point where it is finer than the zoom-16 tiles' grid, one
    # rule goes on to the last zoom.
    assert tolerance(22.0) < 2 * math.pi * 6378137.0 / (512 * 2 ** 16) / 8


def test_screen_unit_vector_field_keeps_its_length_past_the_last_tiles(plugin, tmp_path):
    field = QgsVectorFieldSymbolLayer()
    field.setXAttribute("dx")
    field.setYAttribute("dy")
    field.setScale(6)
    field.setDistanceUnit(MM)
    layer = _layer("Point", f"POINT({X} {Y})", str(tmp_path / "f.gpkg"), ["dx", "dy"], [3.0, 2.0])
    layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol([field])))
    rules, _ = _flatten(tmp_path, layer)
    for zoom in (16, 19, 21):
        drawn = _at(rules, zoom + 0.5)
        assert len(drawn) == 1
        factor = 6 * _per_mm(zoom + 0.5)
        assert repr(factor) in drawn[0].pre_generator


def test_fine_map_unit_dashes_are_materialized_from_an_overzoom(plugin, tmp_path):
    simple = QgsSimpleLineSymbolLayer(QColor("black"), 0.6)
    simple.setUseCustomDashPattern(True)
    simple.setCustomDashVector([2, 2])
    simple.setCustomDashPatternUnit(Qgis.RenderUnit.MapUnits)

    def flatten(*zooms):
        layer = _layer("LineString", LINE, str(tmp_path / f"d{len(zooms)}.gpkg"))
        layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([simple.clone()])))
        return _flatten(tmp_path, layer, *zooms)[0]
    rules = flatten()
    # A 4 m period is 3.4 px at zoom 16 (too dense) and 6.7 px at 17.
    assert _at(rules, 16.5)[0].recipe is None
    for zoom in (17.0, 19.5, 22.0):
        drawn = _at(rules, zoom)
        assert len(drawn) == 1 and drawn[0].recipe.kind == "dash_segments"
        assert drawn[0].get_attr("o") == drawn[0].get_attr("i") == 16  # the last tiles' data
    narrow = flatten(14, 19)
    assert _at(narrow, 17.5)[0].get_attr("o") == 17


def test_screen_interval_markers_past_the_last_tiles_are_laid_out_per_band(plugin, tmp_path):
    from q2vt_plugin.src.core.maplibre_converter import LinePropertyExtractor
    marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, 1.5)
    marker.setSizeUnit(MM)
    line = QgsMarkerLineSymbolLayer(True, 4)
    line.setIntervalUnit(MM)
    line.setSubSymbol(QgsMarkerSymbol([marker]))
    layer = _layer("LineString", LINE, str(tmp_path / "i.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([line])))
    rules, _ = _flatten(tmp_path, layer)
    native = [r for r in rules if r.recipe is None]
    # Eighths of every zoom from 17 to 22, all from one dataset of the lines.
    assert len(native) == 6 * 8 and len({r.output_dataset for r in native}) == 1
    assert len({r.rule.description() for r in native}) == len(native)
    assert min(r.visibility.min_zoom for r in native) == 17.0
    assert max(r.visibility.max_zoom for r in native) == 23.0
    interval_px = 4 * 96 / 25.4
    for rule in native:
        band = rule.visibility
        spacing = LinePropertyExtractor.get_marker_line_spacing(
            rule.rule.symbol().symbolLayer(0), (band.min_zoom, band.max_zoom))
        # MapLibre lays the band out at its whole zoom and scales it up to
        # the band's middle, where it is QGIS's spacing.
        middle = (band.min_zoom + band.max_zoom) / 2
        assert spacing * 2 ** (middle - math.floor(band.min_zoom)) == pytest.approx(interval_px)
    # A style over whole zooms keeps the centred layout (0.71x-1.41x).
    assert LinePropertyExtractor.get_marker_line_spacing(line, (14, 17)) == \
        pytest.approx(interval_px / math.sqrt(2))


def test_view_max_zoom_limits_the_overzoom_rules(plugin, tmp_path):
    marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, 2.5)
    marker.setSizeUnit(MM)
    line = QgsMarkerLineSymbolLayer()
    line.setPlacements(Qgis.MarkerLinePlacement.Vertex)
    line.setSubSymbol(QgsMarkerSymbol([marker]))
    line.setOffset(2)
    line.setOffsetUnit(MM)

    def flatten(top):
        layer = _layer("Polygon", SQUARE, str(tmp_path / f"m{top}.gpkg"))
        layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([line.clone()])))
        return _flatten(tmp_path, layer, overzoom_to=top)[0]
    rules = flatten(18)
    assert sorted(r.get_attr("v") for r in rules if r.get_attr("v") is not None) == [17, 18]
    assert _at(rules, 23.0)[0].get_attr("v") == 18  # the last stays open
    rules = flatten(16)
    assert not [r for r in rules if r.get_attr("v") is not None]
