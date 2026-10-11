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
    features = []
    for one in [wkt] if isinstance(wkt, str) else wkt:
        feature = QgsFeature(layer.fields())
        feature.setGeometry(QgsGeometry.fromWkt(one))
        if values:
            feature.setAttributes(values)
        features.append(feature)
    layer.dataProvider().addFeatures(features)
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


def _total_overzoom(rules, cost):
    """Summed ``cost(rule)`` of the overzoom copies (attribute "v")."""
    return sum(cost(rule) for rule in rules if rule.get_attr("v") is not None)


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


def test_shapeburst_out_of_budget_keeps_its_last_bands_past_the_last_tiles(
        plugin, tmp_path, monkeypatch):
    """When the exported zooms use up the output budget, the last band set
    goes on past the last tiles (as before the overzoom bands), not the
    inner colour that stands for bands narrower than the tiles' grid."""
    from q2vt_plugin.src.core.materializer import SymbolMaterializer
    monkeypatch.setattr(SymbolMaterializer, "_band_count", classmethod(lambda cls, colors: 1))
    monkeypatch.setattr(SymbolMaterializer, "MAX_PATTERN_ELEMENTS", 17 * 4)  # zooms 0-16 only
    burst = QgsShapeburstFillSymbolLayer()
    burst.setUseWholeShape(False)
    burst.setMaxDistance(3)
    burst.setDistanceUnit(MM)
    burst.setColor(QColor("blue"))
    burst.setColor2(QColor("yellow"))
    layer = _layer("Polygon", SQUARE, str(tmp_path / "sb.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([burst])))
    rules, diags = _flatten(tmp_path, layer)
    last = _at(rules, 16.9)
    assert len(last) == 1 and last[0].recipe.kind == "color_bands"
    for zoom in (17.5, 19.5, 22.0):
        assert _at(rules, zoom) == last
    assert last[0].visibility.max_zoom is None
    assert diags.by_code("Q2VT_PATTERN_BUDGET")
    assert not diags.by_code("Q2VT_GRADIENT_APPROXIMATE")


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


def test_overzoom_copies_stay_within_the_last_tiles_budget(plugin, tmp_path, monkeypatch):
    """Every overzoom copy is written into the last tiles: a map whose last
    tile zoom is low holds many features in one tile, so the copies stop at
    the budget and the last one serves the zooms above it."""
    from q2vt_plugin.src.core.materializer import SymbolMaterializer
    field = QgsVectorFieldSymbolLayer()
    field.setXAttribute("dx")
    field.setYAttribute("dy")
    field.setScale(6)
    field.setDistanceUnit(MM)
    # Three points in one zoom-16 tile: three lines a copy.
    points = [f"POINT({X + 10 * k} {Y})" for k in range(3)]
    layer = _layer("Point", points, str(tmp_path / "fb.gpkg"), ["dx", "dy"], [3.0, 2.0])
    layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol([field])))
    monkeypatch.setattr(SymbolMaterializer, "OVERZOOM_TILE_ELEMENTS", 3 * 2)
    rules, diags = _flatten(tmp_path, layer)
    assert sorted(r.get_attr("v") for r in rules if r.get_attr("v") is not None) == [17, 18]
    drawn = _at(rules, 21.5)
    assert len(drawn) == 1 and drawn[0].get_attr("v") == 18
    assert drawn[0].visibility.max_zoom is None
    assert diags.by_code("Q2VT_PATTERN_BUDGET")


def test_point_clusters_past_the_last_tiles_merge_zooms_that_group_alike(plugin, tmp_path):
    """A cluster renderer's grouping is a screen distance (5 mm), so it goes
    on per zoom past the last tiles; zooms that group the points as the
    zoom before extend its rule instead of writing the points again."""
    from qgis.core import QgsPointClusterRenderer
    from fidelity.model import ZoomInterval
    # 5 m apart: one cluster up to early zoom 18.
    layer = _layer("Point", [f"POINT({X} {Y})", f"POINT({X + 5} {Y})"],
                   str(tmp_path / "pc.gpkg"))
    renderer = QgsPointClusterRenderer()
    renderer.setTolerance(5)
    renderer.setToleranceUnit(MM)
    renderer.setEmbeddedRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({})))
    layer.setRenderer(renderer)
    rules, _ = _flatten(tmp_path, layer)
    members = [r for r in rules if r.point_group[1] == "members"
               and dict(r.point_group[3]).get("part") is None]
    # The points alone from the first overzoom written on (none here): once.
    stable = [r for r in rules if dict(r.point_group[3]).get("part") == "stable"]
    assert len(stable) == 1 and stable[0].visibility == ZoomInterval(18.0, None)
    tolerance = dict(stable[0].point_group[3])["stable"]
    assert {dict(r.point_group[3]).get("stable") for r in members if r.get_attr("v")} == {tolerance}

    def drawn(zoom):
        found = _at(members, zoom)
        assert len(found) == 1
        return found[0]
    # Zoom 17 groups like zoom 16: its rule goes on.
    assert drawn(17.5) is drawn(16.5) and drawn(16.5).get_attr("v") is None
    # The pair splits within zoom 18; the zooms above group like its end.
    split = drawn(22.0)
    assert split.get_attr("v") == 18 and split.visibility.max_zoom is None
    assert drawn(19.5) is drawn(21.0) is split and 18.0 < split.visibility.min_zoom < 19.0
    assert drawn(18.0) is not split
    assert not [r for r in members if (r.get_attr("v") or 0) > 18]


def test_map_unit_markers_past_the_last_tiles_keep_an_image_that_fits(plugin, tmp_path):
    """Rotated map-unit markers along a line (arrowheads 100-120 map units
    off each 10 m marker, every 150 m) exported 0-16: their averaged angles
    are kept per zoom past the last tiles, one style a zoom. Drawn at its
    own zoom, the image doubled at every zoom and outgrew the sprite canvas
    from zoom 19: those styles were dropped and the markers vanished. Past
    the zoom where the marker got large, the copies share that zoom's image,
    scaled up with the map like the one image of a style drawn on."""
    import json
    import os
    from qgis.core import QgsRectangle
    from qgis.PyQt.QtCore import QPointF
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles
    marker = []
    for offset in (-120, -100, 100, 120):
        head = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.ArrowHeadFilled, 10)
        head.setSizeUnit(Qgis.RenderUnit.MapUnits)
        head.setOffset(QPointF(offset, 0))
        head.setOffsetUnit(Qgis.RenderUnit.MapUnits)
        marker.append(head)
    line = QgsMarkerLineSymbolLayer(True, 150)
    line.setIntervalUnit(Qgis.RenderUnit.MapUnits)
    line.setSubSymbol(QgsMarkerSymbol(marker))
    layer = _layer("LineString", f"LINESTRING({X} {Y}, {X + 700} {Y + 80}, {X + 1500} {Y})",
                   str(tmp_path / "mu.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([line])))
    reset_project(layer)
    out = tmp_path / "out"
    out.mkdir()
    extent = QgsRectangle(X - 300, Y - 300, X + 1800, Y + 400)
    result = QGIS2VectorTiles(min_zoom=0, max_zoom=16, extent=extent, output_dir=str(out),
                              serve=False).convert_project_to_vector_tiles()
    with open(os.path.join(result, "fidelity_report.json"), encoding="utf-8") as handle:
        report = json.load(handle)
    assert not [d for d in report["diagnostics"] if d["code"] == "Q2VT_SPRITE_RENDER_FAILED"]
    with open(os.path.join(result, "style", "style.json"), encoding="utf-8") as handle:
        style = json.load(handle)
    markers = [l for l in style["layers"] if l["type"] == "symbol"
               and "icon-image" in l.get("layout", {})]

    def drawn(zoom):
        return [l for l in markers if l.get("minzoom", 0) <= zoom < l.get("maxzoom", 24)]
    for zoom in (16.5, 17.5, 19.5, 20.5, 22.0, 23.5):
        assert len(drawn(zoom)) == 1, zoom
    over = [l for l in markers if l["minzoom"] >= 18]
    assert len(over) == 5  # one style a zoom (its averaged angles), 18-22
    assert len({l["layout"]["icon-image"] for l in over}) == 1
    with open(os.path.join(result, "style", "sprite", "sprite@2x.json"), encoding="utf-8") as handle:
        image = json.load(handle)[over[0]["layout"]["icon-image"]]
    assert max(image["width"], image["height"]) < 4096
    # The image grows with the map from the zoom it was drawn at.
    assert over[-1]["layout"]["icon-size"][:3] == ["interpolate", ["exponential", 2], ["zoom"]]


def _scale_interval_line(kind, expression):
    """A marker (1 mm dots) or hashed line, interval 10 map units data
    defined by ``expression``."""
    from qgis.core import QgsHashedLineSymbolLayer, QgsProperty, QgsSymbolLayer
    line = QgsMarkerLineSymbolLayer() if kind == "marker" else QgsHashedLineSymbolLayer()
    if kind == "marker":
        dot = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, 1)
        dot.setSizeUnit(MM)
        line.setSubSymbol(QgsMarkerSymbol([dot]))
    line.setPlacements(Qgis.MarkerLinePlacement.Interval)
    line.setInterval(10)
    line.setIntervalUnit(Qgis.RenderUnit.MapUnits)
    line.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyInterval,
                                QgsProperty.fromExpression(expression))
    return line


def _past_last_tiles_only(symbol):
    """A rule-based renderer drawing ``symbol`` from 1:1500 on (about zoom
    17.6: past the last tiles of a 0-16 export)."""
    from qgis.core import QgsRuleBasedRenderer
    root = QgsRuleBasedRenderer.Rule(None)
    rule = QgsRuleBasedRenderer.Rule(symbol)
    rule.setMinimumScale(1500.0)
    rule.setMaximumScale(1.0)
    root.appendChild(rule)
    return QgsRuleBasedRenderer(root)


def test_scale_dependent_interval_copies_share_the_last_tiles_budget(plugin, tmp_path):
    """A marker-line interval that keeps shrinking with the map scale
    (``@map_scale / 200 + 0.1``) doubles the markers of every zoom past the
    last tiles. Exported 0-10, all of those copies are written into the
    zoom-10 tiles: they stop within the last tiles' budget, the last one
    kept serves the zooms above (a hashed line too)."""
    from q2vt_plugin.src.core.materializer import SymbolMaterializer
    wkts = [f"LINESTRING({X} {Y + 40 * k}, {X + 1000} {Y + 40 * k})" for k in range(20)]
    budget = SymbolMaterializer.OVERZOOM_TILE_ELEMENTS

    def flatten(kind, *zooms):
        line = _scale_interval_line(kind, "@map_scale / 200 + 0.1")
        layer = _layer("LineString", wkts, str(tmp_path / f"{kind}{len(zooms)}.gpkg"))
        layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([line])))
        return _flatten(tmp_path, layer, *zooms)
    for kind in ("marker", "hash"):
        rules, diags = flatten(kind, 0, 10)
        placed = [r for r in rules if r.recipe is not None and r.recipe.kind == "marker_points"]
        copies = sorted(r.get_attr("v") for r in placed if r.get_attr("v") is not None)
        # 20 km of lines: about 15 markers at zoom 11, 14 000 at zoom 21.
        assert copies and copies == list(range(11, copies[-1] + 1)) and copies[-1] < 21
        assert _total_overzoom(placed, lambda r: 20000 / r.recipe.param("interval")) <= budget
        last = _at(placed, 22.0)
        assert len(last) == 1 and last[0].get_attr("v") == copies[-1]
        assert last[0].visibility.max_zoom is None
        assert diags.by_code("Q2VT_PATTERN_BUDGET")
    # Exported 14-19, the copies go on to the last zoom (within the budget).
    rules, _ = flatten("marker", 14, 19)
    assert sorted(r.get_attr("v") for r in rules if r.get_attr("v") is not None) == [20, 21, 22]


def test_interval_following_the_map_scale_is_native_past_the_last_tiles(plugin, tmp_path):
    """``@map_scale / 200`` map units is 5 mm on screen at every scale: past
    the last tiles it is the native screen-spaced placement (eighths of a
    zoom on one dataset of the lines), not a copy of the markers per zoom
    doubling at every zoom (the Publish defaults, 0-16, wrote 1.2 million
    markers more into the zoom-16 tiles)."""
    wkts = [f"LINESTRING({X} {Y + 40 * k}, {X + 1000} {Y + 40 * k})" for k in range(20)]
    for kind in ("marker", "hash"):
        layer = _layer("LineString", wkts, str(tmp_path / f"{kind}.gpkg"))
        layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([
            _scale_interval_line(kind, "@map_scale / 200")])))
        rules, diags = _flatten(tmp_path, layer, 0, 16)
        placed = [r for r in rules if r.recipe is not None]
        assert placed and not [r for r in placed if r.get_attr("v") is not None]
        assert {r.get_attr("o") for r in placed} == set(range(17))
        native = [r for r in rules if r.recipe is None]
        assert len({r.output_dataset for r in native}) == 1
        for zoom in (17.0, 18.5, 21.9, 22.5):
            drawn = _at(rules, zoom)
            assert len(drawn) == 1 and drawn[0] in native, zoom
            line = drawn[0].rule.symbol().symbolLayer(0)
            assert line.intervalUnit() == MM and line.interval() == pytest.approx(5.0)
        assert not diags.by_code("Q2VT_PATTERN_BUDGET")


def test_overzoom_only_rules_out_of_budget_keep_their_first_zoom(plugin, tmp_path, monkeypatch):
    """A rule drawn only past the last tiles (a detail rule from 1:1500 on)
    whose first overzoom copy does not fit the last tiles' budget still
    draws that copy at every zoom above (the scale-dependent split raised
    an IndexError; arrows vanished)."""
    from q2vt_plugin.src.core.materializer import SymbolMaterializer
    monkeypatch.setattr(SymbolMaterializer, "OVERZOOM_TILE_ELEMENTS", 5)
    wkts = [f"LINESTRING({X} {Y + 40 * k}, {X + 1000} {Y + 40 * k})" for k in range(20)]
    layer = _layer("LineString", wkts, str(tmp_path / "scale.gpkg"))
    layer.setRenderer(_past_last_tiles_only(QgsLineSymbol([
        _scale_interval_line("marker", "@map_scale / 200 + 0.1")])))
    rules, diags = _flatten(tmp_path, layer, 0, 10)
    placed = [r for r in rules if r.recipe is not None]
    assert len(placed) == 1 and placed[0].get_attr("v") == 17
    assert placed[0].visibility.max_zoom is None and _at(rules, 22.0) == placed
    assert diags.by_code("Q2VT_PATTERN_BUDGET")
    arrow = QgsArrowSymbolLayer()
    arrow.setIsCurved(False)
    arrow.setIsRepeated(False)
    for setter in ("setArrowWidth", "setArrowStartWidth"):
        getattr(arrow, setter)(1)
        getattr(arrow, setter + "Unit")(MM)
    layer = _layer("LineString", wkts, str(tmp_path / "arrows.gpkg"))
    layer.setRenderer(_past_last_tiles_only(QgsLineSymbol([arrow])))
    rules, diags = _flatten(tmp_path, layer, 0, 10)
    assert rules and {r.get_attr("v") for r in rules} == {17}
    for zoom in (17.7, 20.0, 23.0):
        assert _at(rules, zoom), zoom
    assert diags.by_code("Q2VT_PATTERN_BUDGET")
    # A shapeburst fading to transparent drew its (transparent) inner colour.
    burst = QgsShapeburstFillSymbolLayer()
    burst.setUseWholeShape(False)
    burst.setMaxDistance(3)
    burst.setDistanceUnit(MM)
    burst.setColor(QColor("blue"))
    burst.setColor2(QColor(255, 255, 255, 0))
    squares = [f"POLYGON(({X + 60 * k} {Y}, {X + 60 * k + 50} {Y}, {X + 60 * k + 50} {Y + 50}, "
               f"{X + 60 * k} {Y + 50}, {X + 60 * k} {Y}))" for k in range(20)]
    layer = _layer("Polygon", squares, str(tmp_path / "bursts.gpkg"))
    layer.setRenderer(_past_last_tiles_only(QgsFillSymbol([burst])))
    rules, diags = _flatten(tmp_path, layer, 0, 16)
    assert rules and {r.get_attr("v") for r in rules} == {17}
    for zoom in (17.7, 20.0, 23.0):
        drawn = _at(rules, zoom)
        assert drawn and all(r.recipe is not None and r.recipe.kind == "color_bands"
                             for r in drawn), zoom
    assert diags.by_code("Q2VT_PATTERN_BUDGET")


def test_arrows_past_the_polygon_budget_go_on_natively(plugin, tmp_path, monkeypatch):
    """An overzoom whose arrow polygons do not fit the last tiles' budget is
    drawn natively (a line and head markers, fewer features) if that fits,
    and so are the zooms above it: their widths stay QGIS's instead of the
    last exported band growing with the map."""
    from q2vt_plugin.src.core.materializer import SymbolMaterializer
    monkeypatch.setattr(SymbolMaterializer, "OVERZOOM_TILE_ELEMENTS", 150)
    arrow = QgsArrowSymbolLayer()
    arrow.setIsCurved(False)
    arrow.setIsRepeated(False)
    for setter in ("setArrowWidth", "setArrowStartWidth"):
        getattr(arrow, setter)(1)
        getattr(arrow, setter + "Unit")(MM)
    # 20 arrows in one zoom-10 tile: a band set of polygons does not fit 150
    # features, a line, its outline and a head marker each (60) do.
    layer = _layer("LineString", [f"LINESTRING({X} {Y + 40 * k}, {X + 300} {Y + 40 * k})"
                                  for k in range(20)], str(tmp_path / "arrows.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([arrow])))
    rules, diags = _flatten(tmp_path, layer, 0, 10)
    over = [r for r in rules if r.get_attr("v") is not None]
    assert over and all(r.recipe.param("part") in ("body", "heads") for r in over)
    assert len(over) * 20 <= 150
    zooms = sorted({r.get_attr("v") for r in over})
    assert zooms == list(range(11, zooms[-1] + 1))
    last = _at(rules, 22.0)
    assert last and {r.get_attr("v") for r in last} == {zooms[-1]}
    assert all(r.visibility.max_zoom is None for r in last)
    assert diags.by_code("Q2VT_PATTERN_BUDGET")


def test_arrow_and_shapeburst_copies_stay_within_the_last_tiles_budget(
        plugin, tmp_path, monkeypatch):
    """Arrows and shapeburst bands past the last tiles are written into the
    last tiles like every overzoom copy: they stop at the last tiles'
    budget and the last zoom written serves the zooms above."""
    from q2vt_plugin.src.core.materializer import SymbolMaterializer
    monkeypatch.setattr(SymbolMaterializer, "OVERZOOM_TILE_ELEMENTS", 400)
    arrow = QgsArrowSymbolLayer()
    arrow.setIsCurved(False)
    arrow.setIsRepeated(False)
    for setter in ("setArrowWidth", "setArrowStartWidth"):
        getattr(arrow, setter)(1)
        getattr(arrow, setter + "Unit")(MM)
    # 20 arrows in one zoom-10 tile: 20 polygons a band.
    layer = _layer("LineString", [f"LINESTRING({X} {Y + 40 * k}, {X + 300} {Y + 40 * k})"
                                  for k in range(20)], str(tmp_path / "arrows.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([arrow])))
    rules, diags = _flatten(tmp_path, layer, 0, 10)
    over = [r for r in rules if r.get_attr("v") is not None]
    zooms = sorted({r.get_attr("v") for r in over})
    assert over and len(over) * 20 <= 400 and zooms == list(range(11, zooms[-1] + 1))
    last = _at(rules, 22.0)
    assert last and {r.get_attr("v") for r in last} == {zooms[-1]}
    assert all(r.visibility.max_zoom is None for r in last)
    assert diags.by_code("Q2VT_PATTERN_BUDGET")
    burst = QgsShapeburstFillSymbolLayer()
    burst.setUseWholeShape(False)
    burst.setMaxDistance(3)
    burst.setDistanceUnit(MM)
    monkeypatch.setattr(SymbolMaterializer, "_band_count", classmethod(lambda cls, colors: 1))
    squares = [f"POLYGON(({X + 50 * k} {Y}, {X + 50 * k + 40} {Y}, {X + 50 * k + 40} {Y + 40}, "
               f"{X + 50 * k} {Y + 40}, {X + 50 * k} {Y}))" for k in range(100)]
    layer = _layer("Polygon", squares, str(tmp_path / "bursts.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([burst])))
    rules, diags = _flatten(tmp_path, layer, 0, 10)
    # 100 polygons a band set, two sets a zoom: two zooms at most.
    over = [r for r in rules if r.get_attr("v") is not None]
    assert len(over) * 100 <= 400
    last = _at(rules, 22.0)
    assert len(last) == 1 and last[0].visibility.max_zoom is None
    assert diags.by_code("Q2VT_PATTERN_BUDGET")


def test_overzoom_budget_is_shared_by_every_rule_of_the_last_tiles(plugin, tmp_path, monkeypatch):
    """Three vector field symbol layers on the same points: their overzoom
    copies go into the same last tiles, so they share one budget (each got
    its own, three times what a tile was meant to take)."""
    from q2vt_plugin.src.core.materializer import SymbolMaterializer
    fields = []
    for scale in (4, 6, 8):
        field = QgsVectorFieldSymbolLayer()
        field.setXAttribute("dx")
        field.setYAttribute("dy")
        field.setScale(scale)
        field.setDistanceUnit(MM)
        fields.append(field)
    points = [f"POINT({X + 10 * k} {Y})" for k in range(3)]
    layer = _layer("Point", points, str(tmp_path / "fields.gpkg"), ["dx", "dy"], [3.0, 2.0])
    layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol(fields)))
    monkeypatch.setattr(SymbolMaterializer, "OVERZOOM_TILE_ELEMENTS", 3 * 4)
    rules, diags = _flatten(tmp_path, layer)
    # Three lines a copy, all in one zoom-16 tile: four copies in all.
    assert len([r for r in rules if r.get_attr("v") is not None]) == 4
    drawn = _at(rules, 22.0)
    assert len(drawn) == 3 and all(r.visibility.max_zoom is None for r in drawn)
    assert diags.by_code("Q2VT_PATTERN_BUDGET")


def test_screen_interval_markers_get_sub_zoom_bands_where_lines_hold_several(plugin, tmp_path):
    """Screen-unit interval markers are placed per eighth of a zoom (their
    map spacing halves within a zoom). Where no ring is an interval long,
    every polygon has one marker, at its offset along the ring: one
    placement a zoom, not eight copies of the layer."""
    from fidelity.model import ZoomInterval
    marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, 1)
    marker.setSizeUnit(MM)
    line = QgsMarkerLineSymbolLayer(True, 4)
    line.setIntervalUnit(MM)
    line.setSubSymbol(QgsMarkerSymbol([marker]))
    layer = _layer("Polygon", SQUARE, str(tmp_path / "rings.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([line])))
    rules, _ = _flatten(tmp_path, layer)
    placed = [r for r in rules if r.recipe is not None]

    def bands(zoom):
        return [r for r in placed if r.get_attr("o") == zoom and r.get_attr("v") is None]
    # The 160 m ring is shorter than 4 mm up to zoom 11 (at its end).
    for zoom in range(0, 12):
        assert [r.visibility for r in bands(zoom)] == [ZoomInterval(float(zoom), zoom + 1.0)]
    for zoom in range(12, 17):
        assert len(bands(zoom)) == 8
