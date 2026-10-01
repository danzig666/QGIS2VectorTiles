"""visible_labels.mjs: the label point of the visible part of a polygon
(QGIS "Centroid: visible polygon"): the centroid when it lies inside, else
the middle of the widest span through the middle of the extent."""

import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODULE = os.path.join(ROOT, "resources", "ml_viewer", "visible_labels.mjs")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")


def _label_point(rings):
    script = (f"import {{ labelPoint }} from {json.dumps('file://' + MODULE)};"
              f"console.log(JSON.stringify(labelPoint({json.dumps(rings)})));")
    run = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    return json.loads(run.stdout)


def test_centroid_of_a_square():
    assert _label_point([[[0, 0], [4, 0], [4, 4], [0, 4]]]) == pytest.approx([2, 2])


def test_hole_moves_the_centroid():
    outer = [[0, 0], [6, 0], [6, 2], [0, 2]]
    hole = [[0.5, 0.5], [0.5, 1.5], [2.5, 1.5], [2.5, 0.5]]  # opposite winding
    x, y = _label_point([outer, hole])
    assert x > 3 and y == pytest.approx(1)


def test_u_shape_falls_back_to_an_interior_point():
    # The centroid of a U lies in its notch: the label goes inside instead.
    u_shape = [[0, 0], [6, 0], [6, 6], [4, 6], [4, 2], [2, 2], [2, 6], [0, 6]]
    x, y = _label_point([u_shape])
    assert y == pytest.approx(3) and (x < 2 or x > 4)


def _pan(views, margin=0.0):
    """Label points of one square polygon (one z0 tile) for successive views
    (world rectangles), with positions kept between them."""
    script = f"""
import {{ labelPoints, newLabelState }} from {json.dumps('file://' + MODULE)};
const ring = [[512, 512], [3584, 512], [3584, 3584], [512, 3584]].map(([x, y]) => ({{x, y}}));
const feature = {{ _x: 0, _y: 0, _z: 0, properties: {{ q2vt_orig_id: 7 }},
  _vectorTileFeature: {{ extent: 4096, loadGeometry: () => [ring] }} }};
const maplibregl = {{ MercatorCoordinate: class {{
  constructor(x, y) {{ this.x = x; this.y = y; }}
  toLngLat() {{ return {{ lng: this.x, lat: this.y }}; }} }} }};
const state = newLabelState();
const out = {json.dumps(views)}.map((view) => labelPoints([feature], view, maplibregl,
  {{ state, margin: {margin} }}).features.map((f) => [f.id, ...f.geometry.coordinates]));
console.log(JSON.stringify(out));
"""
    run = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    return json.loads(run.stdout)


def test_label_stays_put_while_its_point_is_visible():
    first, panned, away = _pan([[0, 0, 1, 1], [0.3, 0.3, 1, 1], [0.6, 0.6, 1, 1]], margin=0.02)
    assert first[0][1:] == pytest.approx([0.5, 0.5])
    assert panned[0][1:] == pytest.approx([0.5, 0.5])  # not the visible part's centroid
    # Off screen now: moved to the visible part (same feature id).
    assert away[0][1:] == pytest.approx([0.7375, 0.7375]) and away[0][0] == first[0][0]


def test_label_at_the_screen_edge_moves_in():
    _, edge = _pan([[0, 0, 1, 1], [0.49, 0.49, 1, 1]], margin=0.02)
    assert edge[0][1] > 0.6


def _unless_placed(value):
    script = (f"import {{ enableOverlapFallback }} from {json.dumps('file://' + MODULE)};"
              "const added = [];"
              "const map = { getStyle: () => ({ layers: [{ id: 'a', type: 'symbol', "
              "metadata: { 'q2vt:overlap': 'if-required' }, layout: { 'text-variable-anchor': "
              f"['center', 'top'], 'text-radial-offset': 0 }}, paint: {{ 'text-opacity': {json.dumps(value)} }} }}] }}),"
              "addLayer: (l, before) => added.push([l, before]), on: () => {} };"
              "enableOverlapFallback(map); console.log(JSON.stringify(added));")
    run = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    return json.loads(run.stdout)


def test_overlap_fallback_copy():
    [[copy, before]] = _unless_placed(0.8)
    placed = ["boolean", ["feature-state", "q2vtPlaced"], False]
    assert before == "a" and copy["id"] == "a_q2vt_overlap"
    assert copy["layout"]["text-allow-overlap"] is True and copy["layout"]["text-ignore-placement"] is True
    assert copy["layout"]["text-anchor"] == "center" and "text-variable-anchor" not in copy["layout"]
    assert copy["paint"]["text-opacity"] == ["case", placed, 0, 0.8]
    assert copy["paint"]["icon-opacity"] == ["case", placed, 0, 1]


def test_overlap_fallback_keeps_zoom_curves_on_top():
    curve = ["interpolate", ["linear"], ["zoom"], 10, 0.5, 14, 1]
    [[copy, _]] = _unless_placed(curve)
    placed = ["boolean", ["feature-state", "q2vtPlaced"], False]
    assert copy["paint"]["text-opacity"] == ["interpolate", ["linear"], ["zoom"],
                                             10, ["case", placed, 0, 0.5], 14, ["case", placed, 0, 1]]
