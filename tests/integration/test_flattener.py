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


def test_else_skips_what_its_siblings_match_at_every_scale(flattener):
    """QGIS 3.34 counts a renderer sibling out of its scale range or
    switched off as matched ("Inactive"): its features never fall to the
    ELSE rule. Rule-based labels check a sibling's scale range first, so
    there the ELSE label takes them where the sibling is out of range."""
    from qgis.core import QgsPalLayerSettings, QgsRuleBasedLabeling
    layer = rule_based(zoning_layer(), [
        ("k1 zoomed in", "\"zone\" = 'K1'", zm.zoom_to_scale(10), 0, True, "255,0,0"),
        ("k2 unchecked", "\"zone\" = 'K2'", 0, 0, False, "0,255,0"),
        ("else", "ELSE", 0, 0, True, "0,0,255"),
    ])

    def label(flt, min_scale, active=True):
        settings = QgsPalLayerSettings()
        settings.fieldName = "id"
        rule = QgsRuleBasedLabeling.Rule(settings, 0, min_scale, flt, "", flt == "ELSE")
        rule.setActive(active)
        return rule
    root = QgsRuleBasedLabeling.Rule(None)
    for rule in (label("\"zone\" = 'K1'", zm.zoom_to_scale(10)), label("\"zone\" = 'K2'", 0, False),
                 label("ELSE", 0)):
        root.appendChild(rule)
    labels = zoning_layer("labels")  # every feature drawn
    labels.setLabeling(QgsRuleBasedLabeling(root))
    labels.setLabelsEnabled(True)
    reset_project(layer, labels)
    rules, _ = flattener(0, 14)
    else_rules = [r for r in rules if r.get_attr("t") == 0 and r.get_attr("c") == 2
                  and r.rule.symbol().color().blue() == 255]
    # One ELSE rule at every zoom, for the features neither sibling matches
    # (NULL zones included).
    [else_rule] = else_rules
    assert else_rule.visibility.min_zoom == 0 and else_rule.visibility.max_zoom is None
    assert _matching_ids(layer, else_rule.rule.filterExpression()) == ["C", "D"]
    labelled = {zoom: sorted(i for r in _shown_at(rules, zoom, 1) for i in _matches(r, labels))
                for zoom in (5.0, 12.0)}
    assert labelled == {5.0: ["A", "C", "D"], 12.0: ["A", "C", "D"]}
    # Below zoom 10 A gets the ELSE label, from zoom 10 the K1 label.
    else_labels = {zoom: sorted(i for r in _shown_at(rules, zoom, 1) if r.get_attr("r") == 2
                                for i in _matches(r, labels)) for zoom in (5.0, 12.0)}
    assert else_labels == {5.0: ["A", "C", "D"], 12.0: ["C", "D"]}
    assert len({r.output_dataset for r in rules}) == len(rules)
    # Project ELSE rule untouched.
    project_else = layer.renderer().rootRule().children()[2]
    assert project_else.filterExpression() == "ELSE"


def test_an_unchecked_category_is_not_drawn_as_all_other_values(flattener):
    """Categorized renderer with an "all other values" class: QGIS draws
    nothing for an unchecked category. The export dropped the category and
    drew its features in the catch-all colour."""
    from qgis.core import QgsCategorizedSymbolRenderer, QgsFillSymbol, QgsRendererCategory
    layer = zoning_layer()
    layer.setRenderer(QgsCategorizedSymbolRenderer("zone", [
        QgsRendererCategory("K1", QgsFillSymbol.createSimple({"color": "0,0,255"}), "K1", False),
        QgsRendererCategory("K2", QgsFillSymbol.createSimple({"color": "0,255,0"}), "K2"),
        QgsRendererCategory(None, QgsFillSymbol.createSimple({"color": "255,0,0"}), "")]))
    reset_project(layer)
    rules, _ = flattener(0, 16)
    drawn = {}
    for rule in rules:
        if rule.get_attr("c") == 2:
            color = rule.rule.symbol().color().name()
            drawn.setdefault(color, set()).update(_matching_ids(layer, rule.rule.filterExpression()))
    assert drawn == {"#00ff00": {"B"}, "#ff0000": {"C", "D"}}


def test_a_label_else_excludes_unchecked_siblings_inside_their_scale_range(flattener):
    """Rule-based labels: an unchecked sibling label rule still keeps the
    ELSE label off its features (QGIS: Inactive). Outside the sibling's
    scale range the ELSE labels them (QGIS checks label scale first)."""
    from qgis.core import QgsPalLayerSettings, QgsRuleBasedLabeling
    layer = rule_based(zoning_layer(), [("all", "", 0, 0, True, "200,200,200")])
    root = QgsRuleBasedLabeling.Rule(None)
    for flt, low, active in (("\"zone\" = 'K1'", 0, False), ("\"zone\" = 'K2'", zm.zoom_to_scale(10), True),
                             ("ELSE", 0, True)):
        settings = QgsPalLayerSettings()
        settings.fieldName = "id"
        rule = QgsRuleBasedLabeling.Rule(settings, 0, 0, flt)
        rule.setMinimumScale(low)
        rule.setActive(active)
        root.appendChild(rule)
    layer.setLabeling(QgsRuleBasedLabeling(root))
    layer.setLabelsEnabled(True)
    reset_project(layer)
    rules, _ = flattener(0, 16)
    labels = sorted((r for r in rules if r.get_attr("t") == 1 and r.get_attr("r") == 2),
                    key=lambda r: r.visibility.min_zoom)
    assert [_matching_ids(layer, r.rule.filterExpression()) for r in labels] == \
        [["B", "C", "D"], ["C", "D"]]
    assert labels[0].visibility.max_zoom == pytest.approx(10)
    assert labels[1].visibility.min_zoom == pytest.approx(10) and labels[1].visibility.max_zoom is None
    assert len({r.output_dataset for r in rules}) == len(rules)


def test_label_else_variants_inside_one_zoom_get_their_own_datasets(flattener):
    """Label siblings with scale limits inside one tile zoom split the ELSE
    rule into variants with the same zooms: they shared a dataset (and the
    first variant's filter)."""
    from qgis.core import QgsPalLayerSettings, QgsRuleBasedLabeling
    layer = rule_based(zoning_layer(), [("all", "", 0, 0, True, "200,200,200")])
    root = QgsRuleBasedLabeling.Rule(None)
    for flt, low, high in (("\"zone\" = 'K1'", 15.2, 15.5), ("\"zone\" = 'K2'", 15.5, 15.9),
                           ("ELSE", None, None)):
        settings = QgsPalLayerSettings()
        settings.fieldName = "id"
        rule = QgsRuleBasedLabeling.Rule(settings, 0, 0, flt)
        if low is not None:
            rule.setMinimumScale(zm.zoom_to_scale(low))
            rule.setMaximumScale(zm.zoom_to_scale(high))
        root.appendChild(rule)
    layer.setLabeling(QgsRuleBasedLabeling(root))
    layer.setLabelsEnabled(True)
    reset_project(layer)
    rules, _ = flattener(0, 16)
    labels = sorted((r for r in rules if r.get_attr("t") == 1 and r.get_attr("r") == 2),
                    key=lambda r: r.visibility.min_zoom)
    assert [_matching_ids(layer, r.rule.filterExpression()) for r in labels] == \
        [["A", "B", "C", "D"], ["B", "C", "D"], ["A", "C", "D"], ["A", "B", "C", "D"]]
    assert [(r.get_attr("o"), r.get_attr("i")) for r in labels[1:3]] == [(15, 15), (15, 15)]
    assert len({r.output_dataset for r in rules}) == len(rules)


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
    placement dropped them on wiggly rivers); above the tile zooms the web
    viewer places them on the visible line. The recipe holds the zoom's
    pixels: the text size, and QGIS's repeat distance as at zoom + 0.5 in
    the tile zoom's pixels. Labels the export cannot lay out as QGIS does
    (parallel, data-defined size, merged lines) stay on MapLibre's
    placement over the tile zooms; a label drawn once per line is one rule."""
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
    assert [(r.recipe, r.get_attr("o"), r.get_attr("i"), r.visibility.max_zoom)
            for r in labels["once"]] == [(None, 11, 14, None)]
    for name in ("parallel", "sized", "merged"):
        native, viewer = labels[name]
        assert (native.recipe, native.get_attr("o"), native.get_attr("i")) == (None, 11, 14)
        assert native.visibility.max_zoom == 15 and not native.visible_repeat, name
        assert viewer.visible_repeat and viewer.visibility.min_zoom == 15, name
        assert viewer.visibility.max_zoom is None and viewer.get_attr("c") == 0, name
    river = sorted(labels["river"], key=lambda r: (r.visible_repeat, r.get_attr("o")))
    assert [(r.get_attr("o"), r.get_attr("i")) for r in river[:-1]] == [(z, z) for z in range(11, 15)]
    assert [r.visibility.max_zoom for r in river[:-1]] == [12, 13, 14, 15]
    assert river[-1].visible_repeat and river[-1].recipe is None
    assert (river[-1].visibility.min_zoom, river[-1].visibility.max_zoom) == (15, None)
    assert len({r.output_dataset for r in river}) == 5
    recipe = river[0].recipe
    assert recipe.kind == "label_windows" and recipe.param("zoom") == 11
    assert recipe.param("size") == pytest.approx(12.0)                 # 9 pt
    assert recipe.param("repeat") == pytest.approx(70 * 96 / 25.4 / math.sqrt(2))
    assert recipe.param("chop") == pytest.approx(1 / math.sqrt(2))
    assert recipe.param("fit") == 1.0
    assert (recipe.param("max_in"), recipe.param("max_out"), recipe.param("max_angle")) == \
        (25.0, -25.0, 25.0)


def test_repeated_line_labels_above_the_tile_zooms_go_to_the_viewer(flattener, tmp_path):
    """Publish defaults (0-16, the map overzooms to 22): the z16 windows
    were reused up to z22, map-unit labels outgrew them from z17 and
    screen-unit repeats thinned out to none. Above the tile zooms, and from
    the zoom where the repeat reaches a tile (QGIS's 300 mm repeat: at
    every zoom), the viewer lays the labels out on the visible line."""
    from qgis.core import Qgis
    reset_project(_curved_river(tmp_path, "mapunit", size=20, unit=Qgis.RenderUnit.MapUnits),
                  _curved_river(tmp_path, "sparse", repeat=300),
                  _curved_river(tmp_path, "level", placement=Qgis.LabelPlacement.Horizontal))
    rules, _ = flattener(0, 16)
    labels = {}
    for rule in rules:
        if rule.get_attr("t") == 1:
            labels.setdefault(rule.layer.name(), []).append(rule)
    windows = [r for r in labels["mapunit"] if r.recipe is not None]
    assert max(r.get_attr("i") for r in windows) == 16
    assert all(r.visibility.max_zoom is not None and r.visibility.max_zoom <= 17 for r in windows)
    viewer = [r for r in labels["mapunit"] if r.visible_repeat]
    assert [(r.get_attr("o"), r.get_attr("i"), r.visibility.min_zoom, r.visibility.max_zoom)
            for r in viewer] == [(16, 16, 17, None)]
    for name in ("sparse", "level"):  # 1134 px apart; Horizontal on lines: always
        assert [(r.visible_repeat, r.recipe, r.visibility.min_zoom, r.visibility.max_zoom)
                for r in labels[name]] == [(True, None, 0, None)], name


def test_map_unit_curved_labels_fit_maplibre_line_check(flattener, tmp_path):
    """Map-unit text: the recipe's size is the tile zoom's. MapLibre checks
    the fit of zoom 17's labels with their zoom-18 size (twice as long), so
    their windows lost most labels: from tile zoom 17 the viewer places
    them on the visible line."""
    from qgis.core import Qgis
    reset_project(_curved_river(tmp_path, size=20, unit=Qgis.RenderUnit.MapUnits))
    rules, _ = flattener(16, 17)
    labels = {r.get_attr("o"): r.recipe for r in rules if r.get_attr("t") == 1 and r.recipe}
    assert list(labels) == [16]
    assert labels[16].param("size") == pytest.approx(20 * 512 * 2 ** 16 / 40075016.68557849)
    assert labels[16].param("fit") == 1.0
    assert labels[16].param("chop") == pytest.approx(1.0)               # grows with the map
    viewer = [r for r in rules if r.get_attr("t") == 1 and r.visible_repeat]
    assert [(r.get_attr("o"), r.get_attr("i"), r.visibility.min_zoom, r.visibility.max_zoom)
            for r in viewer] == [(17, 17, 17, None)]


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
    # ELSE: the features A does not match, also beyond A's scale range.
    everything = sorted(f["id"] for f in other.getFeatures())
    for zoom, expected in ((17.0, everything), (20.0, ["B", "C", "D"]), (23.0, ["B", "C", "D"])):
        drawn = sorted(i for r in _fills(_shown_at(rules, zoom)) if r.layer.name() == "other"
                       for i in _matches(r, other))
        assert drawn == expected, zoom


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


def test_zooms_valued_alike_by_a_map_scale_expression_are_one_piece(flattener):
    """A width read from the scale ('min(1.5, 0.5 + 4000 / @map_scale)',
    1.5 mm from zoom 17) gave one piece per zoom up to 22, each a copy of
    the zoom-16 data. Zooms where it has the same value share one piece,
    named after the zoom it starts at (not a count over the export)."""
    from qgis.core import QgsProperty, QgsSymbolLayer
    layer = rule_based(zoning_layer(), [("w", "", 0, 0, True, "0,160,0")])
    layer.renderer().rootRule().children()[0].symbol().symbolLayer(0).setDataDefinedProperty(
        QgsSymbolLayer.Property.PropertyStrokeWidth,
        QgsProperty.fromExpression("min(1.5, 0.5 + 4000 / @map_scale)"))
    reset_project(layer)
    rules, _ = flattener(0, 16)
    assert len({r.rule.description() for r in rules}) == len(rules)
    for component in (1, 2):  # the outline, the fill
        pieces = [r for r in rules if r.get_attr("c") == component]
        [last] = [r for r in pieces if r.visibility.contains(17.5)]
        assert all(r in (last,) for zoom in (19.0, 22.5) for r in _shown_at(rules, zoom)
                   if r.get_attr("c") == component)
        assert (last.visibility.min_zoom, last.visibility.max_zoom) == (17.0, None)
        assert (last.get_attr("o"), last.get_attr("i"), last.get_attr("z")) == (16, 16, 1700)
        assert len([r for r in pieces if r.visibility.min_zoom >= 16]) == 2  # 16, then 17-


def test_a_scale_expression_reading_a_render_variable_keeps_its_zoom_pieces(flattener):
    """A fill fading in with the scale ('set_color_part(@symbol_color,
    'alpha', ...)') reads a variable set only while rendering: valued NULL
    at every zoom, all zooms looked alike and merged into one piece bound
    at zoom 0, so the fill was transparent at every zoom. It is the
    symbol's colour (bind_symbol_color)."""
    from qgis.core import QgsExpressionContext, QgsExpressionContextScope, QgsProperty, QgsSymbolLayer
    from qgis.PyQt.QtGui import QColor
    layer = rule_based(zoning_layer(), [("w", "", 0, 0, True, "255,0,0")])
    layer.renderer().rootRule().children()[0].symbol().symbolLayer(0).setDataDefinedProperty(
        QgsSymbolLayer.Property.PropertyFillColor,
        QgsProperty.fromExpression(
            "set_color_part(@symbol_color, 'alpha', min(255, 2000000 / @map_scale))"))
    reset_project(layer)
    rules, _ = flattener(0, 16)
    fills = [r for r in rules if r.get_attr("c") == 2]
    for zoom in (0.5, 12.5, 16.5, 22.5):
        assert len([r for r in fills if r.visibility.contains(zoom)]) == 1

    def alpha(zoom):
        rule = next(r for r in fills if r.visibility.contains(zoom))
        prop = rule.rule.symbol().symbolLayer(0).dataDefinedProperties().property(
            QgsSymbolLayer.Property.PropertyFillColor)
        scope = QgsExpressionContextScope()
        scope.setVariable("map_scale", zm.zoom_to_scale(rule.evaluation_zoom()))
        color, _ = prop.valueAsColor(QgsExpressionContext([scope]), QColor())
        return color.alpha()
    # Bound to the symbol's colour (red), each zoom is valued at its scale:
    # transparent far out (zooms 0-7 alike, one piece), opaque near.
    assert alpha(0.5) == 0 and 20 < alpha(12.5) < 40 and alpha(16.5) > 200


def test_a_line_shown_beyond_the_last_tile_zoom_does_not_widen_the_tile_buffer(plugin):
    """A line piece shown from zoom 22 draws the zoom-16 tiles 64 times
    magnified: its 60 px width is under a pixel of those tiles. Read at
    zoom 16 it set the largest clip buffer, which grew every tile."""
    from q2vt_plugin.src.core.tiles_generator import GDALTilesGenerator  # pylint: disable=import-error

    class Dataset:
        def __init__(self, name):
            self.name = name

        def source(self):
            return f"/data/{self.name}.gpkg"
    names = ["l00t00d01r00g01c01o16i16s00", "l00t00d01r00g01c01o16i16s00z2200"]
    generator = GDALTilesGenerator.__new__(GDALTilesGenerator)
    generator.layers, generator.layer_zooms = [Dataset(name) for name in names], {}
    generator.style = {"layers": [
        {"type": "line", "source-layer": names[0], "minzoom": 16, "maxzoom": 17,
         "paint": {"line-width": 4}},
        {"type": "line", "source-layer": names[1], "minzoom": 22, "maxzoom": 24,
         "paint": {"line-width": 60}}]}
    assert generator._buffer() == 80  # pylint: disable=protected-access
    generator.style["layers"][1]["minzoom"] = 16
    assert generator._buffer() == 60 * 8 + 8  # pylint: disable=protected-access


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
    """An automatic-maximum heatmap is rescaled per zoom up to the style's
    last zoom, 24 (its stops ended at the last tile zoom: washed out when
    zoomed in)."""
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
    assert min(stops) == 10 and max(stops) == 24


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
        # (the points alone at every zoom past the last tiles are one rule
        # of their own, "part" "stable", grouped at its first zoom)
        return {len(group) for rule in shown
                if dict(rule.point_group[3]).get("part") != "stable"
                for group in pg.group_points(points, dict(rule.point_group[3])["tolerance"])}
    # 15 m apart: a cluster per pair at zoom 16 (10 mm is about 45 m), the
    # single points once 10 mm is under 15 m (about zoom 17.6).
    assert groups(16.5) == {2}
    assert groups(18.5) == {1} and groups(22.5) == {1}
    assert all((r.get_attr("o"), r.get_attr("i")) == (16, 16) for r in _shown_at(rules, 18.5))
    names = [r.output_dataset for r in rules]
    assert len(names) == len(set(names))


def _points(name="points", values=(0, 1, 0, 1)):
    from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer
    layer = QgsVectorLayer("Point?crs=EPSG:3857&field=id:string&field=v:integer&field=lim:integer",
                           name, "memory")
    features = []
    for i, value in enumerate(values):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([f"P{i}", value, 100000 if value else 10000])
        feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(2120000 + 100 * i, 6020000)))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    return layer


def _simple_labels(layer, **settings_attrs):
    from qgis.core import QgsPalLayerSettings, QgsVectorLayerSimpleLabeling
    settings = QgsPalLayerSettings()
    settings.fieldName = "id"
    for key, value in settings_attrs.items():
        setattr(settings, key, value)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    return settings


@pytest.mark.parametrize("layers", [2, 1])
def test_a_symbol_layer_enabled_condition_does_not_restrict_the_labels(flattener, layers):
    """QGIS labels every feature the renderer draws, whichever symbol layers
    a data-defined *enabled* switches off. The label rule took the filter of
    the symbol layer with the condition: only those features were labelled."""
    from qgis.core import (QgsMarkerSymbol, QgsProperty, QgsSimpleMarkerSymbolLayer,
                           QgsSingleSymbolRenderer, QgsSymbolLayer)
    layer = _points()
    symbol = QgsMarkerSymbol.createSimple({"name": "square", "color": "0,0,255"})
    if layers == 2:
        symbol.appendSymbolLayer(QgsSimpleMarkerSymbolLayer())
    symbol.symbolLayer(layers - 1).setDataDefinedProperty(
        QgsSymbolLayer.Property.PropertyLayerEnabled, QgsProperty.fromExpression('"v" = 1'))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    _simple_labels(layer)
    reset_project(layer)
    rules, _ = flattener(0, 16)
    drawn = [r for r in rules if r.get_attr("t") == 0 and r.get_attr("s") == layers - 1]
    assert [_matching_ids(layer, r.rule.filterExpression()) for r in drawn] == [["P1", "P3"]]
    labels = [r for r in rules if r.get_attr("t") == 1]
    assert labels and all(_matching_ids(layer, r.rule.filterExpression()) == ["P0", "P1", "P2", "P3"]
                          for r in labels)


def _rule_labels(layer, rules_spec):
    """rules_spec: [(filter, rule min scale, settings min scale or None)]"""
    from qgis.core import QgsPalLayerSettings, QgsRuleBasedLabeling
    root = QgsRuleBasedLabeling.Rule(None)
    for flt, rule_min, settings_min in rules_spec:
        settings = QgsPalLayerSettings()
        settings.fieldName = "id"
        if settings_min is not None:
            settings.scaleVisibility = True
            settings.minimumScale = settings_min
        rule = QgsRuleBasedLabeling.Rule(settings, 0, 0, flt)
        rule.setMinimumScale(rule_min)
        root.appendChild(rule)
    layer.setLabeling(QgsRuleBasedLabeling(root))
    layer.setLabelsEnabled(True)


def test_label_settings_scale_visibility_narrows_the_rule_range(flattener):
    """QGIS applies a label rule's scale range and its Rendering tab scale
    visibility both. With a rule range the settings range was ignored: the
    labels showed from 1:50000 instead of 1:10000."""
    from qgis.core import QgsMarkerSymbol, QgsSingleSymbolRenderer
    layer = _points()
    layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({})))
    _rule_labels(layer, [("", 50000, 10000), ("", 10000, 50000), ("", 50000, None)])
    reset_project(layer)
    rules, diags = flattener(0, 16)
    starts = {r.get_attr("r"): r.visibility for r in rules if r.get_attr("t") == 1}
    assert starts[0].min_zoom == pytest.approx(zm.scale_to_zoom(10000))
    assert starts[1].min_zoom == pytest.approx(zm.scale_to_zoom(10000))
    assert starts[2].min_zoom == pytest.approx(zm.scale_to_zoom(50000))
    assert all(v.max_zoom is None for v in starts.values())
    assert not diags.by_code("Q2VT_ZOOM_EMPTY_INTERVAL")


def test_label_else_keeps_its_settings_scale_visibility(flattener):
    """An ELSE label split at a sibling's scale limit: its variants carry a
    scale range, so its own settings range was dropped and the labels showed
    from zoom 0."""
    from qgis.core import QgsMarkerSymbol, QgsSingleSymbolRenderer
    layer = _points()
    layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({})))
    _rule_labels(layer, [('"v" = 1', 50000, None), ("ELSE", 0, 10000)])
    reset_project(layer)
    rules, diags = flattener(0, 16)
    labels = [r for r in rules if r.get_attr("t") == 1 and r.get_attr("r") == 1]
    assert [r.visibility.min_zoom for r in labels] == [pytest.approx(zm.scale_to_zoom(10000))]
    assert labels[0].visibility.max_zoom is None
    assert _matching_ids(layer, labels[0].rule.filterExpression()) == ["P0", "P2"]
    assert not diags.by_code("Q2VT_ZOOM_EMPTY_INTERVAL")


def test_data_defined_label_scale_visibility_per_feature(flattener):
    """A data-defined label minimum scale read from a field: each feature's
    labels show from its own scale (evaluated per zoom)."""
    from qgis.core import (QgsMarkerSymbol, QgsPalLayerSettings, QgsProperty,
                           QgsSingleSymbolRenderer)
    layer = _points()
    layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({})))
    settings = _simple_labels(layer, scaleVisibility=True)
    props = settings.dataDefinedProperties()
    props.setProperty(QgsPalLayerSettings.Property.MinimumScale, QgsProperty.fromField("lim"))
    settings.setDataDefinedProperties(props)
    layer.labeling().setSettings(settings)
    reset_project(layer)
    rules, _ = flattener(10, 16)
    labelled = {}
    for rule in rules:
        if rule.get_attr("t") == 1:
            for zoom in range(rule.get_attr("o"), rule.get_attr("i") + 1):
                labelled.setdefault(zoom, set()).update(
                    _matching_ids(layer, rule.rule.filterExpression()))
    # 1:100000 is between zooms 11 and 12, 1:10000 between 14 and 15.
    assert labelled.get(11, set()) == set()
    assert labelled[12] == labelled[14] == {"P1", "P3"}
    assert labelled[15] == labelled[16] == {"P0", "P1", "P2", "P3"}


def _labels_per_feature(layer, rules, zoom, max_zoom):
    """{feature id: number of label rules labelling it} at browser ``zoom``
    (drawn from the tiles of min(floor(zoom), max_zoom))."""
    import math
    tile_zoom = min(math.floor(zoom), max_zoom)
    counts = {f["id"]: 0 for f in layer.getFeatures()}
    for rule in rules:
        if rule.get_attr("t") == 1 and rule.get_attr("o") <= tile_zoom <= rule.get_attr("i") \
                and rule.visibility.contains(zoom):
            for fid in _matching_ids(layer, rule.rule.filterExpression()):
                counts[fid] += 1
    return counts


@pytest.mark.parametrize("renderer", ["symbol_layers", "rules"])
def test_runs_shown_over_overlapping_intervals_label_each_feature_once(flattener, renderer):
    """Renderer runs of one zoom stretch shown over different browser
    intervals each got a label rule over their own interval: where the
    intervals overlapped, features drawn by both were labelled twice (a
    symbol layer enabled by a field beside one enabled by @map_scale; a
    "v" = 1 rule beside a filterless rule up to 1:2000)."""
    from qgis.core import (QgsMarkerSymbol, QgsProperty, QgsRuleBasedRenderer,
                           QgsSimpleMarkerSymbolLayer, QgsSingleSymbolRenderer, QgsSymbolLayer)
    layer = _points()
    everything, ones = ["P0", "P1", "P2", "P3"], ["P1", "P3"]
    if renderer == "symbol_layers":
        first = QgsSimpleMarkerSymbolLayer()
        first.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyLayerEnabled,
                                     QgsProperty.fromExpression('"v" = 1'))
        second = QgsSimpleMarkerSymbolLayer()
        second.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyLayerEnabled,
                                      QgsProperty.fromExpression("@map_scale < 2000"))
        layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol([first, second])))
        # QGIS labels every feature the renderer draws, whichever symbol
        # layers their conditions switch off.
        expected = {zoom: everything for zoom in (0.0, 14.0, 16.0, 17.0, 17.5, 22.0)}
    else:
        root = QgsRuleBasedRenderer.Rule(None)
        root.appendChild(QgsRuleBasedRenderer.Rule(QgsMarkerSymbol.createSimple({}),
                                                   filterExp='"v" = 1'))
        near = QgsRuleBasedRenderer.Rule(QgsMarkerSymbol.createSimple({}))
        near.setMaximumScale(2000)  # shown up to zoom 17.17, within the overzoom
        root.appendChild(near)
        layer.setRenderer(QgsRuleBasedRenderer(root))
        expected = {0.0: everything, 14.0: everything, 16.0: everything, 17.0: everything,
                    17.5: ones, 22.0: ones}
    _simple_labels(layer)
    reset_project(layer)
    for low, high in ((0, 16), (14, 19)):
        rules, _ = flattener(low, high)
        labels = [r for r in rules if r.get_attr("t") == 1]
        assert len({r.output_dataset for r in labels}) == len(labels)
        for zoom, ids in expected.items():
            if zoom < low:
                continue
            counts = _labels_per_feature(layer, rules, zoom, high)
            assert sorted(fid for fid, n in counts.items() if n) == ids, (low, high, zoom, counts)
            assert all(n <= 1 for n in counts.values()), (low, high, zoom, counts)


def test_geometry_generator_sub_layers_have_their_own_datasets(flattener):
    """A line geometry generator at symbol layer 0 with two sub-symbol
    layers, under a plain line: its second sub-layer got the dataset name of
    symbol layer 1 and was drawn on the source line."""
    from qgis.core import (QgsFeature, QgsGeometry, QgsGeometryGeneratorSymbolLayer,
                           QgsLineSymbol, QgsSimpleLineSymbolLayer, QgsSingleSymbolRenderer,
                           QgsVectorLayer)
    from qgis.PyQt.QtGui import QColor
    layer = QgsVectorLayer("LineString?crs=EPSG:3857", "lines", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt("LINESTRING(2120000 6020000, 2120400 6020000)"))
    layer.dataProvider().addFeatures([feature])
    generator = QgsGeometryGeneratorSymbolLayer.create(
        {"geometryModifier": "translate($geometry, 0, 100)", "SymbolType": "Line"})
    sub = QgsLineSymbol.createSimple({"line_color": "255,0,0", "line_width": "1.5"})
    sub.appendSymbolLayer(QgsSimpleLineSymbolLayer(QColor(0, 0, 255), 0.6))
    generator.setSubSymbol(sub)
    layer.setRenderer(QgsSingleSymbolRenderer(
        QgsLineSymbol([generator, QgsSimpleLineSymbolLayer(QColor(0, 255, 0), 2.0)])))
    reset_project(layer)
    rules, _ = flattener(0, 16)
    datasets = {}
    for rule in rules:
        # The generator's expression (wrapped to coerce its output to lines).
        generated = rule.pre_generator is not None and \
            "translate($geometry, 0, 100)" in rule.pre_generator
        datasets.setdefault(rule.output_dataset, []).append(
            (rule.rule.symbol().color().name(), generated))
    assert sorted(entry for group in datasets.values() for entry in group) == [
        ("#0000ff", True), ("#00ff00", False), ("#ff0000", True)]
    assert all(len(group) == 1 for group in datasets.values()), datasets


@pytest.mark.parametrize("max_scale", [0, 5000])
def test_a_marker_generator_on_points_has_its_own_dataset(flattener, max_scale):
    """A marker geometry generator beside a simple marker on a point layer
    (both draw points): the generator's points were named after symbol
    layer 0's dataset, so both rules wrote the generated (moved) points
    into one dataset and the simple marker was drawn at the moved position.
    Also with a rule shown only up to a zoom below the last tile zoom."""
    from qgis.core import (QgsGeometryGeneratorSymbolLayer, QgsMarkerSymbol,
                           QgsRuleBasedRenderer, QgsSimpleMarkerSymbolLayer)
    layer = _points()
    generator = QgsGeometryGeneratorSymbolLayer.create(
        {"geometryModifier": "translate($geometry, 30, 0)", "SymbolType": "Marker"})
    generator.setSubSymbol(QgsMarkerSymbol.createSimple({"name": "square", "color": "0,0,255"}))
    root = QgsRuleBasedRenderer.Rule(None)
    rule = QgsRuleBasedRenderer.Rule(QgsMarkerSymbol([QgsSimpleMarkerSymbolLayer(), generator]))
    rule.setMaximumScale(max_scale)
    root.appendChild(rule)
    layer.setRenderer(QgsRuleBasedRenderer(root))
    reset_project(layer)
    for low, high in ((0, 16), (14, 19)):
        rules, _ = flattener(low, high)
        kinds = {r.output_dataset: r.rule.symbol().symbolLayer(0).layerType() for r in rules}
        assert len(rules) == 2 and sorted(kinds.values()) == ["GeometryGenerator",
                                                              "SimpleMarker"], \
            [(r.output_dataset, r.rule.symbol().symbolLayer(0).layerType()) for r in rules]


@pytest.mark.parametrize("kind", ["Fill", "Line"])
def test_geometry_generator_sub_symbols_keep_their_own_opacity(flattener, kind):
    """QgsGeometryGeneratorSymbolLayer draws its sub-symbol with the
    sub-symbol's own opacity, never the parent symbol's (unlike marker
    lines or point patterns): a 0.4 parent made the generated fill and
    stroke translucent too. The parent's own layers keep its opacity."""
    from qgis.core import (QgsFillSymbol, QgsGeometryGeneratorSymbolLayer, QgsLineSymbol,
                           QgsSimpleFillSymbolLayer, QgsSimpleLineSymbolLayer,
                           QgsSingleSymbolRenderer)
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtGui import QColor
    generator = QgsGeometryGeneratorSymbolLayer.create(
        {"geometryModifier": "buffer($geometry, -15)", "SymbolType": kind})
    for own in (1.0, 0.5):
        layer = zoning_layer()
        if kind == "Fill":
            sub = QgsFillSymbol([QgsSimpleFillSymbolLayer(
                QColor(255, 230, 200), Qt.BrushStyle.SolidPattern, QColor(200, 0, 0),
                Qt.PenStyle.SolidLine, 2.0)])
        else:
            sub = QgsLineSymbol([QgsSimpleLineSymbolLayer(QColor(200, 0, 0), 2.0)])
        sub.setOpacity(own)
        generator.setSubSymbol(sub)
        base = QgsSimpleFillSymbolLayer(QColor(200, 200, 200), Qt.BrushStyle.SolidPattern,
                                        QColor("black"), Qt.PenStyle.NoPen)
        symbol = QgsFillSymbol([base, generator.clone()])
        symbol.setOpacity(0.4)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        reset_project(layer)
        rules, _ = flattener(0, 16)
        generated = [r for r in rules if r.pre_generator]
        plain = [r for r in rules if not r.pre_generator]
        # Fill: the fill and its stroke (a line); Line: the line.
        assert len(generated) == (2 if kind == "Fill" else 1), [r.output_dataset for r in rules]
        assert all(r.rule.symbol().opacity() == pytest.approx(own) for r in generated)
        assert [r.rule.symbol().opacity() for r in plain] == [pytest.approx(0.4)]


def test_split_fill_outline_keeps_its_map_unit_limits(flattener):
    """A map-unit fill outline capped at 0.5 mm (thin borders when zoomed
    in): the outline split off as a line lost the cap and grew without
    bound with the zoom."""
    from qgis.core import Qgis, QgsFillSymbol, QgsMapUnitScale, QgsSingleSymbolRenderer
    from fidelity import expressions as ex
    from fidelity.diagnostics import DiagnosticCollector
    from q2vt_plugin.src.core import maplibre_converter as mc  # pylint: disable=import-error
    layer = zoning_layer()
    symbol = QgsFillSymbol.createSimple({"color": "red", "outline_color": "black"})
    fill = symbol.symbolLayer(0)
    fill.setStrokeWidth(10)
    fill.setStrokeWidthUnit(Qgis.RenderUnit.MapUnits)
    limit = QgsMapUnitScale()
    limit.maxSizeMMEnabled = True
    limit.maxSizeMM = 0.5
    fill.setStrokeWidthMapUnitScale(limit)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    reset_project(layer)
    rules, _ = flattener(min_zoom=0, max_zoom=16)
    outlines = [r for r in rules if r.get_attr("c") == 1]
    assert len(outlines) == 1, [r.output_dataset for r in rules]
    line = outlines[0].rule.symbol().symbolLayer(0)
    assert line.widthMapUnitScale().maxSizeMMEnabled and line.widthMapUnitScale().maxSizeMM == 0.5
    mc.PropertyExtractor.context = mc.ConversionContext(DiagnosticCollector())
    width = mc.LinePropertyExtractor.get_line_width(line)
    for zoom in (16, 17, 19, 22):
        assert ex.evaluate_zoom_curve(width, zoom) == pytest.approx(0.5 * 96 / 25.4, rel=1e-3)


def test_rule_filters_ending_in_a_comment_combine(flattener):
    """QGIS accepts a rule filter ending in "-- note": the parent's and the
    child's filter AND-ed, the ELSE rule's NOT of its siblings and the
    parent's own features without its children's were cut short by it."""
    from qgis.core import QgsFillSymbol, QgsRuleBasedRenderer

    def fill(color):
        return QgsFillSymbol.createSimple({"color": color, "outline_style": "no"})
    layer = zoning_layer()
    root = QgsRuleBasedRenderer.Rule(None)
    zoned = QgsRuleBasedRenderer.Rule(fill("white"), 0, 0, '"zone" IS NOT NULL -- zoned')
    zoned.appendChild(QgsRuleBasedRenderer.Rule(fill("red"), 0, 0, "\"zone\" = 'K1' -- note"))
    other = QgsRuleBasedRenderer.Rule(fill("blue"), 0, 0, "ELSE")
    other.setIsElse(True)
    zoned.appendChild(other)
    root.appendChild(zoned)
    layer.setRenderer(QgsRuleBasedRenderer(root))
    reset_project(layer)
    rules, _ = flattener(0, 14)
    matched = {}
    for rule in rules:
        expression = QgsExpression(rule.rule.filterExpression())
        assert not expression.hasParserError(), rule.rule.filterExpression()
        ids = {f["id"] for f in layer.getFeatures(QgsFeatureRequest(expression))}
        matched.setdefault(rule.rule.symbol().color().name(), set()).update(ids)
    assert matched == {"#ffffff": {"A", "B", "D"}, "#ff0000": {"A"}, "#0000ff": {"B", "D"}}


def test_heatmap_weight_from_a_text_field_is_quoted_and_converted(plugin):
    """A heatmap weighted by a text field named "peso relativo" handed the
    bare name on as an expression (no parse); QGIS reads the attribute as a
    number, 1 when it is none."""
    from qgis.core import (QgsExpressionContext, QgsExpressionContextUtils, QgsFeature, QgsField,
                           QgsHeatmapRenderer, QgsVectorLayer)
    from qgis.PyQt.QtCore import QVariant
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener  # pylint: disable=import-error
    layer = QgsVectorLayer("Point?crs=EPSG:3857", "pts", "memory")
    layer.dataProvider().addAttributes([QgsField("peso relativo", QVariant.String)])
    layer.updateFields()
    heatmap = QgsHeatmapRenderer()
    heatmap.setWeightExpression("peso relativo")
    weight = QgsExpression(RulesFlattener._heatmap_weight(heatmap, layer.fields()))
    assert not weight.hasParserError(), weight.expression()
    context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    values = []
    for value in ("2.5", "abc", None):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([value])
        context.setFeature(feature)
        values.append(weight.evaluate(context))
    assert values == [2.5, 1, 1]


def test_features_of_an_invisible_else_rule_are_labelled(flattener):
    """A rule-based renderer's ELSE rule with a symbol that draws nothing (no
    fill, no outline): QGIS renders its features, invisibly, and labels
    them. The rule exported nothing, so its features got no labels (most
    zone codes of a plan missing)."""
    from qgis.core import QgsSimpleFillSymbolLayer
    from qgis.PyQt.QtCore import Qt
    layer = zoning_layer()
    rule_based(layer, [("first", "\"zone\" = 'K1'", 0, 0, True, "255,0,0"),
                       ("rest", "ELSE", 0, 0, True, "0,0,255")])
    rest = layer.renderer().rootRule().children()[1].symbol().symbolLayer(0)
    assert isinstance(rest, QgsSimpleFillSymbolLayer)
    rest.setBrushStyle(Qt.BrushStyle.NoBrush)
    rest.setStrokeStyle(Qt.PenStyle.NoPen)
    _simple_labels(layer)
    reset_project(layer)
    rules, _ = flattener(0, 16)
    labels = [r for r in rules if r.get_attr("t") == 1]
    everyone = sorted(f["id"] for f in layer.getFeatures())
    assert labels and all(sorted(_matching_ids(layer, r.rule.filterExpression())) == everyone
                          for r in labels)
    assert not any(r.get_attr("t") == 0 and r.get_attr("r") == 1 for r in rules)  # nothing drawn


def test_map_unit_labels_hidden_where_too_small_or_too_large_on_screen(flattener):
    """10 m text is 6 px at 1:6299 and 40 px at 1:945 (96 dpi): hidden
    beyond those with min_label_px=6 and QGIS's pixel size limits up to 40."""
    from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsMarkerSymbol, QgsPalLayerSettings,
                           QgsProject, QgsSingleSymbolRenderer, QgsVectorLayerSimpleLabeling)
    layer = _points()
    layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "id"
    text = settings.format()
    text.setSize(10)
    text.setSizeUnit(Qgis.RenderUnit.MetersInMapUnits)
    settings.setFormat(text)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    reset_project(layer)
    QgsProject.instance().setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    per_px = 10 * 96 / 0.0254

    def label_range(**options):
        rules, _ = flattener(0, 20, **options)
        return next(r.visibility for r in rules if r.get_attr("t") == 1)
    assert label_range().min_zoom == 0
    shown = label_range(min_label_px=6)
    assert shown.min_zoom == pytest.approx(zm.scale_to_zoom(per_px / 6)) and shown.max_zoom is None
    settings.fontLimitPixelSize, settings.fontMinPixelSize, settings.fontMaxPixelSize = True, 8, 40
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    shown = label_range(min_label_px=6)
    assert shown.min_zoom == pytest.approx(zm.scale_to_zoom(per_px / 8))
    assert shown.max_zoom == pytest.approx(zm.scale_to_zoom(per_px / 40))


def test_symbol_color_is_the_drawn_symbols(flattener):
    """@symbol_color is the colour of the symbol QGIS draws: a marker inside a
    marker line reads its line symbol's colour, a geometry generator's
    sub-symbol its own. Bound after the split, each one-layer piece read its
    own (Air Corridor's black letters were red)."""
    from qgis.core import (QgsFeature, QgsGeometry, QgsGeometryGeneratorSymbolLayer, QgsLineSymbol,
                           QgsMarkerLineSymbolLayer, QgsMarkerSymbol, QgsProperty,
                           QgsSimpleLineSymbolLayer, QgsSimpleMarkerSymbolLayer,
                           QgsSingleSymbolRenderer, QgsSymbolLayer, QgsVectorLayer)
    from qgis.PyQt.QtGui import QColor
    layer = QgsVectorLayer("LineString?crs=EPSG:3857&field=id:integer", "line", "memory")
    feature = QgsFeature(layer.fields())
    feature.setAttributes([1])
    feature.setGeometry(QgsGeometry.fromWkt("LINESTRING(0 0, 400 0)"))
    layer.dataProvider().addFeatures([feature])

    def markers():
        marker = QgsSimpleMarkerSymbolLayer()
        marker.setColor(QColor("red"))
        marker.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyFillColor,
                                      QgsProperty.fromExpression("@symbol_color"))
        line = QgsMarkerLineSymbolLayer()
        line.setSubSymbol(QgsMarkerSymbol([marker]))
        return line
    generator = QgsGeometryGeneratorSymbolLayer.create({"geometryModifier": "$geometry",
                                                        "SymbolType": "Line"})
    generator.setSubSymbol(QgsLineSymbol([QgsSimpleLineSymbolLayer(QColor("green")), markers()]))
    layer.setRenderer(QgsSingleSymbolRenderer(
        QgsLineSymbol([QgsSimpleLineSymbolLayer(QColor("blue")), markers(), generator])))
    reset_project(layer)
    rules, _ = flattener(14, 16)
    colors = set()
    for rule in rules:
        line = rule.rule.symbol().symbolLayer(0)
        if line.layerType() == "MarkerLine":
            prop = line.subSymbol().symbolLayer(0).dataDefinedProperties().property(
                QgsSymbolLayer.Property.PropertyFillColor)
            colors.add((bool(rule.pre_generator), prop.expressionString()))
    assert colors == {(False, "'0,0,255,255'"), (True, "'0,128,0,255'")}


def test_a_fill_effect_stays_on_its_split_outline(flattener):
    """A simple fill's outline is split into a line rule; the fill's paint
    effect (a firefly blur) is drawn on it too: it was lost."""
    from qgis.core import (QgsBlurEffect, QgsEffectStack, QgsFillSymbol, QgsSimpleFillSymbolLayer,
                           QgsSingleSymbolRenderer)
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtGui import QColor
    layer = zoning_layer()
    fill = QgsSimpleFillSymbolLayer(QColor("white"), Qt.BrushStyle.NoBrush, QColor("red"))
    fill.setStrokeWidth(1.8)
    fill.setPaintEffect(QgsEffectStack(QgsBlurEffect()))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([fill])))
    reset_project(layer)
    rules, _ = flattener(14, 16)
    lines = [r.rule.symbol().symbolLayer(0) for r in rules if r.get_attr("c") == 1]
    assert lines and all(type(l.paintEffect()).__name__ == "QgsEffectStack" and
                         l.paintEffect().effectList()[0].type() == "blur" for l in lines)


def test_data_defined_map_unit_labels_hidden_where_too_small(flattener):
    """A data-defined size (e.g. ``if("fekves" = 2, 6, 4)``): each feature's
    label is hidden where its own text is below min_label_px."""
    from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsMarkerSymbol, QgsPalLayerSettings,
                           QgsProject, QgsProperty, QgsSingleSymbolRenderer, QgsVectorLayerSimpleLabeling)
    layer = _points()   # v: 0, 1, 0, 1
    layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple({})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "id"
    text = settings.format()
    text.setSize(10)
    text.setSizeUnit(Qgis.RenderUnit.MetersInMapUnits)
    settings.setFormat(text)
    settings.dataDefinedProperties().setProperty(
        QgsPalLayerSettings.Property.Size, QgsProperty.fromExpression('if("v" = 1, 20, 10)'))
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    reset_project(layer)
    QgsProject.instance().setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    rules, _ = flattener(0, 20, min_label_px=6)
    labels = [r for r in rules if r.get_attr("t") == 1]
    # 10 m text is 6 px at 1:6299, 20 m text at 1:12598: one zoom earlier
    # (the condition is taken per whole zoom).
    small = min(r.visibility.min_zoom for r in labels if "P0" in _matching_ids(layer, r.rule.filterExpression()))
    large = min(r.visibility.min_zoom for r in labels if "P1" in _matching_ids(layer, r.rule.filterExpression()))
    assert abs(small - zm.scale_to_zoom(10 * 96 / 0.0254 / 6)) <= 1
    assert small - large == 1
