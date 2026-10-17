"""Style layers cut into zoom ranges (a map-unit marker's sprite per zoom)
joined again where nothing tells the pieces apart (fidelity.layer_merge)."""

from fidelity.layer_merge import join_zoom_split_layers


def _marker(zoom, image="m", size=1.0, end=None):
    return {"id": f"s_z{zoom}", "type": "symbol", "source": "t", "source-layer": "s",
            "minzoom": zoom, "maxzoom": zoom + 1 if end is None else end,
            "layout": {"icon-image": image, "icon-size": size}, "paint": {"icon-opacity": 1}}


def test_identical_pieces_become_one_layer_over_their_zooms():
    out = join_zoom_split_layers([_marker(z) for z in range(0, 6)])
    assert len(out) == 1
    assert (out[0]["id"], out[0]["minzoom"], out[0]["maxzoom"]) == ("s_z0", 0, 6)
    assert out[0]["layout"]["icon-image"] == "m"


def test_pieces_with_their_own_image_join_as_a_zoom_step():
    layers = [_marker(12, "a"), _marker(13, "a"), _marker(14, "b"), _marker(15, "c", end=24)]
    out = join_zoom_split_layers(layers)
    assert len(out) == 1
    assert out[0]["layout"]["icon-image"] == ["step", ["zoom"], "a", 14, "b", 15, "c"]
    assert (out[0]["minzoom"], out[0]["maxzoom"]) == (12, 24)


def test_pieces_that_differ_otherwise_stay_apart():
    # Another size, a fractional boundary with another image, a layer drawn
    # between them, a gap in the zooms, another layer's pieces.
    assert len(join_zoom_split_layers([_marker(1), _marker(2, size=2.0)])) == 2
    fractional = _marker(10, "b")
    fractional["minzoom"] = 9.5
    first = _marker(9, "a", end=9.5)
    assert len(join_zoom_split_layers([first, fractional])) == 2
    between = {"id": "other", "type": "line", "source": "t", "source-layer": "o"}
    assert len(join_zoom_split_layers([_marker(1), between, _marker(2)])) == 3
    assert len(join_zoom_split_layers([_marker(1), _marker(3)])) == 2
    other = _marker(2)
    other["id"] = "t_z2"
    assert len(join_zoom_split_layers([_marker(1), other])) == 2
    data_defined = _marker(2, ["get", "img"])
    assert len(join_zoom_split_layers([_marker(1), data_defined])) == 2
    # Data-defined images (sprite variants) join only when they are the same.
    variants = lambda zoom, suffix: _marker(zoom, ["match", ["get", "c"], "a", f"m_a{suffix}", f"m{suffix}"])
    assert len(join_zoom_split_layers([variants(1, "_1"), variants(2, "_2")])) == 2
    assert len(join_zoom_split_layers([variants(1, "_1"), variants(2, "_1")])) == 1
