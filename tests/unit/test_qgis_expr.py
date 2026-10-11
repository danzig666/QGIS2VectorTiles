import pytest

from fidelity.qgis_expr import bind_geometry, substitute_geometry, with_map_scale


def test_with_map_scale_does_not_touch_literals():
    expr = "concat('@map_scale is ', @map_scale)"
    out = with_map_scale(expr, 1000)
    assert out == "with_variable('map_scale', 1000.0, (concat('@map_scale is ', @map_scale)))"


def test_with_map_scale_noop_without_variable():
    assert with_map_scale('"size" * 2', 5) == '"size" * 2'


def test_geometry_tokens_swapped_outside_literals_only():
    expr = "buffer($geometry, 5) || '$geometry' || \"@geometry\" || @Geometry"
    assert substitute_geometry(expr, "G") == \
        "buffer((G), 5) || '$geometry' || \"@geometry\" || (G)"


def test_geometry_prefix_names_are_not_replaced():
    assert substitute_geometry("@geometry_part_num + 1", "G") == "@geometry_part_num + 1"


def test_bind_geometry():
    out = bind_geometry("centroid($geometry)", "transform(@geometry, 'A', 'B')")
    assert out == "centroid((transform(@geometry, 'A', 'B')))"


def test_in_layer_crs_rebinds_geometry_and_measures():
    from fidelity.qgis_expr import in_layer_crs
    out = in_layer_crs("round($area) || ' m2' || '$area'", "EPSG:3857", "EPSG:23700", True)
    assert out == ("round(area(transform(@geometry, 'EPSG:3857', 'EPSG:23700'))) "
                   "|| ' m2' || '$area'")
    kept = in_layer_crs("$area", "EPSG:3857", "EPSG:23700", False)
    assert kept == "$area"
    assert in_layer_crs("$x", "EPSG:3857", "EPSG:23700", False) == \
        "x(transform(@geometry, 'EPSG:3857', 'EPSG:23700'))"
    assert in_layer_crs("$area", "EPSG:3857", "EPSG:3857", True) == "$area"


def test_enabled_condition_and_filters():
    from fidelity.qgis_expr import and_filters, enabled_condition
    assert and_filters("a", "", None, "b") == "(a) AND (b)"
    assert "IS NULL" in enabled_condition("x = 1")


def test_with_map_scale_binds_the_zoom_variables_of_the_scale():
    """QGIS's map scope sets @zoom_level and @vector_tile_zoom from the map
    scale (QgsVectorTileUtils, zoom 0 = 1:559082264); they were NULL."""
    from fidelity.qgis_expr import vector_tile_zoom, zoom_level
    assert vector_tile_zoom(9404) == pytest.approx(14.8594, abs=1e-4)
    assert zoom_level(9404) == 15 and zoom_level(1e12) == 0
    out = with_map_scale("@zoom_level >= 15 AND @vector_tile_zoom < 16", 9404)
    assert out.startswith("with_variable('zoom_level', 15, (with_variable('vector_tile_zoom', 14.85")


def test_scale_thresholds_are_the_scales_where_a_comparison_switches():
    from fidelity.qgis_expr import scale_thresholds
    assert scale_thresholds('@map_scale < 2000 OR "v" = 1') == [2000.0]
    assert scale_thresholds("CASE WHEN 3000 < @map_scale THEN 10 ELSE 3 END") == [3000.0]
    assert scale_thresholds("@map_scale BETWEEN 1000 AND 5000") == [1000.0, 5000.0]
    # @zoom_level >= 15 from vector tile zoom 14.5; > 15 from 15.5.
    assert scale_thresholds("@zoom_level >= 15") == [pytest.approx(559082264.0287178 / 2 ** 15.5)]
    assert scale_thresholds("@zoom_level > 15") == [pytest.approx(559082264.0287178 / 2 ** 16.5)]
    # A value growing with the scale, or a calculated threshold: every scale.
    assert scale_thresholds("@map_scale / 1000") is None
    assert scale_thresholds("2 * @map_scale < 1000") is None
    assert scale_thresholds("@map_scale < 1000 * 2") is None
    assert scale_thresholds('"map_scale" > 3') == []
