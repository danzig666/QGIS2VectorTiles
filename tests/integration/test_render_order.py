"""PR-12: renderer order-by becomes a per-feature drawing rank and sort key;
QGIS's feature order across the rules of a layer (feature-order strata)."""

import json
import os

from qgis.core import (QgsCategorizedSymbolRenderer, QgsFeature, QgsFeatureRequest, QgsField,
                       QgsFillSymbol, QgsGeometry, QgsLineSymbol, QgsProcessingFeedback, QgsProject,
                       QgsRectangle, QgsRendererCategory, QgsRuleBasedRenderer,
                       QgsSimpleLineSymbolLayer, QgsVectorLayer)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor

from q2vt_fixtures import SQUARES, reset_project, to_geopackage, zoning_layer

EXTENT = QgsRectangle(2119000, 6019000, 2123000, 6023000)


def test_order_by_rank_follows_qgis_request_order(plugin, tmp_path):
    from q2vt_plugin.src.core.rules_exporter import ORDER_FIELD, RulesExporter
    layer = zoning_layer(path=str(tmp_path / "z.gpkg"))
    renderer = layer.renderer()
    renderer.setOrderBy(QgsFeatureRequest.OrderBy(
        [QgsFeatureRequest.OrderByClause('"width"', False)]))
    renderer.setOrderByEnabled(True)
    order_by = RulesExporter._order_by(layer)
    assert order_by == (('"width"', False, True),)  # descending defaults to nulls first
    RulesExporter._add_order_field(str(tmp_path / "z.gpkg"), order_by)
    saved = QgsVectorLayer(str(tmp_path / "z.gpkg"), "z", "ogr")
    ranks = {f["id"]: f[ORDER_FIELD] for f in saved.getFeatures()}
    # Widths grow with the fixture index; descending order draws D first.
    assert ranks == {"D": 0, "C": 1, "B": 2, "A": 3}


def test_ordered_styles_get_sort_keys(plugin):
    from q2vt_plugin.src.core import maplibre_converter as mc
    from q2vt_plugin.src.core.rules_exporter import ORDER_FIELD
    exporter = mc.QgisMapLibreStyleExporter.__new__(mc.QgisMapLibreStyleExporter)
    layers = [{"type": "fill", "layout": {}}, {"type": "line", "layout": {}},
              {"type": "symbol", "layout": {"text-field": "x"}}]
    exporter._apply_draw_order(layers)
    assert layers[0]["layout"]["fill-sort-key"] == ["to-number", ["get", ORDER_FIELD], 0]
    assert "line-sort-key" in layers[1]["layout"]
    assert "symbol-sort-key" not in layers[2]["layout"]


# --- feature order across rules --------------------------------------------------------------

def _square(x, y, size=10.0):
    return QgsGeometry.fromWkt(f"POLYGON(({x} {y}, {x + size} {y}, {x + size} {y + size}, "
                               f"{x} {y + size}, {x} {y}))")


def test_later_feature_of_earlier_rule_is_lifted(plugin):
    from fidelity import feature_order as fo
    # Drawing order A (rule 0), C (rule 1, overlaps A and B), B (rule 0): QGIS
    # draws B above C and C above A. A stays in stratum 0, under C.
    a, c, b = _square(0, 0), _square(5, 5), _square(10, 10)
    assert fo.strata([(1, 0, a), (2, 1, c), (3, 0, b)]) == {(3, 0): 1}
    # A chain: each later feature of the earlier rule climbs one stratum more.
    d, e = _square(15, 15), _square(20, 20)
    assert fo.strata([(1, 0, a), (2, 1, c), (3, 0, b), (4, 1, d), (5, 0, e)]) == \
        {(3, 0): 1, (4, 1): 1, (5, 0): 2}
    # A feature's later rules stay above its earlier ones.
    assert fo.strata([(1, 1, a), (2, 0, c), (2, 2, c)]) == {(2, 0): 1, (2, 2): 1}


def test_touching_or_same_rule_features_are_not_lifted(plugin):
    from fidelity import feature_order as fo
    squares = [QgsGeometry.fromWkt(wkt) for _, _, wkt in SQUARES]  # neighbours only touch
    assert fo.strata([(i, (i + 1) % 2, g) for i, g in enumerate(squares)]) == {}
    assert fo.strata([(i, 3 - i, g) for i, g in enumerate(squares)]) == {}
    assert fo.strata([(1, 0, _square(0, 0)), (2, 0, _square(5, 5))]) == {}  # same rule
    assert fo.strata([(1, 0, _square(0, 0)), (1, 1, _square(0, 0))]) == {}  # one feature
    # A sliver narrower than twice the margin (simplified neighbours) is no overlap.
    sliver = QgsGeometry.fromWkt("POLYGON((9.6 0, 20 0, 20 10, 9.6 10, 9.6 0))")
    assert fo.strata([(1, 1, _square(0, 0)), (2, 0, sliver)], margin=0.25) == {}
    assert fo.strata([(1, 1, _square(0, 0)), (2, 0, sliver)]) == {(2, 0): 1}


def _line(*points):
    return QgsGeometry.fromWkt("LINESTRING(" + ", ".join(f"{x} {y}" for x, y in points) + ")")


def test_touching_lines_are_lifted(plugin):
    """A stroke has a width: a street ending on a main road (a T-junction)
    covers half of it, and lines meeting end to end overlap at their caps."""
    from fidelity import feature_order as fo
    street, main = _line((5, 0), (5, 10)), _line((0, 10), (10, 10))
    # QGIS draws the street, then the main road (an earlier rule) above it.
    assert fo.strata([(1, 2, street), (2, 0, main)]) == {(2, 0): 1}
    assert fo.strata([(1, 2, _line((0, 0), (5, 0))), (2, 0, _line((5, 0), (9, 0)))]) == {(2, 0): 1}
    # A junction vertex moved off the main road by the data simplification.
    near = _line((5, 0), (5, 9.95))
    assert fo.strata([(1, 2, near), (2, 0, main)], margin=0.1) == {(2, 0): 1}
    assert fo.strata([(1, 2, near), (2, 0, main)], margin=0.01) == {}
    # Touching polygons (a partition) still do not count.
    assert fo.strata([(1, 1, _square(0, 0)), (2, 0, _square(10, 0))]) == {}
    # Crossings only (the fallback beyond the limits).
    assert fo.strata([(1, 2, street), (2, 0, main)], touching_lines=False) == {}
    assert fo.strata([(1, 2, near), (2, 0, main)], margin=0.1, touching_lines=False) == {}
    crossing = _line((5, 0), (5, 20))
    assert fo.strata([(1, 2, crossing), (2, 0, main)], touching_lines=False) == {(2, 0): 1}


# Drawing order A, C, B: conservation C overlaps the forests A and B, which
# touch at a corner only.
LANDUSE = [("forest", _square(2120000, 6020000, 600)),
           ("conservation", _square(2120300, 6020300, 600)),
           ("forest", _square(2120600, 6020600, 600))]


def _landuse(path, symbol_levels=False, features=LANDUSE):
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "landuse", "memory")
    layer.dataProvider().addAttributes([QgsField("landuse", QVariant.String)])
    layer.updateFields()
    for use, geometry in features:
        feature = QgsFeature(layer.fields())
        feature.setAttributes([use])
        feature.setGeometry(geometry)
        layer.dataProvider().addFeature(feature)
    saved = to_geopackage(layer, path)
    renderer = QgsCategorizedSymbolRenderer("landuse", [
        QgsRendererCategory("forest", QgsFillSymbol.createSimple(
            {"color": "255,0,0", "outline_color": "128,0,0"}), "Forest"),
        QgsRendererCategory("conservation", QgsFillSymbol.createSimple(
            {"color": "0,0,255", "outline_style": "no"}), "Conservation")])
    renderer.setUsingSymbolLevels(symbol_levels)
    saved.setRenderer(renderer)
    return saved


def _export_rules(tmp_path, layer, name="utils"):
    """Renderer rules after the rule export, bottom first, and the diagnostics."""
    from q2vt_plugin.src.core.rules_exporter import RulesExporter
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener
    from q2vt_plugin.src.core.tiles_styler import TilesStyler
    from fidelity.diagnostics import DiagnosticCollector
    reset_project(layer)
    diags = DiagnosticCollector()
    rules = RulesFlattener(10, 14, str(tmp_path), QgsProcessingFeedback(), diags).flatten_all_rules()
    utils = tmp_path / name
    utils.mkdir()
    _, rules = RulesExporter(rules, EXTENT, 0, 14, str(utils), 0, QgsProcessingFeedback(),
                             diagnostics=diags).export()
    ordered = TilesStyler.draw_order(TilesStyler.__new__(TilesStyler), rules)
    return [r for r in ordered if r.get_attr("t") == 0], diags, utils


def _orig_ids(utils, dataset):
    layer = QgsVectorLayer(str(utils / f"{dataset}.gpkg"), "ids", "ogr")
    return sorted(f["q2vt_orig_id"] for f in layer.getFeatures())


def _fills(rules):
    return [(r.rule.symbol().color().name(), r.feature_filter) for r in rules if r.get_attr("c") == 2]


def test_categorized_overlap_keeps_feature_order(plugin, tmp_path):
    rules, diags, utils = _export_rules(tmp_path, _landuse(str(tmp_path / "landuse.gpkg")))
    fills = [r for r in rules if r.get_attr("c") == 2]
    _, b = _orig_ids(utils, fills[0].output_dataset)  # the forests A and B
    without_b = ["match", ["get", "q2vt_orig_id"], [b], False, True]
    only_b = ["match", ["get", "q2vt_orig_id"], [b], True, False]
    # Forest B is drawn above conservation C by a copy of the forest style
    # layers; A stays under C, as in QGIS.
    assert _fills(rules) == [("#ff0000", without_b), ("#0000ff", None), ("#ff0000", only_b)]
    assert fills[2].rule.description() == fills[0].rule.description() + "_k01"
    assert fills[2].output_dataset == fills[0].output_dataset
    # The forest outline (its own component) is lifted with the fill.
    outlines = [r for r in rules if r.get_attr("c") == 1]
    assert [r.feature_filter for r in outlines] == [without_b, only_b]
    assert rules.index(outlines[1]) > rules.index(fills[1])
    assert not diags.by_code("Q2VT_FEATURE_ORDER_ACROSS_RULES")

    # Symbol levels: QGIS draws category by category, as the style layers do.
    rules, _, _ = _export_rules(tmp_path, _landuse(str(tmp_path / "levels.gpkg"), True), "levels")
    assert _fills(rules) == [("#ff0000", None), ("#0000ff", None)]
    assert not [r for r in rules if "_k" in r.rule.description()]


def test_simplified_neighbours_are_not_lifted(plugin, tmp_path):
    """A T-junction vertex 0.1 m off the edge of the big neighbour: the data
    simplification drops it from that polygon only, and the simplified
    neighbours overlap in a sliver (up to 0.1 m wide) that QGIS does not
    draw. Without the margin both later features climbed a stratum."""
    def polygon(*points):
        ring = ", ".join(f"{2120000 + x} {6020000 + y}" for x, y in points + points[:1])
        return QgsGeometry.fromWkt(f"POLYGON(({ring}))")
    features = [("conservation", polygon((0, 10), (10, 9.9), (10, 20), (0, 20))),
                ("forest", polygon((0, 0), (20, 0), (20, 10), (10, 9.9), (0, 10))),
                ("conservation", polygon((10, 9.9), (20, 10), (20, 20), (10, 20)))]
    rules, diags, _ = _export_rules(tmp_path, _landuse(str(tmp_path / "t.gpkg"), features=features))
    assert _fills(rules) == [("#ff0000", None), ("#0000ff", None)]
    assert not diags.by_code("Q2VT_FEATURE_ORDER_ACROSS_RULES")


# A street (fid 1) ending on a main road (fid 2): a T-junction.
JUNCTION = [("street", ((500, 0), (500, 1000))), ("main", ((0, 1000), (1000, 1000)))]


def _roads(path, features=JUNCTION):
    """Rule-based roads with symbol levels: casings in pass 0, fills in pass 1."""
    layer = QgsVectorLayer("LineString?crs=EPSG:3857", "roads", "memory")
    layer.dataProvider().addAttributes([QgsField("kind", QVariant.String)])
    layer.updateFields()
    for kind, points in features:
        feature = QgsFeature(layer.fields())
        feature.setAttributes([kind])
        feature.setGeometry(_line(*((2120000 + x, 6020000 + y) for x, y in points)))
        layer.dataProvider().addFeature(feature)
    saved = to_geopackage(layer, path)
    root = QgsRuleBasedRenderer.Rule(None)
    for kind, casing, fill in (("main", "#804000", "#ffc040"), ("street", "#808080", "#ffffff")):
        symbol = QgsLineSymbol()
        symbol.deleteSymbolLayer(0)
        for color, width, draw_pass in ((casing, 2.0, 0), (fill, 1.4, 1)):
            line = QgsSimpleLineSymbolLayer(QColor(color), width)
            line.setRenderingPass(draw_pass)
            symbol.appendSymbolLayer(line)
        root.appendChild(QgsRuleBasedRenderer.Rule(symbol, 0, 0, f"\"kind\" = '{kind}'", kind))
    renderer = QgsRuleBasedRenderer(root)
    renderer.setUsingSymbolLevels(True)
    saved.setRenderer(renderer)
    return saved


def test_rule_based_symbol_levels_keep_feature_order_at_junctions(plugin, tmp_path):
    """Within a pass QGIS draws feature by feature: the main road, drawn after
    the street ending on it, stays unbroken although its rule comes first."""
    rules, diags, utils = _export_rules(tmp_path, _roads(str(tmp_path / "roads.gpkg")))
    [main_id] = _orig_ids(utils, rules[0].output_dataset)  # the main road's casing
    without_main = ["match", ["get", "q2vt_orig_id"], [main_id], False, True]
    only_main = ["match", ["get", "q2vt_orig_id"], [main_id], True, False]
    for draw_pass, (main_color, street_color) in ((0, ("#804000", "#808080")),
                                                  (1, ("#ffc040", "#ffffff"))):
        lines = [(r.rule.symbol().color().name(), r.feature_filter) for r in rules
                 if r.get_attr("c") == 1 and r.order[1] == draw_pass]
        assert lines == [(main_color, without_main), (street_color, None), (main_color, only_main)]
    assert not diags.by_code("Q2VT_FEATURE_ORDER_ACROSS_RULES")


def test_line_layer_beyond_the_limits_keeps_the_crossings(plugin, tmp_path, monkeypatch):
    """Too many lines lifted with the junctions: the crossings keep QGIS's
    order, the junctions rule order (a note), instead of rule order everywhere."""
    from fidelity import feature_order as fo
    monkeypatch.setattr(fo, "MAX_LIFTED", 3)  # both main roads in both passes: 4
    features = JUNCTION + [("street", ((2500, 0), (2500, 2000))),   # fid 3 crosses
                           ("main", ((2000, 1000), (3000, 1000)))]  # main road 4
    rules, diags, utils = _export_rules(tmp_path, _roads(str(tmp_path / "roads.gpkg"), features))
    [diag] = diags.by_code("Q2VT_FEATURE_ORDER_ACROSS_RULES")
    assert diag.severity.value == "info" and "(4 features in 1 strata" in diag.message
    _, crossed = _orig_ids(utils, rules[0].output_dataset)  # the main roads 2 and 4
    fills = [r.feature_filter for r in rules if r.get_attr("c") == 1 and r.order[1] == 1]
    assert fills == [["match", ["get", "q2vt_orig_id"], [crossed], False, True], None,
                     ["match", ["get", "q2vt_orig_id"], [crossed], True, False]]


def test_feature_order_limit_reports_diagnostic(plugin, tmp_path, monkeypatch):
    from fidelity import feature_order as fo
    monkeypatch.setattr(fo, "MAX_LIFTED", 0)
    rules, diags, _ = _export_rules(tmp_path, _landuse(str(tmp_path / "landuse.gpkg")))
    [diag] = diags.by_code("Q2VT_FEATURE_ORDER_ACROSS_RULES")
    assert diag.severity.value == "warning" and "1 feature(s)" in diag.message
    assert _fills(rules) == [("#ff0000", None), ("#0000ff", None)]
    # Too many features to check: a note, and the layer keeps rule order.
    monkeypatch.setattr(fo, "MAX_FEATURES", 2)
    rules, diags, _ = _export_rules(tmp_path, _landuse(str(tmp_path / "big.gpkg")), "big")
    [diag] = diags.by_code("Q2VT_FEATURE_ORDER_ACROSS_RULES")
    assert diag.severity.value == "info" and "too many" in diag.message
    assert _fills(rules) == [("#ff0000", None), ("#0000ff", None)]


def test_feature_filters_become_style_filters(plugin, tmp_path):
    from q2vt_plugin.src.core import maplibre_converter as mc
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-error
    from publishing import qgis_model
    from publishing.models import LayerConfig, PublicationProfile
    stratum = ["match", ["get", "q2vt_orig_id"], [3], True, False]
    layers = [{"type": "fill"}, {"type": "line", "filter": ["==", ["get", "a"], 1]}]
    mc.QgisMapLibreStyleExporter._apply_feature_filter(layers, stratum)
    assert layers[0]["filter"] == stratum
    assert layers[1]["filter"] == ["all", ["==", ["get", "a"], 1], stratum]

    # Through the whole export: style layers bottom first.
    layer = _landuse(str(tmp_path / "landuse.gpkg"))
    reset_project(layer)
    out = tmp_path / "out"
    out.mkdir()
    exporter = QGIS2VectorTiles(min_zoom=10, max_zoom=14, extent=EXTENT, output_dir=str(out),
                                feedback=QgsProcessingFeedback(), serve=False, background_type=2)
    result = exporter.convert_project_to_vector_tiles()
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    drawn = [l for l in style["layers"] if l["type"] in ("fill", "line")]
    fills = [l for l in drawn if l["type"] == "fill"]
    red, blue = "rgba(255, 0, 0, 1.0)", "rgba(0, 0, 255, 1.0)"
    assert [l["paint"]["fill-color"] for l in fills] == [red, blue, red]
    lifted = fills[2]["filter"]
    assert lifted[:2] == ["match", ["get", "q2vt_orig_id"]] and lifted[3:] == [True, False]
    assert fills[0]["filter"] == ["match", ["get", "q2vt_orig_id"], lifted[2], False, True]
    assert "filter" not in fills[1]
    # The copy is the forest's style layer again (same paint, data, images).
    assert fills[2]["id"] == fills[0]["id"] + "_k01"
    assert {k: v for k, v in fills[2].items() if k not in ("id", "filter")} == \
        {k: v for k, v in fills[0].items() if k not in ("id", "filter")}
    # The forest outline: under conservation C, and again above it.
    assert [l["type"] for l in drawn] == ["fill", "line", "fill", "fill", "line"]
    assert drawn[4]["id"] == drawn[1]["id"] + "_k01" and drawn[4]["filter"] == lifted

    # Publishing: the copy belongs to the forest rule (layer and rule
    # toggles, the "visible only" legend and 3D find it by its component).
    profile = PublicationProfile(title="Order", slug="order")
    profile.layers = [LayerConfig(layer.id())]
    model = qgis_model.logical_model(QgsProject.instance(), profile, exporter.rules, style)
    owner = {sid: c for c in model["components"] for sid in c["styleLayerIds"]}
    assert owner[fills[2]["id"]]["ruleIds"] == owner[fills[0]["id"]]["ruleIds"] != []
    assert owner[fills[2]["id"]]["ruleIds"] != owner[fills[1]["id"]]["ruleIds"]
