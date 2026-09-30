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
