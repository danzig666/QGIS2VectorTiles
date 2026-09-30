"""PR-12: renderer order-by becomes a per-feature drawing rank and sort key."""

from qgis.core import QgsFeatureRequest, QgsVectorLayer

from q2vt_fixtures import zoning_layer


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
