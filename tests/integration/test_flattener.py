"""Rule flattening: visibility intervals, ELSE semantics, project non-mutation."""

import pytest
from qgis.core import QgsFeatureRequest, QgsExpression, QgsProcessingFeedback

from fidelity import zoom as zm
from q2vt_fixtures import categorized, reset_project, rule_based, zoning_layer


@pytest.fixture
def flattener(plugin):
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener  # pylint: disable=import-error
    from fidelity.diagnostics import DiagnosticCollector

    def run(min_zoom=0, max_zoom=14, **options):
        diags = DiagnosticCollector()
        rules = RulesFlattener(min_zoom, max_zoom, "/tmp", QgsProcessingFeedback(),
                               diags, **options).flatten_all_rules()
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


def test_a_category_field_with_an_operator_in_its_name_is_the_field(flattener):
    """A categorized renderer on a field named "a/b": QGIS reads the field
    (a field of that name exists), not the division of fields a and b."""
    from qgis.core import (QgsCategorizedSymbolRenderer, QgsFeature, QgsField, QgsFillSymbol,
                           QgsGeometry, QgsRendererCategory, QgsVectorLayer)
    from qgis.PyQt.QtCore import QVariant
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "stops", "memory")
    layer.dataProvider().addAttributes([QgsField(name, QVariant.Int) for name in ("a", "b", "a/b")])
    layer.updateFields()
    feature = QgsFeature(layer.fields())
    feature.setAttributes([6, 3, 1])  # a / b = 2, the field "a/b" = 1
    feature.setGeometry(QgsGeometry.fromWkt("POLYGON((0 0, 10 0, 10 10, 0 0))"))
    layer.dataProvider().addFeatures([feature])
    layer.setRenderer(QgsCategorizedSymbolRenderer("a/b", [
        QgsRendererCategory(value, QgsFillSymbol.createSimple({"color": color, "outline_style": "no"}),
                            str(value))
        for value, color in ((1, "red"), (2, "blue"))]))
    reset_project(layer)
    rules, _ = flattener()
    drawn = [r.rule.symbol().color().name() for r in rules
             if list(layer.getFeatures(QgsFeatureRequest(QgsExpression(r.rule.filterExpression()))))]
    assert drawn == ["#ff0000"]


def test_a_position_that_is_not_a_number_leaves_the_label_free(flattener):
    """Data-defined label X/Y read from text fields: QGIS pins a label only
    where both convert to numbers; text that does not ("n/a") is no
    position and the label is placed normally. Such features went to the
    pinned labels, where no point could be made: the label was lost."""
    from qgis.core import (QgsFeature, QgsField, QgsFillSymbol, QgsGeometry, QgsPalLayerSettings,
                           QgsProperty, QgsSingleSymbolRenderer, QgsVectorLayer,
                           QgsVectorLayerSimpleLabeling)
    from qgis.PyQt.QtCore import QVariant
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "lots", "memory")
    layer.dataProvider().addAttributes([QgsField(n, QVariant.String) for n in ("name", "px", "py")])
    layer.updateFields()
    features = []
    for values in (["A", "10.5", "20"], ["B", "n/a", "20"], ["C", None, None]):
        feature = QgsFeature(layer.fields())
        feature.setAttributes(values)
        feature.setGeometry(QgsGeometry.fromWkt("POLYGON((0 0, 50 0, 50 50, 0 0))"))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "red"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "name"
    props = settings.dataDefinedProperties()
    props.setProperty(QgsPalLayerSettings.Property.PositionX, QgsProperty.fromField("px"))
    props.setProperty(QgsPalLayerSettings.Property.PositionY, QgsProperty.fromField("py"))
    settings.setDataDefinedProperties(props)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    reset_project(layer)
    rules, _ = flattener()
    labels = [r for r in rules if r.get_attr("t") == 1]
    pinned = [r for r in labels if r.get_attr("p") == 1]
    free = [r for r in labels if r.get_attr("p") != 1]

    def names(group):
        return sorted({f["name"] for r in group for f in layer.getFeatures(
            QgsFeatureRequest(QgsExpression(r.rule.filterExpression())))})
    assert names(pinned) == ["A"] and names(free) == ["B", "C"]


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
    # Active: split where the width changes (1:1000), not per zoom.
    assert len(rules) == (2 if active else 1), [r.output_dataset for r in rules]
    if active:
        assert rules[1].visibility.min_zoom == pytest.approx(zm.scale_to_zoom(1000))
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


def test_a_feature_drawn_by_two_rules_is_labelled_once(flattener):
    """Zone polygons coloured by category, with a filterless last rule that
    draws every outline: the labels were split per renderer rule, so the
    catch-all rule labelled every zone a second time (two copies of each
    code on the web). QGIS labels a drawn feature once."""
    from qgis.core import (QgsExpression, QgsExpressionContext, QgsExpressionContextUtils,
                           QgsFillSymbol, QgsPalLayerSettings, QgsRuleBasedRenderer,
                           QgsVectorLayerSimpleLabeling)
    root = QgsRuleBasedRenderer.Rule(None)
    for zone, colour in (("K1", "255,0,0"), ("K2", "0,255,0")):
        root.appendChild(QgsRuleBasedRenderer.Rule(QgsFillSymbol.createSimple({"color": colour}),
                                                   filterExp=f"\"zone\" = '{zone}'"))
    root.appendChild(QgsRuleBasedRenderer.Rule(QgsFillSymbol.createSimple(
        {"style": "no", "outline_color": "black"})))  # catch-all outline
    layer = zoning_layer()
    layer.setRenderer(QgsRuleBasedRenderer(root))
    settings = QgsPalLayerSettings()
    settings.fieldName = "zone"
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    reset_project(layer)
    rules, _ = flattener(10, 14)
    labels = [r for r in rules if r.get_attr("t") == 1]
    assert labels
    for feature in layer.getFeatures():
        for zoom in range(10, 15):
            context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
            context.setFeature(feature)
            hits = [r for r in labels if r.get_attr("o") <= zoom <= r.get_attr("i")
                    and (not r.rule.filterExpression()
                         or QgsExpression(r.rule.filterExpression()).evaluate(context))]
            assert len(hits) == 1, (feature["zone"], zoom, [r.rule.filterExpression() for r in hits])


def test_nested_rules_get_unique_ids(flattener):
    """Children of different parents (a nested rule-based renderer: land use
    with quality classes 1-8 under each use) had the same rule number at
    their depth: one id and one dataset for several rules, and duplicate
    style layer ids that stopped the web map from loading."""
    from qgis.core import QgsFillSymbol, QgsRuleBasedRenderer, QgsSymbol
    root = QgsRuleBasedRenderer.Rule(None)
    for zone, colour in (("K1", "255,0,0"), ("K2", "0,255,0")):
        parent = QgsRuleBasedRenderer.Rule(QgsFillSymbol.createSimple({"color": colour}),
                                           filterExp=f"\"zone\" = '{zone}'")
        for width in (0.2, 0.4):
            parent.appendChild(QgsRuleBasedRenderer.Rule(
                QgsFillSymbol.createSimple({"color": colour, "outline_width": str(width)}),
                filterExp=f"\"width\" = {width}"))
        root.appendChild(parent)
    layer = zoning_layer()
    layer.setRenderer(QgsRuleBasedRenderer(root))
    reset_project(layer)
    rules, _ = flattener()
    names = [r.output_dataset for r in rules]
    assert len(rules) == 12 and len(set(names)) == len(names), names


def test_a_repeated_style_layer_id_is_renamed_and_reported(plugin):
    """MapLibre rejects the whole style when a layer id repeats: whatever
    produced it, the written style keeps the map loadable and says so."""
    from q2vt_plugin.src.core import maplibre_converter as mc  # pylint: disable=import-error
    from fidelity.diagnostics import DiagnosticCollector
    exporter = mc.QgisMapLibreStyleExporter.__new__(mc.QgisMapLibreStyleExporter)
    exporter.diagnostics = DiagnosticCollector()
    exporter.style = {"layers": [{"id": "a"}, {"id": "b"}, {"id": "a"}, {"id": "a"}, {"id": "a_2"}]}
    exporter._unique_layer_ids()  # pylint: disable=protected-access
    assert [l["id"] for l in exporter.style["layers"]] == ["a", "b", "a_2", "a_3", "a_2_2"]
    assert len(exporter.diagnostics.by_code("Q2VT_STYLE_DUPLICATE_ID")) == 3


def _curved_river(tmp_path, name="river", **options):
    """A river layer with a curved label repeated every 70 mm (9 pt)."""
    from qgis.core import (Qgis, QgsFeature, QgsField, QgsGeometry, QgsLineSymbol,
                           QgsPalLayerSettings, QgsProperty, QgsSingleSymbolRenderer,
                           QgsTextFormat, QgsVectorLayer, QgsVectorLayerSimpleLabeling)
    from qgis.PyQt.QtCore import QVariant
    from q2vt_fixtures import to_geopackage
    memory = QgsVectorLayer("LineString?crs=EPSG:3857", name, "memory")
    memory.dataProvider().addAttributes([QgsField("name", QVariant.String)])
    memory.updateFields()
    feature = QgsFeature(memory.fields())
    feature.setAttributes(["Koornlands"])
    feature.setGeometry(QgsGeometry.fromWkt(
        "LINESTRING(2119000 6019000, 2121000 6020500, 2123000 6020000)"))
    memory.dataProvider().addFeatures([feature])
    layer = to_geopackage(memory, str(tmp_path / f"{name}.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({"color": "blue"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "name"
    settings.placement = options.get("placement", Qgis.LabelPlacement.Curved)
    settings.repeatDistance = options.get("repeat", 70)
    settings.repeatDistanceUnit = Qgis.RenderUnit.Millimeters
    fmt = QgsTextFormat()
    fmt.setSize(options.get("size", 9))
    fmt.setSizeUnit(options.get("unit", Qgis.RenderUnit.Points))
    settings.setFormat(fmt)
    if options.get("dd_size"):
        settings.dataDefinedProperties().setProperty(
            QgsPalLayerSettings.Property.Size, QgsProperty.fromExpression('length("name")'))
    if options.get("merge"):
        line = settings.lineSettings()
        line.setMergeLines(True)
        settings.setLineSettings(line)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    return layer


def test_repeated_curved_line_labels_are_laid_out_per_zoom(flattener, tmp_path):
    """Swellendam rivers: a curved label repeated along its line becomes one
    rule per zoom that lays its labels out at export time (MapLibre's line
    placement dropped them on wiggly rivers); the last zoom keeps its
    overzoom. The recipe holds the zoom's pixels: the text size, and QGIS's
    repeat distance as at zoom + 0.5 in the tile zoom's pixels. Labels the
    export cannot lay out as QGIS does (once per line, parallel,
    data-defined size, merged lines) stay one rule on MapLibre's placement."""
    import math
    from qgis.core import Qgis
    others = {"once": {"repeat": 0}, "parallel": {"placement": Qgis.LabelPlacement.Line},
              "sized": {"dd_size": True}, "merged": {"merge": True}}
    reset_project(_curved_river(tmp_path), *(_curved_river(tmp_path, name, **options)
                                             for name, options in others.items()))
    rules, _ = flattener(11, 14)
    labels = {}
    for rule in rules:
        if rule.get_attr("t") == 1:
            labels.setdefault(rule.layer.name(), []).append(rule)
    for name in others:
        assert [(r.recipe, r.get_attr("o"), r.get_attr("i")) for r in labels[name]] == \
            [(None, 11, 14)], name
    river = sorted(labels["river"], key=lambda r: r.get_attr("o"))
    assert [(r.get_attr("o"), r.get_attr("i")) for r in river] == [(z, z) for z in range(11, 15)]
    assert [r.visibility.max_zoom for r in river] == [12, 13, 14, None]
    assert len({r.output_dataset for r in river}) == 4
    recipe = river[0].recipe
    assert recipe.kind == "label_windows" and recipe.param("zoom") == 11
    assert recipe.param("size") == pytest.approx(12.0)                 # 9 pt
    assert recipe.param("repeat") == pytest.approx(70 * 96 / 25.4 / math.sqrt(2))
    assert recipe.param("chop") == pytest.approx(1 / math.sqrt(2))
    assert recipe.param("fit") == 1.0
    assert (recipe.param("max_in"), recipe.param("max_out"), recipe.param("max_angle")) == \
        (25.0, -25.0, 25.0)


def test_map_unit_curved_labels_fit_maplibre_line_check(flattener, tmp_path):
    """Map-unit text: the recipe's size is the tile zoom's; MapLibre checks
    the fit of zoom 17's labels with their zoom-18 size (twice as long)."""
    from qgis.core import Qgis
    reset_project(_curved_river(tmp_path, size=20, unit=Qgis.RenderUnit.MapUnits))
    rules, _ = flattener(16, 17)
    labels = {r.get_attr("o"): r.recipe for r in rules if r.get_attr("t") == 1}
    assert labels[16].param("size") == pytest.approx(20 * 512 * 2 ** 16 / 40075016.68557849)
    assert labels[17].param("size") == pytest.approx(2 * labels[16].param("size"))
    assert labels[16].param("fit") == 1.0 and labels[17].param("fit") == pytest.approx(2.0)
    assert labels[16].param("chop") == pytest.approx(1.0)               # grows with the map


# --- zooms beyond the export's last tile zoom (Publish: tiles 0-16, the
# browser zooms in to 22 on the zoom-16 tiles) ---------------------------------

def _shown_at(rules, zoom, rule_type=0):
    return [r for r in rules if r.get_attr("t") == rule_type and r.visibility.contains(zoom)]


def _matches(rule, layer):
    """Ids of the features a flat rule's (bound) filter selects."""
    request = QgsFeatureRequest()
    if rule.rule.filterExpression():
        request = QgsFeatureRequest(QgsExpression(rule.rule.filterExpression()))
    return sorted(f["id"] for f in layer.getFeatures(request))


def _fills(rules):
    """The fill components (not the outlines drawn as lines)."""
    return [r for r in rules if r.get_attr("c") == 2]


def test_rules_shown_only_beyond_the_last_tile_zoom_are_drawn_from_its_tiles(flattener):
    """A rule from 1:1000 (zoom 18.2), a layer shown from 1:1500, a label
    limited to 1:1500 and an ELSE rule beside a rule up to 1:1000 were
    dropped from a 0-16 export (their first tile zoom is beyond 16): the
    web map never drew them, even at zoom 22."""
    from qgis.core import (QgsFillSymbol, QgsPalLayerSettings, QgsRuleBasedRenderer,
                           QgsVectorLayerSimpleLabeling)
    detail = rule_based(zoning_layer("detail"), [("d", "", 1000, 0, True, "0,160,0")])
    near = rule_based(zoning_layer("near"), [("n", "", 0, 0, True, "0,0,255")])
    near.setScaleBasedVisibility(True)
    near.setMinimumScale(1500)
    near.setMaximumScale(0)
    settings = QgsPalLayerSettings()
    settings.fieldName = "zone"
    settings.scaleVisibility = True
    settings.minimumScale = 1500
    settings.maximumScale = 0
    labelled = rule_based(zoning_layer("labelled"), [("l", "", 0, 0, True, "128,128,128")])
    labelled.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    labelled.setLabelsEnabled(True)
    other = zoning_layer("other")
    root = QgsRuleBasedRenderer.Rule(None)
    near_rule = QgsRuleBasedRenderer.Rule(QgsFillSymbol.createSimple({"color": "blue"}), 0, 0,
                                          "\"zone\" = 'K1'", "A")
    near_rule.setMinimumScale(5000)
    near_rule.setMaximumScale(1000)
    root.appendChild(near_rule)
    root.appendChild(QgsRuleBasedRenderer.Rule(QgsFillSymbol.createSimple({"color": "red"}),
                                               0, 0, "ELSE", "else"))
    other.setRenderer(QgsRuleBasedRenderer(root))
    reset_project(detail, near, labelled, other)
    rules, _ = flattener(0, 16)
    for name, scale, rule_type in (("detail", 1000, 0), ("near", 1500, 0), ("labelled", 1500, 1)):
        shown = [r for r in _shown_at(rules, 20.0, rule_type) if r.layer.name() == name]
        assert shown, name
        for rule in shown:
            assert (rule.get_attr("o"), rule.get_attr("i")) == (16, 16)  # the zoom-16 tiles
            assert rule.visibility.min_zoom == pytest.approx(zm.scale_to_zoom(scale))
            assert rule.visibility.max_zoom is None
        assert not [r for r in _shown_at(rules, 16.5, rule_type) if r.layer.name() == name]
    # ELSE: the features A does not draw while A is shown, then all of them.
    everything = sorted(f["id"] for f in other.getFeatures())
    for zoom in (17.0, 20.0, 23.0):
        drawn = sorted(i for r in _fills(_shown_at(rules, zoom)) if r.layer.name() == "other"
                       for i in _matches(r, other))
        assert drawn == everything, zoom


def test_rules_beyond_max_zoom_plus_one_are_reported_when_the_map_stops(flattener):
    """Hide layers beyond the maximum zoom: such a rule is never shown; it
    is left out with a diagnostic, not silently."""
    from fidelity.model import OverzoomPolicy
    reset_project(rule_based(zoning_layer("detail"), [("d", "", 1000, 0, True, "0,160,0")]))
    rules, diags = flattener(0, 16, overzoom=OverzoomPolicy.STOP)
    assert not rules
    assert diags.by_code("Q2VT_ZOOM_EMPTY_INTERVAL")


def test_labels_are_not_shown_where_no_renderer_rule_draws(flattener):
    """QGIS labels only the features its renderer draws: a label from zoom
    14 of a layer drawn at zooms 10-13 shows nothing (it was exported for
    every feature, floating without symbols). The "No symbols" renderer
    still labels every feature."""
    from qgis.core import (QgsNullSymbolRenderer, QgsPalLayerSettings,
                           QgsVectorLayerSimpleLabeling)

    def labelled(layer):
        settings = QgsPalLayerSettings()
        settings.fieldName = "zone"
        settings.scaleVisibility = True
        settings.minimumScale = zm.zoom_to_scale(14)
        settings.maximumScale = 0
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        return layer
    drawn = labelled(rule_based(zoning_layer("drawn"), [
        ("r", "", zm.zoom_to_scale(10), zm.zoom_to_scale(13.5), True, "0,0,255")]))
    plain = labelled(zoning_layer("plain"))
    plain.setRenderer(QgsNullSymbolRenderer())
    reset_project(drawn, plain)
    rules, _ = flattener(0, 16)
    labels = {r.layer.name() for r in rules if r.get_attr("t") == 1}
    assert labels == {"plain"}


def test_map_scale_rules_switch_where_qgis_switches_them(flattener):
    """A filter, a symbol layer enabled and a width switched by @map_scale
    below the scale of the export's last zoom (1:2000 < 1:4514 at zoom 16)
    never switched on the web map: the zoom-16 value was kept up to zoom
    22. Inside the export a 1:5000 switch came up to a zoom late (at zoom
    16, not 15.85)."""
    from qgis.core import QgsProperty, QgsSymbolLayer
    from fidelity.qgis_expr import with_map_scale
    layer = rule_based(zoning_layer(), [("f", "@map_scale < 2000", 0, 0, True, "0,0,255"),
                                        ("e", "", 0, 0, True, "255,0,0"),
                                        ("w", "", 0, 0, True, "0,160,0"),
                                        ("lag", "@map_scale < 5000", 0, 0, True, "0,0,0")])
    width = "CASE WHEN @map_scale < 2000 THEN 3 ELSE 0.5 END"
    children = layer.renderer().rootRule().children()
    children[1].symbol().symbolLayer(0).setDataDefinedProperty(
        QgsSymbolLayer.Property.PropertyLayerEnabled, QgsProperty.fromExpression("@map_scale < 2000"))
    children[2].symbol().symbolLayer(0).setDataDefinedProperty(
        QgsSymbolLayer.Property.PropertyStrokeWidth, QgsProperty.fromExpression(width))
    reset_project(layer)
    rules, _ = flattener(0, 16)
    everything = sorted(f["id"] for f in layer.getFeatures())

    def shown(color, zoom):
        return [r for r in _fills(_shown_at(rules, zoom))
                if r.rule.symbol().color().name() == color]
    switch = zm.scale_to_zoom(2000)
    for color in ("#0000ff", "#ff0000"):
        assert not [r for r in shown(color, switch - 0.05) if _matches(r, layer)], color
        for zoom in (switch + 0.05, 20.0, 22.5):
            [rule] = shown(color, zoom)
            assert (rule.get_attr("o"), rule.get_attr("i")) == (16, 16)
            assert _matches(rule, layer) == everything, (color, zoom)
    # The width in each piece: the exporter binds @map_scale at its scale zoom.
    for zoom, expected in ((switch - 0.05, 0.5), (switch + 0.05, 3), (21.0, 3)):
        [rule] = shown("#00a000", zoom)
        value = QgsExpression(with_map_scale(width, zm.zoom_to_scale(rule.evaluation_zoom())))
        assert value.evaluate() == expected, zoom
    lag = zm.scale_to_zoom(5000)
    assert [_matches(r, layer) for r in shown("#000000", lag + 0.02)] == [everything]
    assert not [r for r in shown("#000000", lag - 0.02) if _matches(r, layer)]


def test_labels_of_a_map_scale_rule_follow_its_features_at_every_zoom(flattener):
    """Labels of a rule '@map_scale < 50000 OR zone = K1' used the filter
    bound at the first zoom (where only K1 is drawn) at every zoom: the
    other features drawn closer in were never labelled."""
    from qgis.core import QgsPalLayerSettings, QgsVectorLayerSimpleLabeling
    layer = rule_based(zoning_layer(), [("r", "@map_scale < 50000 OR \"zone\" = 'K1'", 0, 0,
                                         True, "0,0,255")])
    settings = QgsPalLayerSettings()
    settings.fieldName = "zone"
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    reset_project(layer)
    rules, _ = flattener(0, 16)
    everything = sorted(f["id"] for f in layer.getFeatures())
    k1 = sorted(f["id"] for f in layer.getFeatures() if f["zone"] == "K1")
    assert k1 != everything
    for zoom, expected in ((8.0, k1), (15.0, everything), (20.0, everything)):
        drawn = [_matches(r, layer) for r in _fills(_shown_at(rules, zoom))]
        labelled = [_matches(r, layer) for r in _shown_at(rules, zoom, 1)]
        assert drawn == [expected] and labelled == [expected], zoom


def test_zoom_level_variables_are_the_zoom_of_the_map_scale(flattener):
    """@zoom_level / @vector_tile_zoom (QGIS's map scope, from the map
    scale) were NULL: rules filtered with them exported no feature."""
    layer = rule_based(zoning_layer(), [("hi", "@zoom_level >= 15", 0, 0, True, "255,0,0"),
                                        ("lo", "@zoom_level < 15", 0, 0, True, "0,0,255"),
                                        ("v", "@vector_tile_zoom > 17.5", 0, 0, True, "0,160,0")])
    reset_project(layer)
    rules, _ = flattener(0, 16)
    everything = sorted(f["id"] for f in layer.getFeatures())
    # QGIS: zoom_level = round(log2(559082264.03 / scale) - 1).
    switch = zm.scale_to_zoom(559082264.0287178 / 2 ** 15.5)
    for zoom, colors in ((switch - 0.05, {"#0000ff"}), (switch + 0.05, {"#ff0000"}),
                         (20.0, {"#ff0000", "#00a000"})):
        drawn = {r.rule.symbol().color().name() for r in _fills(_shown_at(rules, zoom))
                 if _matches(r, layer) == everything}
        assert drawn == colors, zoom


def test_heatmap_intensity_goes_on_beyond_the_last_tile_zoom(flattener):
    """An automatic-maximum heatmap is rescaled per zoom up to zoom 22 (its
    stops ended at the last tile zoom: washed out when zoomed in)."""
    from qgis.core import Qgis, QgsFeature, QgsGeometry, QgsHeatmapRenderer, QgsVectorLayer
    layer = QgsVectorLayer("Point?crs=EPSG:3857", "pts", "memory")
    for x in range(5):
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromWkt(f"POINT({x * 6} 0)"))
        layer.dataProvider().addFeature(feature)
    heatmap = QgsHeatmapRenderer()
    heatmap.setRadius(5)
    heatmap.setRadiusUnit(Qgis.RenderUnit.Millimeters)
    layer.setRenderer(heatmap)
    reset_project(layer)
    rules, _ = flattener(10, 16)
    stops = dict(rules[0].heatmap["intensity_stops"])
    assert min(stops) == 10 and max(stops) == 22


def test_scale_switched_marker_interval_switches_beyond_the_last_tile_zoom(flattener):
    """A marker-line interval CASE WHEN @map_scale > 3000 THEN 10 ELSE 3
    END kept its zoom-16 value (10 mm) up to zoom 22."""
    from qgis.core import (Qgis, QgsFeature, QgsGeometry, QgsLineSymbol, QgsMarkerLineSymbolLayer,
                           QgsMarkerSymbol, QgsProperty, QgsSingleSymbolRenderer, QgsSymbolLayer,
                           QgsVectorLayer)
    layer = QgsVectorLayer("LineString?crs=EPSG:3857&field=id:integer", "line", "memory")
    feature = QgsFeature(layer.fields())
    feature.setAttributes([1])
    feature.setGeometry(QgsGeometry.fromWkt("LINESTRING(0 0, 400 0)"))
    layer.dataProvider().addFeatures([feature])
    markers = QgsMarkerLineSymbolLayer()
    markers.setPlacements(Qgis.MarkerLinePlacement.Interval)
    markers.setInterval(3)
    markers.setSubSymbol(QgsMarkerSymbol.createSimple({"name": "circle", "size": "1.5"}))
    markers.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyInterval, QgsProperty.fromExpression(
        "CASE WHEN @map_scale > 3000 THEN 10 ELSE 3 END"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([markers])))
    reset_project(layer)
    rules, _ = flattener(14, 16)

    def intervals(zoom):
        return {r.rule.symbol().symbolLayer(0).interval() for r in _shown_at(rules, zoom)
                if r.rule.symbol().symbolLayer(0).layerType() == "MarkerLine"}
    # Beyond the last tile zoom MapLibre spaces the markers (on screen):
    # 3 mm from zoom 17 (1:2257), as QGIS from 1:3000.
    assert intervals(17.5) == {3.0} and intervals(21.0) == {3.0}


def test_point_clusters_break_apart_beyond_the_last_tile_zoom(flattener):
    """QGIS's cluster distance is on screen: zooming in splits the clusters.
    The last tile zoom's grouping was kept up to zoom 24."""
    from qgis.core import (Qgis, QgsFeature, QgsGeometry, QgsMarkerSymbol, QgsPointClusterRenderer,
                           QgsSingleSymbolRenderer, QgsVectorLayer)
    layer = QgsVectorLayer("Point?crs=EPSG:3857&field=id:integer", "pts", "memory")
    features = []
    for i, (x, y) in enumerate(((0, 0), (15, 0), (200, 0), (215, 0))):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([i])
        feature.setGeometry(QgsGeometry.fromWkt(f"POINT({x} {y})"))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    renderer = QgsPointClusterRenderer()
    renderer.setTolerance(10)
    renderer.setToleranceUnit(Qgis.RenderUnit.Millimeters)
    renderer.setEmbeddedRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({})))
    layer.setRenderer(renderer)
    reset_project(layer)
    rules, _ = flattener(10, 16)

    from fidelity import point_groups as pg
    points = [(f.geometry().asPoint().x(), f.geometry().asPoint().y())
              for f in layer.getFeatures()]

    def groups(zoom):
        shown = _shown_at(rules, zoom)
        assert shown, zoom
        return {len(group) for rule in shown
                for group in pg.group_points(points, dict(rule.point_group[3])["tolerance"])}
    # 15 m apart: a cluster per pair at zoom 16 (10 mm is about 45 m), the
    # single points once 10 mm is under 15 m (about zoom 17.6).
    assert groups(16.5) == {2}
    assert groups(18.5) == {1} and groups(22.5) == {1}
    assert all((r.get_attr("o"), r.get_attr("i")) == (16, 16) for r in _shown_at(rules, 18.5))
    names = [r.output_dataset for r in rules]
    assert len(names) == len(set(names))
