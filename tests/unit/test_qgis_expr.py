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
