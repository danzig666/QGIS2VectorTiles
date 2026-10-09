"""Rule flattening: visibility intervals, ELSE semantics, project non-mutation."""

import pytest
from qgis.core import QgsFeatureRequest, QgsExpression, QgsProcessingFeedback

from fidelity import zoom as zm
from q2vt_fixtures import categorized, reset_project, rule_based, zoning_layer


@pytest.fixture
def flattener(plugin):
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener  # pylint: disable=import-error
    from fidelity.diagnostics import DiagnosticCollector

    def run(min_zoom=0, max_zoom=14):
        diags = DiagnosticCollector()
        rules = RulesFlattener(min_zoom, max_zoom, "/tmp", QgsProcessingFeedback(),
                               diags).flatten_all_rules()
        return rules, diags
    return run


def _renderer_xml(layer):
    from qgis.core import QgsReadWriteContext
    from qgis.PyQt.QtXml import QDomDocument
    doc = QDomDocument()
    root = doc.createElement("r")
    doc.appendChild(root)
    layer.writeSymbology(root, doc, "", QgsReadWriteContext())
    return doc.toString()


def test_flattening_does_not_mutate_project(flattener):
    layer = categorized(zoning_layer())
    reset_project(layer)
    before = _renderer_xml(layer)
    rules, _ = flattener()
    assert rules
    assert type(layer.renderer()).__name__ == "QgsCategorizedSymbolRenderer"
    assert _renderer_xml(layer) == before


def test_inactive_categories_are_dropped(flattener):
    reset_project(categorized(zoning_layer()))
    rules, _ = flattener()
    filters = " ".join(r.rule.filterExpression() for r in rules)
    assert "'Lk'" not in filters and "'K1'" in filters


def test_single_zoom_rule_has_nonempty_interval(flattener):
    layer = rule_based(zoning_layer(), [
        ("narrow", "", zm.zoom_to_scale(3.2), zm.zoom_to_scale(3.8), True, "255,0,0")])
    reset_project(layer)
    rules, _ = flattener()
    fills = [r for r in rules if r.get_attr("c") == 2]
    assert len(fills) == 1
    rule = fills[0]
    assert (rule.get_attr("o"), rule.get_attr("i")) == (3, 3)
    assert rule.visibility.min_zoom == pytest.approx(3.2)
    assert rule.visibility.max_zoom == pytest.approx(3.8)


def test_inverted_scale_rule_is_reported_and_skipped(flattener):
    layer = rule_based(zoning_layer(), [
        ("inverted", "", zm.zoom_to_scale(8), zm.zoom_to_scale(6), True, "255,0,0")])
    reset_project(layer)
    rules, diags = flattener()
    assert not rules
    assert diags.by_code("Q2VT_ZOOM_EMPTY_INTERVAL")


def _matching_ids(layer, expression):
    request = QgsFeatureRequest(QgsExpression(expression)) if expression else QgsFeatureRequest()
    return sorted(f["id"] for f in layer.getFeatures(request))


def test_else_respects_sibling_scale_ranges_and_disabled_siblings(flattener):
    layer = rule_based(zoning_layer(), [
        ("k1 zoomed in", "\"zone\" = 'K1'", zm.zoom_to_scale(10), 0, True, "255,0,0"),
        ("k2 disabled", "\"zone\" = 'K2'", 0, 0, False, "0,255,0"),
        ("else", "ELSE", 0, 0, True, "0,0,255"),
    ])
    reset_project(layer)
    rules, _ = flattener(0, 14)
    else_rules = sorted((r for r in rules if r.rule.symbol().color().blue() == 255
                         and r.get_attr("c") == 2), key=lambda r: r.visibility.min_zoom)
    assert len(else_rules) == 2
    low, high = else_rules
    # Below zoom 10 the K1 rule is invisible, so ELSE covers every feature
    # (including NULL zones); the disabled K2 rule never excludes anything.
    assert low.visibility.max_zoom == pytest.approx(10)
    assert _matching_ids(layer, low.rule.filterExpression()) == ["A", "B", "C", "D"]
    assert high.visibility.min_zoom == pytest.approx(10)
    assert _matching_ids(layer, high.rule.filterExpression()) == ["B", "C", "D"]
    # Project ELSE rule untouched.
    project_else = layer.renderer().rootRule().children()[2]
    assert project_else.filterExpression() == "ELSE"


def _draw_colors(rules):
    """Fill colours of the renderer styles, bottom first, as the styler emits them."""
    from q2vt_plugin.src.core.tiles_styler import TilesStyler  # pylint: disable=import-error
    ordered = TilesStyler.draw_order(TilesStyler.__new__(TilesStyler), rules)
    return [r.rule.symbol().color().name() for r in ordered
            if r.get_attr("t") == 0 and r.get_attr("c") == 2]


def test_later_rules_draw_on_top(flattener):
    # QGIS draws a feature's rules in tree order: the last rule ends on top.
    layer = rule_based(zoning_layer(), [
        ("first", "", 0, 0, True, "255,0,0"), ("second", "", 0, 0, True, "0,0,255")])
    reset_project(layer)
    rules, _ = flattener()
    assert _draw_colors(rules) == ["#ff0000", "#0000ff"]


def test_rendering_pass_orders_symbol_layers(flattener):
    from qgis.core import QgsSimpleFillSymbolLayer
    from qgis.PyQt.QtGui import QColor
    layer = rule_based(zoning_layer(), [
        ("first", "", 0, 0, True, "255,0,0"), ("second", "", 0, 0, True, "0,0,255")])
    # Rule-based renderers honour passes even without symbol levels.
    layer.renderer().rootRule().children()[0].symbol().symbolLayer(0).setRenderingPass(1)
    reset_project(layer)
    rules, _ = flattener()
    assert _draw_colors(rules) == ["#0000ff", "#ff0000"]

    from qgis.core import QgsFillSymbol, QgsSingleSymbolRenderer
    symbol = QgsFillSymbol.createSimple({"color": "255,0,0"})
    symbol.appendSymbolLayer(QgsSimpleFillSymbolLayer(QColor(0, 128, 0)))
    symbol.symbolLayer(0).setRenderingPass(1)
    renderer = QgsSingleSymbolRenderer(symbol)
    single = zoning_layer()
    single.setRenderer(renderer)
    reset_project(single)
    assert _draw_colors(flattener()[0]) == ["#ff0000", "#008000"]  # levels off
    renderer.setUsingSymbolLevels(True)
    assert _draw_colors(flattener()[0]) == ["#008000", "#ff0000"]


def test_upper_layer_draws_above_lower_layer(flattener):
    top = rule_based(zoning_layer("top"), [("t", "", 0, 0, True, "0,0,255")])
    bottom = rule_based(zoning_layer("bottom"), [("b", "", 0, 0, True, "255,0,0")])
    reset_project(top, bottom)
    root = __import__("qgis.core", fromlist=["QgsProject"]).QgsProject.instance().layerTreeRoot()
    names = [n.layer().name() for n in root.findLayers()]
    rules, _ = flattener()
    colors = _draw_colors(rules)
    expected = {"top": "#0000ff", "bottom": "#ff0000"}
    assert colors == [expected[n] for n in reversed(names)]


def test_last_zoom_of_a_map_scale_split_stays_open(flattener):
    """Övezethatár disappeared beyond the export's maximum zoom + 1: the rule
    split per zoom for @map_scale closed its last zoom too."""
    from qgis.core import (QgsFillSymbol, QgsProperty, QgsSingleSymbolRenderer,
                           QgsSymbolLayer)
    layer = zoning_layer()
    symbol = QgsFillSymbol.createSimple({"color": "red", "outline_width": "0.5"})
    symbol.symbolLayer(0).setDataDefinedProperty(
        QgsSymbolLayer.Property.PropertyStrokeWidth,
        QgsProperty.fromExpression("if(@map_scale > 5000, 0.2, 0.6)"))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    reset_project(layer)
    rules, _ = flattener(min_zoom=12, max_zoom=15)
    split = [r for r in rules if r.get_attr("o") == r.get_attr("i")]
    assert split, [r.output_dataset for r in rules]
    last = max(split, key=lambda r: r.get_attr("o"))
    assert last.get_attr("o") == 15 and last.visibility.max_zoom is None
    assert all(r.visibility.max_zoom == r.get_attr("o") + 1 for r in split if r is not last
               and r.get_attr("o") == r.get_attr("i") and r.get_attr("o") < 15)


@pytest.mark.parametrize("active", [False, True])
def test_switched_off_map_scale_property_does_not_split_per_zoom(flattener, active):
    """Debrecen: a parcel fill kept an unused (switched off) outline width
    reading @map_scale; the fill became a dataset of every parcel per zoom.
    Only an active property splits the rule."""
    from qgis.core import (QgsFillSymbol, QgsProperty, QgsSingleSymbolRenderer,
                           QgsSymbolLayer)
    layer = zoning_layer()
    symbol = QgsFillSymbol.createSimple({"color": "red", "outline_style": "no"})
    width = QgsProperty.fromExpression("CASE WHEN @map_scale < 1000 THEN 0 ELSE 0.8 END")
    width.setActive(active)
    symbol.symbolLayer(0).setDataDefinedProperty(QgsSymbolLayer.Property.PropertyStrokeWidth, width)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    reset_project(layer)
    rules, _ = flattener(min_zoom=12, max_zoom=15)
    assert len(rules) == (4 if active else 1), [r.output_dataset for r in rules]
    # The project's own symbol keeps its switched-off property.
    kept = layer.renderer().symbol().symbolLayer(0).dataDefinedProperties().property(
        QgsSymbolLayer.Property.PropertyStrokeWidth)
    assert kept.expressionString() == width.expressionString() and kept.isActive() == active


def test_labels_keep_the_zooms_of_a_renderer_rule_materialized_per_zoom(flattener, tmp_path):
    """Flow arrows along a river (a screen-unit marker interval) are placed
    per zoom: the renderer rule becomes one slice per zoom. Its labels keep
    the rule's whole zoom range (they were matched to one slice and only
    shown at the last zoom)."""
    from qgis.core import (Qgis, QgsLineSymbol, QgsMarkerLineSymbolLayer, QgsMarkerSymbol,
                           QgsPalLayerSettings, QgsSimpleLineSymbolLayer, QgsSingleSymbolRenderer,
                           QgsVectorLayerSimpleLabeling)
    from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsVectorLayer
    from qgis.PyQt.QtCore import QVariant
    from q2vt_fixtures import to_geopackage
    memory = QgsVectorLayer("LineString?crs=EPSG:3857", "river", "memory")
    memory.dataProvider().addAttributes([QgsField("name", QVariant.String)])
    memory.updateFields()
    feature = QgsFeature(memory.fields())
    feature.setAttributes(["Koornlands"])
    feature.setGeometry(QgsGeometry.fromWkt("LINESTRING(2119000 6019000, 2121000 6020500, 2123000 6020000)"))
    memory.dataProvider().addFeatures([feature])
    layer = to_geopackage(memory, str(tmp_path / "river.gpkg"))
    arrows = QgsMarkerLineSymbolLayer(True, 22)
    arrows.setPlacements(Qgis.MarkerLinePlacement.Interval)
    arrows.setSubSymbol(QgsMarkerSymbol.createSimple({"name": "arrowhead", "size": "1.6"}))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([QgsSimpleLineSymbolLayer(), arrows])))
    settings = QgsPalLayerSettings()
    settings.fieldName = "name"
    settings.placement = Qgis.LabelPlacement.Curved
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    reset_project(layer)
    rules, _ = flattener(11, 17)
    slices = [r for r in rules if r.get_attr("t") == 0 and r.get_attr("o") == r.get_attr("i")]
    assert len({r.get_attr("o") for r in slices}) > 1   # the arrows are per zoom
    labels = [r for r in rules if r.get_attr("t") == 1]
    assert labels and all((r.get_attr("o"), r.get_attr("i")) == (11, 17) for r in labels), \
        [(r.output_dataset, r.get_attr("o"), r.get_attr("i")) for r in labels]
    assert len({r.output_dataset for r in labels}) == len(labels)
