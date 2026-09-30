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
