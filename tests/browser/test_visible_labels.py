"""visible_labels.mjs: the label point of the visible part of a polygon
(QGIS "Centroid: visible polygon"): the centroid when it lies inside, else
the middle of the widest span through the middle of the extent."""

import json
import math
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


def test_overlap_if_required_uses_cooperative_overlap():
    # No copy layers (switched by the placement result, they lagged behind it
    # and labels blinked while the map moved): MapLibre's cooperative overlap.
    script = (f"import {{ enableOverlapFallback }} from {json.dumps('file://' + MODULE)};"
              "const set = [], added = [];"
              "const layers = ["
              "{ id: 'a', type: 'symbol', metadata: { 'q2vt:overlap': 'if-required' }, layout: { 'text-field': 'x' } },"
              "{ id: 'b', type: 'symbol', metadata: { 'q2vt:overlap': 'if-required' },"
              "  layout: { 'text-field': 'x', 'icon-image': 'i' } },"
              "{ id: 'c', type: 'symbol', metadata: { 'q2vt:overlap': 'if-required' },"
              "  layout: { 'text-field': 'x', 'text-overlap': 'always' } },"
              "{ id: 'd', type: 'symbol', layout: { 'text-field': 'x' } }];"
              "const map = { getStyle: () => ({ layers }), setLayoutProperty: (...a) => set.push(a),"
              "  addLayer: (l) => added.push(l), on: () => {} };"
              "const out = enableOverlapFallback(map);"
              "console.log(JSON.stringify({ set, added: added.length, layers: out.layers }));")
    run = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    result = json.loads(run.stdout)
    assert result["added"] == 0 and result["layers"] == ["a", "b"]
    assert result["set"] == [["a", "text-overlap", "cooperative"], ["b", "text-overlap", "cooperative"],
                             ["b", "icon-overlap", "cooperative"]]


def _points(squares, view, options):
    """labelPoints of square polygons [(id, z, x, y, (x0, y0, x1, y1) in tile units)]."""
    script = f"""
import {{ labelPoints }} from {json.dumps('file://' + MODULE)};
const maplibregl = {{ MercatorCoordinate: class {{
  constructor(x, y) {{ this.x = x; this.y = y; }}
  toLngLat() {{ return {{ lng: this.x, lat: this.y }}; }} }} }};
const features = {json.dumps(squares)}.map(([id, z, x, y, [x0, y0, x1, y1]]) => ({{
  _x: x, _y: y, _z: z, properties: {{ q2vt_orig_id: id }},
  _vectorTileFeature: {{ extent: 4096, loadGeometry: () => [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    .map(([px, py]) => ({{ x: px, y: py }}))] }} }}));
const options = {json.dumps(options)};
if (options.box) {{ const box = options.box; options.labelBox = () => box; }}
const out = labelPoints(features, {json.dumps(view)}, maplibregl, options);
console.log(JSON.stringify(out.features.map((f) => [f.properties.q2vt_orig_id, ...f.geometry.coordinates])));
"""
    run = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    return {item[0]: item[1:] for item in json.loads(run.stdout)}


def test_labels_just_off_screen_are_placed_in_advance():
    # One z0 tile; the screen is its left half. Polygon 1 on screen, 2 off
    # screen within reach, 3 off screen but too close to the edge (guard).
    squares = [(1, 0, 0, 0, (512, 512, 1024, 1024)), (2, 0, 0, 0, (3072, 512, 3584, 1024)),
               (3, 0, 0, 0, (2100, 2048, 2300, 2248))]
    view = [0, 0, 0.5, 1]
    points = _points(squares, view, {"reach": [0, 0, 1, 1], "guard": 0.05})
    assert points[1] == pytest.approx([0.1875, 0.1875])
    assert points[2] == pytest.approx([0.8125, 0.1875])   # whole polygon's centroid
    assert 3 not in points                                  # its text could reach the screen
    assert set(_points(squares, view, {})) == {1}           # without reach: on screen only


def test_the_wanted_tile_level_wins_over_deeper_leftovers():
    # Zooming out: an old z1 tile still drawn next to the new z0 tile. With
    # tileZoom 0 the z0 data is used (both polygons), not only the z1 piece.
    squares = [(1, 0, 0, 0, (512, 512, 1024, 1024)), (2, 0, 0, 0, (3072, 3072, 3584, 3584)),
               (1, 1, 0, 0, (1024, 1024, 2048, 2048))]
    assert set(_points(squares, [0, 0, 1, 1], {"tileZoom": 0})) == {1, 2}
    assert set(_points(squares, [0, 0, 1, 1], {})) == {1}  # deepest only (zooming in)


def test_labels_do_not_move_while_the_map_moves():
    # A label at the screen edge would move in (test above), but not while
    # the map is moving: it stays put until the map stops.
    script = f"""
import {{ labelPoints, newLabelState }} from {json.dumps('file://' + MODULE)};
const ring = [[512, 512], [3584, 512], [3584, 3584], [512, 3584]].map(([x, y]) => ({{x, y}}));
const feature = {{ _x: 0, _y: 0, _z: 0, properties: {{ q2vt_orig_id: 7 }},
  _vectorTileFeature: {{ extent: 4096, loadGeometry: () => [ring] }} }};
const maplibregl = {{ MercatorCoordinate: class {{
  constructor(x, y) {{ this.x = x; this.y = y; }}
  toLngLat() {{ return {{ lng: this.x, lat: this.y }}; }} }} }};
const state = newLabelState();
const at = (view, freeze) => labelPoints([feature], view, maplibregl, {{ state, margin: 0.02, freeze }})
  .features.map((f) => f.geometry.coordinates)[0];
console.log(JSON.stringify([at([0, 0, 1, 1], false), at([0.49, 0.49, 1, 1], true), at([0.49, 0.49, 1, 1], false)]));
"""
    run = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    first, moving, stopped = json.loads(run.stdout)
    assert moving == pytest.approx(first)       # dragging: glued to the map
    assert stopped[0] > 0.6                     # stopped: moved onto the visible part


def _js(expression):
    script = (f"import * as m from {json.dumps('file://' + MODULE)};"
              f"console.log(JSON.stringify({expression}));")
    run = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    return json.loads(run.stdout)


def test_roomiest_point_ignores_tile_cuts():
    # A 10 x 4 rectangle cut by a tile edge at x = 5: the most room is its
    # middle; a U shape's centroid lies in the notch, its roomiest point not.
    a = "Object.assign([[0,0],[5,0],[5,4],[0,4]], {cut: [-1,-1,5,10]})"
    b = "Object.assign([[5,0],[10,0],[10,4],[5,4]], {cut: [5,-1,11,10]})"
    best = _js(f"m.roomiestPoint([{a}, {b}])")
    assert best["point"] == pytest.approx([5, 2]) and best["room"] == pytest.approx(2)
    u = [[0, 0], [6, 0], [6, 6], [4, 6], [4, 2], [2, 2], [2, 6], [0, 6]]
    x, y = _js(f"m.roomiestPoint([{json.dumps(u)}]).point")
    assert 0 < y < 2 and 0 < x < 6  # in the bottom bar, not in the notch (y > 2, 2 < x < 4)
    assert _js(f"m.roomAt([{json.dumps(u)}], [3, 4])") < 0


def test_horizontal_labels_fit_the_screen_and_slivers_get_none():
    view = [0, 0, 0.5, 1]
    box = [0.05, 0.02]  # half width, half height (world units)
    squares = [(1, 0, 0, 0, (0, 1000, 2400, 3000)),      # half visible: label moved inward
               (2, 0, 0, 0, (1900, 3200, 4000, 3600)),   # a sliver at the edge: no label
               (3, 0, 0, 0, (200, 3300, 1600, 3900))]    # fully visible
    points = _points(squares, view, {"anchor": "pole", "box": box})
    assert set(points) == {1, 3}
    x, y = points[1]
    assert x <= 0.5 - box[0] + 1e-9 and 0 < x and 1000 / 4096 < y < 3000 / 4096


def test_labels_keep_clear_of_each_other():
    # Two polygons with the same shape (a zone and its parcel): the bigger
    # one first, the other moves to a spot clear of its box.
    squares = [(1, 0, 0, 0, (400, 400, 3600, 3600)), (2, 0, 0, 0, (400, 400, 3600, 3700))]
    box = [0.08, 0.03]
    points = _points(squares, [0, 0, 1, 1], {"anchor": "pole", "box": box})
    (x1, y1), (x2, y2) = points[2], points[1]
    assert points[2] == pytest.approx([0.5, 0.5 + 50 / 4096], abs=0.02)  # the bigger: middle
    assert abs(x1 - x2) >= 2 * box[0] - 1e-6 or abs(y1 - y2) >= 2 * box[1] - 1e-6


def test_old_edge_spot_moves_back_to_the_middle():
    # After a pan a label kept near the bottom of its polygon moves to the
    # middle once the map stops (it kept less than most of the best room).
    script = f"""
import {{ labelPoints, newLabelState }} from {json.dumps('file://' + MODULE)};
const ring = [[512, 512], [3584, 512], [3584, 3584], [512, 3584]].map(([x, y]) => ({{x, y}}));
const feature = {{ _x: 0, _y: 0, _z: 0, properties: {{ q2vt_orig_id: 7 }},
  _vectorTileFeature: {{ extent: 4096, loadGeometry: () => [ring] }} }};
const maplibregl = {{ MercatorCoordinate: class {{
  constructor(x, y) {{ this.x = x; this.y = y; }}
  toLngLat() {{ return {{ lng: this.x, lat: this.y }}; }} }} }};
const state = newLabelState();
state.points.set("7", {{ point: [0.5, 0.84], properties: {{ q2vt_orig_id: 7 }} }});
const out = labelPoints([feature], [0, 0, 1, 1], maplibregl, {{ state, anchor: "pole" }});
console.log(JSON.stringify(out.features[0].geometry.coordinates));
"""
    run = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    assert json.loads(run.stdout) == pytest.approx([0.5, 0.5])


def test_style_expressions_for_label_sizes():
    zoom_curve = ["interpolate", ["exponential", 2], ["zoom"], 0, 0.0002, 24, 3213.2041]
    assert _js(f"m.evaluate({json.dumps(zoom_curve)}, 18.2)") == pytest.approx(57.7, abs=0.1)
    data = ["*", ["to-number", ["get", "size"], 6], 2]
    assert _js(f"m.evaluate({json.dumps(data)}, 10, {{size: '3'}})") == 6
    assert _js(f"m.evaluate({json.dumps(data)}, 10, {{}})") == 12
    assert _js(f"m.evaluate(['step', ['zoom'], 1, 10, 2, 15, 3], 12)") == 2


def test_label_boxes_use_each_characters_advance():
    """The style's font metrics (one advance per character, as QGIS measures
    the label) instead of the mean width: narrow letters make a shorter box."""
    style = {"metadata": {"q2vt:font-metrics": {"F": {"chars": "il W", "advances": [222, 222, 278, 944],
                                                     "height": 1.15}}}}
    layout = {"text-field": ["get", "t"], "text-size": 10}
    expr = (f"(() => {{ const f = m.fontMetrics({json.dumps(style)}).get('F'); "
            f"const box = (t, metrics, layout = {json.dumps(layout)}) => "
            f"m.layoutBoxes(layout, 15, 1, 0.5, metrics)({{t}}); "
            f"return [box('ill', f), box('ill', null), box('W W', f), box('il', f, "
            f"{{...{json.dumps(layout)}, 'text-letter-spacing': 0.1, 'text-transform': 'uppercase'}})]; }})()")
    narrow, mean, wide, spaced = _js(expr)
    # half width = width / 2 + 2 px margin; half height = height / 2 + 2 px.
    assert narrow == pytest.approx([3 * 2.22 / 2 + 2, 11.5 / 2 + 2])
    assert mean == pytest.approx([3 * 5 / 2 + 2, 12 / 2 + 2])
    assert wide[0] == pytest.approx((9.44 * 2 + 2.78) / 2 + 2)
    # "IL": unknown upper-case letters count as the mean (0.5 em), plus one
    # letter spacing between them.
    assert spaced[0] == pytest.approx((5 + 5 + 1) / 2 + 2)


def _lines(lines, view, options, before=None):
    """labelPoints of line features [(id, [[x, y], ...] in z0 tile units)]
    with kind "line" and the rotation written to "rot"; ``before`` sets a
    kept point first."""
    script = f"""
import {{ labelPoints, newLabelState }} from {json.dumps('file://' + MODULE)};
const maplibregl = {{ MercatorCoordinate: class {{
  constructor(x, y) {{ this.x = x; this.y = y; }}
  toLngLat() {{ return {{ lng: this.x, lat: this.y }}; }} }} }};
const features = {json.dumps(lines)}.map(([id, points]) => ({{
  _x: 0, _y: 0, _z: 0, properties: {{ q2vt_orig_id: id, rot: 99 }},
  _vectorTileFeature: {{ extent: 4096, loadGeometry: () => [points.map(([x, y]) => ({{ x, y }}))] }} }}));
const options = {{ kind: "line", rotationField: "rot", state: newLabelState(), ...{json.dumps(options)} }};
if (options.box) {{ const box = options.box; options.labelBox = () => box; }}
for (const [id, point] of {json.dumps(before or [])}) options.state.points.set(String(id), {{ point }});
const out = labelPoints(features, {json.dumps(view)}, maplibregl, options);
console.log(JSON.stringify(out.features.map((f) => [f.properties.q2vt_orig_id, ...f.geometry.coordinates,
  f.properties.rot])));
"""
    run = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    return {item[0]: item[1:] for item in json.loads(run.stdout)}


def test_line_label_at_the_middle_of_the_visible_stretch():
    # A long horizontal contour: the label goes to the middle of the part on
    # the screen (the left half of the tile), not the middle of the line.
    points = _lines([(1, [[0, 2048], [4096, 2048]])], [0, 0, 0.5, 1], {"box": [0.05, 0.01]})
    x, y, rotation = points[1]
    assert (x, y) == pytest.approx((0.25, 0.5)) and rotation == pytest.approx(0)


def test_line_label_rotation_follows_the_line_and_stays_upright():
    view = [0, 0, 1, 1]
    down = _lines([(1, [[0, 0], [4096, 4096]])], view, {})[1]      # down-right on screen
    up = _lines([(1, [[4096, 4096], [0, 0]])], view, {})[1]        # same line, reversed
    steep = _lines([(1, [[2048, 0], [2048, 4096]])], view, {})[1]  # vertical
    assert down[:2] == pytest.approx([0.5, 0.5]) and down[2] == pytest.approx(45)
    assert up[2] == pytest.approx(45)                              # upright either way
    assert steep[2] == pytest.approx(90)


def test_line_label_needs_room_along_the_visible_stretch():
    # Only a short piece of the line is on the screen: no label there.
    lines = [(1, [[1900, 1000], [2400, 1000]]), (2, [[100, 3000], [1900, 3000]])]
    points = _lines(lines, [0, 0, 0.5, 1], {"box": [0.1, 0.01]})
    assert set(points) == {2}


def test_short_line_label_with_degraded_placement():
    """A dimension line shorter than its label: QGIS makes no candidate
    along it; with "Allow degraded placement" the label is drawn level,
    its bottom-left corner on the line's interior point (GEOS: the end
    nearest the centroid, the first on a tie, for two points); without
    it, no label. Not for a line cut by the screen edge."""
    lines = [(1, [[1000, 1000], [1200, 1100]]),                        # 224 long, the label 410
             (2, [[1000, 2000], [1100, 2000], [1300, 2000]]),          # an inner vertex
             (3, [[1900, 3000], [2200, 3000]])]                        # cut by the screen edge
    box = [0.05, 0.01]
    assert _lines(lines, [0, 0, 0.5, 1], {"box": box}) == {}
    points = _lines(lines, [0, 0, 0.5, 1], {"box": box, "degraded": True, "textAnchor": "bottom"})
    assert set(points) == {1, 2}
    x, y, rotation = points[1]
    # Bottom-centre anchor: half a label right of the corner, on its baseline.
    assert (x, y) == pytest.approx((1000 / 4096 + box[0], 1000 / 4096)) and rotation == 0
    x, y, _ = points[2]
    assert (x, y) == pytest.approx((1100 / 4096 + box[0], 2000 / 4096))
    x, y, _ = _lines(lines[:1], [0, 0, 0.5, 1], {"box": box, "degraded": True, "textAnchor": "center"})[1]
    assert (x, y) == pytest.approx((1000 / 4096 + box[0], 1000 / 4096 - box[1]))


def test_line_labels_overlap_only_if_qgis_allows_it():
    """Two dimension lines side by side whose level labels collide: with
    "Allow overlap if required" both are drawn, the second overlapping;
    with "Never overlap" the second is left out (as QGIS)."""
    lines = [(1, [[1000, 1000], [1100, 1050]]), (2, [[1010, 1020], [1110, 1070]])]
    options = {"box": [0.05, 0.01], "degraded": True, "textAnchor": "bottom"}
    assert set(_lines(lines, [0, 0, 0.5, 1], options)) == {1}
    assert set(_lines(lines, [0, 0, 0.5, 1], {**options, "overlap": True})) == {1, 2}
    # Along a line too: a long line under an already placed label.
    long_line = [(3, [[0, 2048], [4096, 2048]])]
    blocked = {"box": [0.05, 0.01], "avoid": [[0, 0.4, 1, 0.6]]}
    assert _lines(long_line, [0, 0, 0.9, 1], blocked) == {}
    assert set(_lines(long_line, [0, 0, 0.9, 1], {**blocked, "overlap": True})) == {3}


def test_line_label_slides_along_to_fit_and_keeps_clear():
    # A label already placed on the middle: the line label slides along the
    # line until its box is clear; nowhere clear: no label; a kept spot near
    # the middle stays, one far from it moves back.
    box = [0.05, 0.01]
    line = [(1, [[0, 2048], [4096, 2048]])]
    x, y, _ = _lines(line, [0, 0, 1, 1], {"box": box, "avoid": [[0.45, 0.45, 0.55, 0.55]]})[1]
    assert y == pytest.approx(0.5) and abs(x - 0.5) >= 0.1 - 1e-9
    assert _lines(line, [0, 0, 1, 1], {"box": box, "avoid": [[0, 0.49, 1, 0.51]]}) == {}
    kept = _lines(line, [0, 0, 1, 1], {"box": box, "precision": 0.001}, before=[(1, [0.6, 0.5])])
    assert kept[1][:2] == pytest.approx([0.6, 0.5])
    far = _lines(line, [0, 0, 1, 1], {"box": box, "precision": 0.001}, before=[(1, [0.9, 0.5])])
    assert far[1][:2] == pytest.approx([0.5, 0.5])                 # too far from the middle


def test_line_label_tries_other_visible_stretches():
    # The longest stretch is taken; a shorter one gets the label.
    lines = [(1, [[0, 1024], [4096, 1024], [4096, 3072], [0, 3072]])]  # two long horizontals on screen
    box = [0.05, 0.01]
    blocked = [[0, 0.2, 1, 0.3]]                                        # the whole upper one
    x, y, _ = _lines(lines, [0, 0, 0.9, 1], {"box": box, "avoid": blocked})[1]
    assert y == pytest.approx(0.75)


def _free(polygons, view, box, always=False, extra=None):
    """labelPoints of polygons [(id, [[x, y], ...] in z0 tile units)] with QGIS
    Free placement: {id: (x, y, rotation in degrees, label scale)}."""
    script = f"""
import {{ labelPoints, newLabelState }} from {json.dumps('file://' + MODULE)};
const maplibregl = {{ MercatorCoordinate: class {{
  constructor(x, y) {{ this.x = x; this.y = y; }}
  toLngLat() {{ return {{ lng: this.x, lat: this.y }}; }} }} }};
const extra = {json.dumps(extra or {})};
const features = {json.dumps(polygons)}.map(([id, ring]) => ({{
  _x: 0, _y: 0, _z: 0, properties: {{ q2vt_orig_id: id, ...(extra[id] || {{}}) }},
  _vectorTileFeature: {{ extent: 4096, loadGeometry: () => [ring.map(([x, y]) => ({{ x, y }}))] }} }}));
const box = {json.dumps(box)};
const out = labelPoints(features, {json.dumps(view)}, maplibregl, {{ state: newLabelState(),
  anchor: "pole", orient: "free", rotationField: "rot", labelBox: () => box, precision: 0.0005,
  always: {json.dumps(bool(always))} }});
console.log(JSON.stringify(out.features.map((f) => [f.properties.q2vt_orig_id, ...f.geometry.coordinates,
  f.properties.rot, f.properties.q2vt_label_scale ?? 1])));
"""
    run = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    return {item[0]: item[1:] for item in json.loads(run.stdout)}


def _tile_pieces(pieces, view, options, z=1):
    """labelPoints of one line feature given as tile pieces of zoom z
    [(tile x, [[x, y], ...] in tile units)]: {key: (x, y, rot)}."""
    script = f"""
import {{ labelPoints, newLabelState }} from {json.dumps('file://' + MODULE)};
const maplibregl = {{ MercatorCoordinate: class {{
  constructor(x, y) {{ this.x = x; this.y = y; }}
  toLngLat() {{ return {{ lng: this.x, lat: this.y }}; }} }} }};
const features = {json.dumps(pieces)}.map(([tx, points]) => ({{
  _x: tx, _y: 0, _z: {z}, properties: {{ q2vt_orig_id: 5, rot: 99 }},
  _vectorTileFeature: {{ extent: 4096, loadGeometry: () => [points.map(([x, y]) => ({{ x, y }}))] }} }}));
const options = {{ kind: "line", rotationField: "rot", state: newLabelState(), tileZoom: 1,
  ...{json.dumps(options)} }};
if (options.box) {{ const box = options.box; options.labelBox = () => box; }}
const out = labelPoints(features, {json.dumps(view)}, maplibregl, options);
console.log(JSON.stringify(out.features.map((f) => [...f.geometry.coordinates, f.properties.rot])));
"""
    run = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    return json.loads(run.stdout)


def test_line_pieces_of_neighbouring_tiles_are_joined():
    """Each tile cuts its own rounded, buffered copy of a line: the two cuts
    on a shared tile edge miss each other by a fraction of a pixel. The
    pieces were not joined (1e-10 tolerance) and a label longer than either
    piece, though shorter than the visible line, was dropped."""
    left = (0, [[2048, 2048], [4096 + 80, 2048]])      # buffered past the tile edge
    right = (1, [[-80, 2049], [2048, 2049]])           # the same line, rounded a unit lower
    view, box = [0, 0, 1, 0.5], [0.15, 0.01]           # a label 0.3 long, pieces 0.25
    labels = _tile_pieces([left, right], view, {"box": box, "precision": 1 / 1000})
    assert len(labels) == 1 and labels[0][0] == pytest.approx(0.5, abs=0.01)
    gap = (1, [[-80, 2088], [2048, 2088]])             # 40 units off: a real gap, not joined
    assert _tile_pieces([left, gap], view, {"box": box, "precision": 1 / 1000}) == []
    # Overzoomed: z3 tiles cut from z1 data (tileZoom, the source's max
    # zoom), whose rounding misses by 3 z3 units, several screen pixels at
    # this precision. The tolerance follows the data's tile unit, not the
    # screen: the pieces are still joined.
    left = (0, [[2048, 2048], [4096 + 80, 2048]])
    right = (1, [[-80, 2051], [2048, 2051]])
    view, box = [0, 0, 0.25, 0.125], [0.15 / 4, 0.01 / 4]
    options = {"box": box, "precision": 1 / 4000 / 8 / 4}
    labels = _tile_pieces([left, right], view, options, z=3)
    assert len(labels) == 1 and labels[0][0] == pytest.approx(0.125, abs=0.01)


def test_horizontal_line_label_is_level_and_centred_on_the_line():
    """QGIS Horizontal placement on lines: a level label centred on a point
    of the line near its middle (pal createHorizontalCandidatesAlongLine),
    on the visible part; it need not fit along the line."""
    steep = [(1, [[2048, 0], [2048, 4096]])]  # vertical: 0.1 of it on the screen
    view = [0, 0.2, 1, 0.3]
    x, y, rotation = _lines(steep, view, {"box": [0.1, 0.01], "level": True})[1]
    assert (x, y) == pytest.approx((0.5, 0.25)) and rotation == 0  # not turned to the line
    assert _lines(steep, view, {"box": [0.1, 0.01]}) == {}  # along it: no room


def test_repeated_line_label_is_chopped_on_the_visible_line():
    """A repeated line label (QGIS repeat distance): the line clipped to the
    screen is cut into equal parts at a whole multiple of the repeat at
    least as long as the label, one label in the middle of each part, as
    pal chops the line clipped to the map extent. Laid out on the whole
    line (windows, MapLibre's spacing) most views had none."""
    script = f"""
import {{ labelPoints, newLabelState, repeatParts, repeatOption }} from {json.dumps('file://' + MODULE)};
const maplibregl = {{ MercatorCoordinate: class {{
  constructor(x, y) {{ this.x = x; this.y = y; }}
  toLngLat() {{ return {{ lng: this.x, lat: this.y }}; }} }} }};
const line = [[0, 2048], [4096, 2048]].map(([x, y]) => ({{ x, y }}));
const feature = {{ _x: 0, _y: 0, _z: 0, properties: {{ q2vt_orig_id: 3, rot: 0 }},
  _vectorTileFeature: {{ extent: 4096, loadGeometry: () => [line] }} }};
const run = (view, repeat) => labelPoints([feature], view, maplibregl, {{ kind: "line",
  rotationField: "rot", state: newLabelState(), repeat, labelBox: () => [0.05, 0.01] }})
  .features.map((f) => f.geometry.coordinates[0]);
console.log(JSON.stringify({{
  parts: repeatParts(1000, 80, 264.6), long: repeatParts(1000, 300, 200),
  whole: run([0, 0, 1, 1], 0.3), half: run([0.2, 0, 0.8, 1], 0.3), sparse: run([0.2, 0, 0.8, 1], 2),
  mapUnits: repeatOption({{ px: 100, zoom: 16 }}, 18, 0.5), screen: repeatOption({{ px: 100 }}, 18, 0.5),
}}));
"""
    run = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    out = json.loads(run.stdout)
    assert [round(b - a, 2) for a, b in out["parts"]] == [333.33] * 3
    assert out["long"] == [[0, 500], [500, 1000]]
    assert sorted(out["whole"]) == pytest.approx([1 / 6, 1 / 2, 5 / 6])
    assert sorted(out["half"]) == pytest.approx([0.35, 0.65])    # the visible 0.6: two parts
    assert out["sparse"] == pytest.approx([0.5])                 # repeat longer than the view
    assert out["mapUnits"] == pytest.approx(200) and out["screen"] == pytest.approx(50)


def test_free_placement_turns_labels_along_narrow_polygons():
    # A street rising to the right at 30° (narrower than the label is long),
    # a wide square, and a tall narrow strip.
    import math
    angle = math.radians(-30)  # y down: rising to the right
    def strip(cx, cy, length, width, a):
        dx, dy = math.cos(a), math.sin(a)
        nx, ny = -dy, dx
        return [[cx + sx * dx * length / 2 + sy * nx * width / 2, cy + sx * dy * length / 2 + sy * ny * width / 2]
                for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
    polygons = [(1, strip(1300, 1300, 2000, 200, angle)),
                (2, [[2600, 2600], [3800, 2600], [3800, 3800], [2600, 3800]]),
                (3, strip(800, 3200, 1400, 150, math.pi / 2))]
    box = [0.06, 0.012]  # half width / height: wider than the street, fits the square
    points = _free(polygons, [0, 0, 1, 1], box)
    assert points[1][2] == pytest.approx(-30, abs=1)   # along the street, upright
    assert points[2][2] == pytest.approx(0)            # fits: horizontal (QGIS)
    assert points[3][2] == pytest.approx(-90, abs=1)   # vertical strip: reads bottom to top


def test_free_placement_helpers():
    assert _js("m.envelope([2, 1], Math.PI / 2)") == pytest.approx([1, 2])
    square = [[0, 0], [10, 0], [10, 10], [0, 10]]
    assert _js(f"m.boxInside([{json.dumps(square)}], [5, 5], [2, 1])") is True
    assert _js(f"m.boxInside([{json.dumps(square)}], [5, 5], [6, 1])") is False
    # An L: around its horizontal arm the direction is horizontal.
    ell = [[0, 0], [100, 0], [100, 10], [10, 10], [10, 100], [0, 100]]
    assert _js(f"m.localDirection([{json.dumps(ell)}], [60, 5], 20)") == pytest.approx(0, abs=0.01)
    assert abs(_js(f"m.localDirection([{json.dumps(ell)}], [5, 60], 20)")) == pytest.approx(math.pi / 2, abs=0.01)


def _strip(cx, cy, length, width, a):
    dx, dy = math.cos(a), math.sin(a)
    nx, ny = -dy, dx
    return [[cx + sx * dx * length / 2 + sy * nx * width / 2, cy + sx * dy * length / 2 + sy * ny * width / 2]
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]


def test_turned_label_on_an_even_strip_sits_at_its_centroid():
    """Along a long strip of even width every spot has the same room: QGIS
    then takes the one nearest the polygon's centroid. The roomiest point
    found by the search lay anywhere along the strip."""
    for angle, cx, length in ((-30, 2048, 2600), (20, 2600, 3200), (-10, 1500, 3200)):
        a = math.radians(angle)
        points = _free([(1, _strip(cx, 2048, length, 150, a))], [0, 0, 1, 1], [0.03, 0.012])
        x, y = points[1][0] * 4096, points[1][1] * 4096
        along = (x - cx) * math.cos(a) + (y - 2048) * math.sin(a)
        assert abs(along) < 0.03 * 4096 / 3 + 1, (angle, along)  # within one grid step (was up to 1464)


def test_turned_labels_stay_inside_and_centred():
    # Streets rising at 30°, 300 units wide (the label is 2 × 0.012 × 4096 ≈ 98 high):
    # the label sits on the street's centre line, wholly inside.
    a = math.radians(-30)
    box = [0.06, 0.012]
    street = _strip(2048, 2048, 3000, 300, a)
    x, y, rotation, _scale = _free([(1, street)], [0, 0, 1, 1], box)[1]
    assert rotation == pytest.approx(-30, abs=1)
    # Distance from the centre line (world units; 300 / 4096 wide street).
    nx, ny = -math.sin(a), math.cos(a)
    off = abs((x - 0.5) * nx + (y - 0.5) * ny)
    assert off < 0.25 * 300 / 4096
    # Too narrow for the label's height, or too short for its length: QGIS
    # (no "fit in polygon") still labels it, centred inside and sticking out.
    narrow = _strip(2048, 2048, 3000, 60, a)
    short = _strip(2048, 2048, 300, 300, a + 0.0)
    x, y, rotation, _scale = _free([(2, narrow)], [0, 0, 1, 1], box)[2]
    assert rotation == pytest.approx(-30, abs=1) and abs((x - 0.5) * nx + (y - 0.5) * ny) < 30 / 4096
    x, y, _rotation, _scale = _free([(3, short)], [0, 0, 1, 1], box)[3]
    assert math.hypot(x - 0.5, y - 0.5) < 150 / 4096


def test_place_turned_keeps_clear_of_other_labels_inside_the_street():
    # Another label on the middle of a horizontal-ish street: the turned label
    # moves along the street (still centred across it), not to its edge.
    rings = json.dumps([_strip(0.5, 0.5, 0.8, 0.06, math.radians(-30))])
    other = json.dumps([[0.45, 0.45, 0.55, 0.55]])
    out = _js(f"m.placeTurned({rings}, -Math.PI / 6, [0.06, 0.012], [0, 0, 1, 1], {other}, [0.5, 0.5], null, 'rot', 0.001)")
    x, y = out["point"]
    nx, ny = -math.sin(math.radians(-30)), math.cos(math.radians(-30))
    assert abs((x - 0.5) * nx + (y - 0.5) * ny) < 0.01   # on the centre line
    assert abs(x - 0.5) > 0.05                            # moved along the street


def _band(cx, cy, length, width):
    return [[cx - length / 2, cy - width / 2], [cx + length / 2, cy - width / 2],
            [cx + length / 2, cy + width / 2], [cx - length / 2, cy + width / 2]]


def test_always_label_shrinks_or_forces_a_label_that_does_not_fit():
    # A narrow horizontal strip (tile units; label half height 0.012 world =
    # ~49 tile units): 70 units wide -> the label fits at 65 %; 30 units wide
    # -> not even at half size: at the roomiest point anyway, half size.
    box = [0.03, 0.012]
    polygons = [(1, _band(1000, 1000, 1600, 70)), (2, _band(1000, 2000, 1600, 30))]
    off = _free(polygons, [0, 0, 1, 1], box)
    # Without the option: full size, centred on the strip and sticking out of
    # it (QGIS without "fit in polygon").
    assert off[1][3] == 1 and off[2][3] == 1
    assert off[2][1] == pytest.approx(2000 / 4096, abs=0.002)
    on = _free(polygons, [0, 0, 1, 1], box, always=True)
    assert on[1][3] < 1 and on[2][3] == pytest.approx(0.5)
    assert on[2][1] == pytest.approx(2000 / 4096, abs=0.002)  # on the strip's middle line
    assert on[1][2] == pytest.approx(0, abs=1) and on[2][2] == pytest.approx(0, abs=1)  # along it


def test_always_label_uses_the_precomputed_spot_without_a_search():
    # The export's pole (EPSG:3857 metres) of a strip too narrow for the label:
    # used as it is (point, main angle 90 = east -> horizontal, scale = room / height).
    size = 2 * 20037508.342789244
    x, y = (1000 / 4096 - 0.5) * size, (0.5 - 2000 / 4096) * size
    room = 0.006 * size  # half the label's half height
    extra = {2: {"q2vt_pole_x": x, "q2vt_pole_y": y, "q2vt_pole_r": room, "q2vt_pole_a": 90}}
    on = _free([(2, _band(1000, 2000, 1600, 30))], [0, 0, 1, 1], [0.03, 0.012], always=True, extra=extra)
    assert on[2][0] == pytest.approx(1000 / 4096) and on[2][1] == pytest.approx(2000 / 4096)
    assert on[2][2] == pytest.approx(0) and on[2][3] == pytest.approx(0.5)


def test_scaled_text_size_keeps_a_dense_zoom_curve():
    size = _js('m.scaledTextSize(["interpolate", ["exponential", 2], ["zoom"], 0, 1, 24, 2 ** 24])')
    assert size[:3] == ["interpolate", ["exponential", 2], ["zoom"]]
    stops = size[3:]
    assert stops[0::2] == list(range(0, 25))  # a stop at every whole zoom
    assert stops[1::2][10][1] == pytest.approx(2 ** 10)
    assert stops[1][2][0] == "to-number"


def test_label_point_source_is_deep_enough_for_the_deepest_browser_zoom():
    # Points are rounded to 1/8192 of a maxzoom tile: 2^(z - maxzoom - 4) px
    # at zoom z, at most half a pixel at the map's maximum zoom.
    depths = _js("[22, 23.99, 18, 0].map(m.pointMaxzoom)")
    assert depths == [18, 20, 15, 15]
    for max_zoom, depth in zip((22, 23.99), depths):
        assert 2 ** (max_zoom - depth - 4) / 2 <= 0.5


# "Around point" polygon labels (QGIS AroundPoint): beside the point at the
# QGIS label distance, never on it (a centroid marker stays visible).
AROUND = {"anchors": ["bottom", "bottom-left", "bottom-right", "left", "right", "top", "top-left",
                      "top-right"], "distance": 0.03}


def _overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def test_around_point_label_sits_beside_its_point_at_the_distance():
    squares = [(1, 0, 0, 0, (1024, 1024, 3072, 3072))]   # centroid (0.5, 0.5)
    box = [0.08, 0.02]
    x, y = _points(squares, [0, 0, 1, 1], {"box": box, "around": AROUND})[1]
    assert x == pytest.approx(0.5)
    assert y + box[1] == pytest.approx(0.5 - AROUND["distance"])   # above, label bottom at the distance
    marker = [0.48, 0.48, 0.52, 0.52]  # a centroid marker 0.04 wide (< 2 x the distance)
    assert not _overlaps([x - box[0], y - box[1], x + box[0], y + box[1]], marker)
    # Without "around" (over point / horizontal) the label stays on the point.
    assert _points(squares, [0, 0, 1, 1], {"box": box})[1] == pytest.approx([0.5, 0.5])


def test_around_point_tries_the_next_position_when_one_does_not_fit_or_is_taken():
    box = [0.08, 0.02]
    # The top of the screen cuts the position above: the next one that fits.
    squares = [(1, 0, 0, 0, (1024, 100, 3072, 300))]     # centroid y = 200/4096 ~ 0.049
    x, y = _points(squares, [0, 0, 1, 1], {"box": box, "around": AROUND})[1]
    # Above (and above-left/right) would leave the screen: right of the point.
    assert [x, y] == pytest.approx([0.5 + AROUND["distance"] + box[0], 200 / 4096])
    # Two polygons around the same point: the second label keeps clear of the first.
    squares = [(1, 0, 0, 0, (1024, 1024, 3072, 3072)), (2, 0, 0, 0, (1536, 1536, 2560, 2560))]
    points = _points(squares, [0, 0, 1, 1], {"box": box, "around": AROUND})
    boxes = [[x - box[0], y - box[1], x + box[0], y + box[1]] for x, y in points.values()]
    assert len(boxes) == 2 and not _overlaps(*boxes)


def test_around_point_label_of_a_polygon_smaller_than_its_label():
    # A building-sized polygon (0.01 across) with a long name (0.16 x 0.04),
    # wholly on the screen: an "over the point" label goes on its centroid
    # (QGIS), an "around" label beside it.
    squares = [(1, 0, 0, 0, (2028, 2028, 2068, 2068))]
    box = [0.08, 0.02]
    assert _points(squares, [0, 0, 1, 1], {"box": box})[1] == pytest.approx([0.5, 0.5])
    x, y = _points(squares, [0, 0, 1, 1], {"box": box, "around": AROUND})[1]
    assert x == pytest.approx(0.5) and y + box[1] == pytest.approx(0.5 - AROUND["distance"])


def test_around_point_distance_is_measured_from_the_bare_text():
    """QGIS keeps the label distance from the text itself: the box margin
    and a background's padding do not push an around-point label further."""
    expr = ("(() => { const box = m.layoutBoxes({'text-field': ['get', 't'], 'text-size': 10, "
            "'icon-text-fit': 'both', 'icon-text-fit-padding': ['literal', [3, 4, 3, 4]]}, 15, 1, 0.5)({t: 'abcd'});"
            " return [box, box.text]; })()")
    box, text = _js(expr)
    assert text == pytest.approx([10, 6]) and box == pytest.approx([16, 11])
    squares = [(1, 0, 0, 0, (1024, 1024, 3072, 3072))]
    script_box = {"box": [0.08, 0.02]}
    x, y = _points(squares, [0, 0, 1, 1], {**script_box, "around": AROUND})[1]
    assert y + 0.02 == pytest.approx(0.5 - AROUND["distance"])


def test_around_point_distance_in_map_units_follows_the_zoom():
    fixed = _js("m.aroundOption({px: 10, anchors: ['top']}, 16, 0.001)")
    assert fixed == {"anchors": ["top"], "distance": pytest.approx(0.01)}
    scaled = _js("m.aroundOption({px: 10, zoom: 14}, 16, 0.001)")
    # Without anchors: QGIS's first candidate, above right of the point.
    assert scaled["distance"] == pytest.approx(0.04) and scaled["anchors"][0] == "bottom-left"
    assert _js("m.aroundOption(undefined, 16, 0.001)") is None
    # No distance: centred on the point first (QGIS), then around it.
    assert _js("m.aroundOption({px: 0, anchors: ['top']}, 16, 0.001)")["anchors"] == ["center", "top"]
    candidates = _js("m.aroundCandidates([0.5, 0.5], [0.1, 0.02], "
                     "{anchors: ['bottom', 'right', 'top-left'], distance: 0.04})")
    diagonal = 0.04 * math.sqrt(0.5)
    assert sum(candidates, []) == pytest.approx([0.5, 0.44, 0.36, 0.5, 0.6 + diagonal, 0.52 + diagonal])


# QGIS 3.34 Free (angled) label angles for rectangles w x h turned by the box
# angle (counter-clockwise, map y up), "free label" at 20 map units (about
# 95 x 26), read from QgsLabelingResults; normalised to [-90, 90) degrees.
_QGIS_FREE = {
    (300, 200): {a: 0 for a in range(0, 181, 10)},
    (400, 150): {0: 0, 10: 0, 20: 0, 30: 0, 40: -40, 50: 40, 60: 30, 70: 20, 80: 10, 90: 0,
                 100: -10, 110: -20, 120: -30, 130: -40, 140: 40, 150: 0, 160: 0, 170: 0, 180: 0},
    (300, 60): {0: 0, 10: -10, 20: -20, 30: -30, 40: -40, 50: -50, 60: -60, 70: -70, 80: -80,
                90: -90, 100: 80, 110: 70, 120: 60, 130: 50, 140: 40, 150: 30, 160: 20,
                170: 10, 180: 0},
}


@pytest.mark.parametrize("size", list(_QGIS_FREE))
def test_free_angle_follows_qgis(size):
    """Free (angled) labels as QGIS's PAL turns them: horizontal when a
    label twice the size fits around the box centre, else along the box axis
    nearest horizontal (both sides long enough) or along the long side."""
    w, h = size
    rings = []
    for angle in _QGIS_FREE[size]:
        a = math.radians(angle)
        ring = [[x * math.cos(a) - y * math.sin(a), -(x * math.sin(a) + y * math.cos(a))]  # y down
                for x, y in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2))]
        rings.append(ring)
    got = _js(f"{json.dumps(rings)}.map((ring) => m.freeAngle([ring], [47.5, 13]) * 180 / Math.PI)")
    for (box_angle, expected), angle in zip(_QGIS_FREE[size].items(), got):
        expected = -90 if expected == 90 else expected
        assert min(abs(angle - expected), abs(abs(angle - expected) - 180)) < 1, (box_angle, angle, expected)


def test_label_boxes_read_plain_array_layout_values():
    """The exporter writes text-offset and icon-text-fit-padding as plain
    arrays, not expressions: they used to evaluate to nothing (a framed
    label's box lost its padding)."""
    assert _js("m.evaluate([0, -0.75], 15)") == [0, -0.75]
    expr = ("(() => { const box = m.layoutBoxes({'text-field': ['get', 't'], 'text-size': 10, "
            "'icon-text-fit': 'both', 'icon-text-fit-padding': [3, 4, 3, 4]}, 15, 1, 0.5)({t: 'abcd'});"
            " return [box, box.text]; })()")
    box, text = _js(expr)
    assert box == pytest.approx([16, 11]) and text == pytest.approx([10, 6])


def test_framed_label_box_is_the_frame_maplibre_draws():
    """MapLibre fits a frame to its own line box (1.2 em a line); the
    exporter's padding brings it out to the font's ascent and descent. The
    box is that frame, not the font's height plus the padding once more."""
    expr = ("(() => { const box = m.layoutBoxes({'text-field': ['get', 't'], 'text-size': 10, "
            "'icon-text-fit': 'both', 'icon-text-fit-padding': [4.4, 4, 2.5, 4]}, 15, 1, 0.5, "
            "{advance: new Map(), height: 1.362})({t: 'abcd'}); return [box, box.text]; })()")
    box, text = _js(expr)
    assert box == pytest.approx([16, (12 + 6.9) / 2 + 2])
    assert text == pytest.approx([10, 6.81])  # the bare text keeps the font's height


def test_rendered_label_boxes_follow_text_offset():
    """A town name drawn 0.75 em above its dot: its box is where the text is."""
    expr = """(() => {
      const town = {id: 'town', type: 'symbol', layout: {'text-field': ['get', 't'], 'text-size': 20,
                                                          'text-offset': [0, -0.75]}};
      const map = {getStyle: () => ({layers: [town]}), queryRenderedFeatures: () => [
        {layer: town, properties: {t: 'ABCD'}, geometry: {type: 'Point', coordinates: [0.5, 0.5]}}]};
      const maplibregl = {MercatorCoordinate: {fromLngLat: ([x, y]) => ({x, y})}};
      return m.renderedLabelBoxes(map, maplibregl, new Map(), 15, 0.001);
    })()"""
    (box,) = _js(expr)
    assert (box[1] + box[3]) / 2 == pytest.approx(0.485) and (box[0] + box[2]) / 2 == pytest.approx(0.5)


def test_around_point_labels_keep_clear_of_maplibre_labels():
    """Around-point labels avoid the point labels MapLibre draws itself (a
    town name), like line labels; plain polygon labels only the viewer's."""
    expr = """(() => {
      let asked = 0; const others = () => { asked++; return [[9, 9, 10, 10]]; };
      const a = m.avoidFor({around: {anchors: ['top']}}, [], others);
      const b = m.avoidFor({kind: 'line'}, [], others);
      const c = m.avoidFor({}, [[1, 1, 2, 2]], others);
      return [a, b, c, asked];
    })()"""
    around, line, plain, asked = _js(expr)
    assert around == [[9, 9, 10, 10]] and line == [[9, 9, 10, 10]] and plain == [[1, 1, 2, 2]] and asked == 2
    qgis_order = {"anchors": ["bottom-left", "left", "bottom", "top-left", "bottom-right", "top", "right",
                              "top-right"], "distance": 0.03}
    squares = [(1, 0, 0, 0, (1024, 1024, 3072, 3072))]   # centroid (0.5, 0.5)
    box = [0.08, 0.02]
    first = _points(squares, [0, 0, 1, 1], {"box": box, "around": qgis_order})[1]
    town = [first[0] - box[0], first[1] - box[1], first[0] + box[0], first[1] + box[1]]
    x, y = _points(squares, [0, 0, 1, 1], {"box": box, "around": qgis_order, "avoid": [town]})[1]
    assert not _overlaps([x - box[0], y - box[1], x + box[0], y + box[1]], town)


def test_pole_labels_keep_clear_of_maplibre_labels():
    """Horizontal / Free polygon labels ("pole") avoid the point labels
    MapLibre draws itself too: QGIS places all labels together."""
    expr = """(() => {
      const others = () => [[9, 9, 10, 10]];
      return [m.avoidFor({anchor: 'pole'}, [[1, 1, 2, 2]], others), m.avoidFor({}, [[1, 1, 2, 2]], others)];
    })()"""
    pole, plain = _js(expr)
    assert pole == [[1, 1, 2, 2], [9, 9, 10, 10]] and plain == [[1, 1, 2, 2]]


def test_pole_label_moves_off_a_maplibre_label_inside_its_polygon():
    """End to end on a stub map: a place name MapLibre draws (one em above its
    dot) on the middle of a square park. The park's name (pole) used to go to
    the middle anyway, drawn over the place name (text-overlap "always")."""
    expr = """(() => {
      const park = {id: 'park', type: 'symbol', source: 't', 'source-layer': 'parks',
        layout: {'text-field': ['get', 'name'], 'text-size': 16},
        metadata: {'q2vt:visible-polygons': 'parks', 'q2vt:label-anchor': 'pole'}};
      const place = {id: 'place', type: 'symbol', source: 't', 'source-layer': 'places',
        layout: {'text-field': ['get', 'name'], 'text-size': 16, 'text-offset': [0, -1]}};
      const layers = [park, place], sources = {t: {type: 'vector', minzoom: 0, maxzoom: 14}}, out = {};
      const ring = [[400, 400], [3696, 400], [3696, 3696], [400, 3696], [400, 400]].map(([x, y]) => ({x, y}));
      const parkName = 'Greenfield Nature Reserve', placeName = 'Greenfield Visitor Centre and Shop';
      const map = {
        getStyle: () => ({sources, layers: layers.slice()}), getLayer: (id) => layers.find((l) => l.id === id),
        addLayer: (l, before) => {
          const i = layers.findIndex((x) => x.id === before);
          if (i < 0) layers.push(l); else layers.splice(i, 0, l);
        },
        removeLayer: (id) => layers.splice(layers.findIndex((l) => l.id === id), 1),
        addSource: (id, spec) => { sources[id] = spec; }, getSource: (id) => ({setData: (d) => { out[id] = d; }}),
        getLayoutProperty: () => 'visible', setLayoutProperty: () => {}, on: () => {}, off: () => {},
        isMoving: () => false, isSourceLoaded: () => true, getZoom: () => 0, getMaxZoom: () => 22,
        getContainer: () => ({clientWidth: 1000}),
        getBounds: () => ({getSouthWest: () => ({lng: 0, lat: 1}), getNorthEast: () => ({lng: 1, lat: 0})}),
        querySourceFeatures: (s, {sourceLayer}) => sourceLayer !== 'parks' ? [] : [{_x: 0, _y: 0, _z: 0,
          properties: {q2vt_orig_id: 1, name: parkName},
          _vectorTileFeature: {extent: 4096, loadGeometry: () => [ring]}}],
        queryRenderedFeatures: ({layers: wanted}) => !wanted.includes('place') ? [] : [{layer: place,
          properties: {name: placeName}, geometry: {type: 'Point', coordinates: [0.5, 0.516]}}],
      };
      const maplibregl = {MercatorCoordinate: class {
        constructor(x, y) { this.x = x; this.y = y; }
        static fromLngLat(ll) { return Array.isArray(ll) ? new this(ll[0], ll[1]) : new this(ll.lng, ll.lat); }
        toLngLat() { return {lng: this.x, lat: this.y}; } }};
      m.enableVisibleLabels(map, maplibregl, 't');
      const box = (layout, name, [x, y]) => {
        const [w, h] = m.layoutBoxes(layout, 0, 0.001)({name});
        return [x - w, y - h, x + w, y + h];
      };
      const at = out['q2vt_visible_parks'].features[0].geometry.coordinates;
      return [box(park.layout, parkName, at), box(place.layout, placeName, [0.5, 0.516 - 0.016])];
    })()"""
    park, place = _js(expr)
    assert not _overlaps(park, place)
    # Still inside the park (0.098 .. 0.902 in both directions).
    assert 0.098 < park[0] and park[2] < 0.902 and 0.098 < park[1] and park[3] < 0.902


def test_rendered_labels_of_any_expression_are_measured():
    """Basemap place names (text-field a "case" expression) and HTML labels
    ("format"): the viewer's own small evaluator does not know these, so the
    boxes come from MapLibre's evaluation of each rendered feature; labels
    made from the style's definitions got no box and were overlapped."""
    expr = """(() => {
      const town = {id: 'town', type: 'symbol', layout: {
        'text-field': ['case', ['has', 'name'], ['get', 'name'], ''], 'text-size': 10}};
      const html = {id: 'html', type: 'symbol', layout: {
        'text-field': ['format', ['get', 'a'], {}, ['get', 'b'], {'font-scale': 0.8}], 'text-size': 10}};
      const evaluated = (layer, t) => ({...layer, layout: {...layer.layout,
        'text-field': {sections: [{text: t}], toString() { return t; }}}});
      const map = {getStyle: () => ({layers: [town, html]}), queryRenderedFeatures: () => [
        {layer: evaluated(town, 'Suburb'), properties: {name: 'Suburb'}, geometry: {type: 'Point', coordinates: [0.5, 0.5]}},
        {layer: evaluated(html, 'AB12'), properties: {a: 'AB', b: '12'}, geometry: {type: 'Point', coordinates: [0.2, 0.2]}}]};
      const maplibregl = {MercatorCoordinate: {fromLngLat: ([x, y]) => ({x, y})}};
      return m.renderedLabelBoxes(map, maplibregl, new Map(), 15, 0.001).map((b) => b[2] - b[0]);
    })()"""
    town_width, html_width = _js(expr)
    assert town_width == pytest.approx((6 * 0.6 * 10 + 4) * 0.001)
    assert html_width == pytest.approx((4 * 0.6 * 10 + 4) * 0.001)


def test_rendered_label_boxes_measure_each_label_with_its_own_text():
    """A rendered feature's ``layer.layout`` holds that feature's own
    evaluated values (text-field: a Formatted of its text): each label is
    measured with its own text, not every label of the layer with the first."""
    expr = """(() => {
      const town = {id: 'town', type: 'symbol', layout: {'text-field': ['get', 't'], 'text-size': 10}};
      const evaluated = (t) => ({...town, layout: {...town.layout,
        'text-field': {sections: [{text: t}], toString() { return t; }}}});
      const map = {getStyle: () => ({layers: [town]}), queryRenderedFeatures: () => [
        {layer: evaluated('ABCDEFGHIJ'), properties: {t: 'ABCDEFGHIJ'}, geometry: {type: 'Point', coordinates: [0.5, 0.5]}},
        {layer: evaluated('AB'), properties: {t: 'AB'}, geometry: {type: 'Point', coordinates: [0.2, 0.2]}}]};
      const maplibregl = {MercatorCoordinate: {fromLngLat: ([x, y]) => ({x, y})}};
      return m.renderedLabelBoxes(map, maplibregl, new Map(), 15, 0.001).map((b) => b[2] - b[0]);
    })()"""
    long_width, short_width = _js(expr)
    assert long_width == pytest.approx((10 * 0.6 * 10 + 4) * 0.001)
    assert short_width == pytest.approx((2 * 0.6 * 10 + 4) * 0.001)


def _place(features, view, options):
    """labelPoints of z0 features [(id, [[[x, y], ...] rings in tile units], extra
    properties)]: [(id, x, y, rotation, label box as a world rectangle)]."""
    script = f"""
import {{ labelPoints, newLabelState, envelope }} from {json.dumps('file://' + MODULE)};
const maplibregl = {{ MercatorCoordinate: class {{
  constructor(x, y) {{ this.x = x; this.y = y; }}
  toLngLat() {{ return {{ lng: this.x, lat: this.y }}; }} }} }};
const features = {json.dumps(features)}.map(([id, rings, extra]) => ({{
  _x: 0, _y: 0, _z: 0, properties: {{ q2vt_orig_id: id, ...extra }},
  _vectorTileFeature: {{ extent: 4096, loadGeometry: () => rings.map((ring) => ring.map(([x, y]) => ({{ x, y }}))) }} }}));
const options = {{ state: newLabelState(), precision: 0.0005, ...{json.dumps(options)} }};
const box = options.box;
if (box && options.text) box.text = options.text;  // the bare text (box: with its margin)
if (box) options.labelBox = () => box;
const out = labelPoints(features, {json.dumps(view)}, maplibregl, options);
console.log(JSON.stringify(out.features.map((f) => {{
  const [x, y] = f.geometry.coordinates;
  const angle = (Number(f.properties.rot) || 0) * Math.PI / 180;
  const [hx, hy] = box ? envelope(box, angle) : [0, 0];
  return [f.properties.q2vt_orig_id, x, y, f.properties.rot ?? 0, [x - hx, y - hy, x + hx, y + hy]];
}})));
"""
    run = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    return json.loads(run.stdout)


def _square(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def _overlapping(placed):
    return sum(1 for i, a in enumerate(placed) for b in placed[i + 1:] if _overlaps(a[4], b[4]))


@pytest.mark.parametrize("mode", [{}, {"anchor": "pole"}, {"around": {**AROUND, "distance": 0}}])
def test_polygon_labels_never_overlap_unless_qgis_allows_it(mode):
    """A grid of strips narrower than their labels (strip parcels zoomed
    out): with QGIS's default "Never overlap" no two drawn labels overlap
    (some are left out, as QGIS thins them); "Allow overlap if required"
    draws every label. Centroid labels were drawn on top of each other."""
    strips = [(10 * row + col, [_square(400 + 300 * col, 400 + 300 * row, 600 + 300 * col, 650 + 300 * row)], {})
              for row in range(12) for col in range(10)]
    box = [0.1, 0.02]  # 819 x 164 tile units: wider than a strip and its gaps
    never = _place(strips, [0, 0, 1, 1], {**mode, "box": box})
    assert never and _overlapping(never) == 0
    assert len(never) < len(strips)
    allowed = _place(strips, [0, 0, 1, 1], {**mode, "box": box, "overlap": "if-required"})
    assert len(allowed) == len(strips) and _overlapping(allowed) > 0


def test_label_every_part_or_only_the_biggest():
    """A multi-part feature (one q2vt_orig_id, parts q2vt_part 1 and 2): with
    "Label every part" one label per part; without it only the biggest part
    is labelled, at its own centroid. The centroid of both parts together
    lay between them and the label went to the smaller part."""
    big, small = _square(400, 400, 2000, 2000), _square(2600, 200, 3000, 3600)
    feature = [(1, [big], {"q2vt_part": 1}), (1, [small], {"q2vt_part": 2})]
    one = _place(feature, [0, 0, 1, 1], {"box": [0.02, 0.01]})
    assert len(one) == 1 and one[0][1:3] == pytest.approx([1200 / 4096, 1200 / 4096])
    both = _place(feature, [0, 0, 1, 1], {"box": [0.02, 0.01], "perPart": True})
    assert sorted(round(item[1] * 4096) for item in both) == [1200, 2800]
    # Lines: the longest part, or each part.
    lines = [(1, [[[100, 1000], [3900, 1000]]], {"q2vt_part": 1}),
             (1, [[[100, 3000], [900, 3000]]], {"q2vt_part": 2})]
    one = _place(lines, [0, 0, 1, 1], {"kind": "line", "box": [0.02, 0.01]})
    assert len(one) == 1 and one[0][2] == pytest.approx(1000 / 4096)
    assert len(_place(lines, [0, 0, 1, 1], {"kind": "line", "box": [0.02, 0.01], "perPart": True})) == 2


@pytest.mark.parametrize("mode", [{}, {"anchor": "pole"}])
def test_polygon_smaller_than_its_label_is_labelled_when_wholly_visible(mode):
    """QGIS (Over point, Horizontal) labels a polygon however small, the
    label's centre inside it; only a polygon cut by the screen edge with a
    sliver in view gets no label."""
    tiny = [(1, [_square(2000, 2000, 2060, 2060)], {})]  # 60 x 60 with a 164 x 66 label
    [(_, x, y, _rotation, _box)] = _place(tiny, [0, 0, 1, 1], {**mode, "box": [0.02, 0.008]})
    assert x == pytest.approx(2030 / 4096, abs=0.002) and y == pytest.approx(2030 / 4096, abs=0.002)
    sliver = [(1, [_square(1980, 1000, 2400, 1400)], {})]  # only 68 tile units of it on the screen
    assert _place(sliver, [0, 0, 0.5, 1], {**mode, "box": [0.03, 0.0022]}) == []


def test_free_label_on_a_strip_narrower_than_the_label():
    """Free (angled) on a narrow strip at 30°: QGIS turns the label along the
    strip, centred on it and sticking out (no "fit in polygon"); the viewer
    drew no label until the strip was as wide as the label."""
    strip = [(1, [_strip(2048, 2048, 3000, 24, math.radians(-30))], {})]  # the label is 37 high
    [(_, x, y, rotation, _box)] = _place(strip, [0, 0, 1, 1], {
        "anchor": "pole", "orient": "free", "rotationField": "rot", "box": [0.03, 0.0045]})
    assert rotation == pytest.approx(-30, abs=1)
    nx, ny = -math.sin(math.radians(-30)), math.cos(math.radians(-30))
    assert abs((x - 0.5) * nx + (y - 0.5) * ny) < 12 / 4096


def test_outside_polygons_labels_go_beside_the_polygon():
    """QGIS "Outside polygons": the label beside the polygon at the label
    distance, never on it, also for a polygon smaller than the label; a
    polygon filling the screen has no room outside: no label."""
    outside = {**AROUND, "outside": True, "distance": 0.01}
    box = [0.05, 0.01]
    for square in (_square(1500, 1500, 2600, 2600), _square(2028, 2028, 2068, 2068)):
        [(_, x, y, _rotation, label)] = _place([(1, [square], {})], [0, 0, 1, 1], {"box": box, "around": outside})
        polygon = [v / 4096 for v in square[0] + square[2]]
        gap = max(polygon[0] - label[2], label[0] - polygon[2], polygon[1] - label[3], label[1] - polygon[3])
        assert gap == pytest.approx(outside["distance"], abs=0.003)
    assert _place([(1, [_square(0, 0, 4096, 4096)], {})], [0.2, 0.2, 0.8, 0.8],
                  {"box": box, "around": outside}) == []


@pytest.mark.parametrize("distance", [0, 0.001, 0.01])
def test_outside_polygons_labels_at_a_small_distance(distance):
    """"Outside polygons" at the QGIS default distance 0 (or a small one):
    the text beside the polygon's edge at that distance. The candidates were
    tested with the label's box including its margin for other labels and
    all touched the polygon: no label at distances below ~4 px."""
    box, text = [0.052, 0.009], [0.05, 0.007]
    square = _square(1500, 1500, 2600, 2400)
    [(_, x, y, _rotation, _label)] = _place([(1, [square], {})], [0, 0, 1, 1],
                                            {"box": box, "text": text, "precision": 1 / 1024,
                                             "around": {**AROUND, "outside": True, "distance": distance}})
    polygon = [v / 4096 for v in square[0] + square[2]]
    drawn = [x - text[0], y - text[1], x + text[0], y + text[1]]
    gap = max(polygon[0] - drawn[2], drawn[0] - polygon[2], polygon[1] - drawn[3], drawn[1] - polygon[3])
    assert gap == pytest.approx(distance, abs=1e-6)


def test_outside_polygons_label_kept_after_a_zoom_out_stays_off_the_polygon():
    """An "Outside polygons" label kept from the last update (not moved
    unless needed) stays only while its text is still beside the polygon:
    zooming out the text grows and would reach onto the polygon. The test
    was inverted: the label was kept just when it lay on the polygon."""
    script = f"""
import {{ labelPoints, newLabelState }} from {json.dumps('file://' + MODULE)};
const maplibregl = {{ MercatorCoordinate: class {{
  constructor(x, y) {{ this.x = x; this.y = y; }}
  toLngLat() {{ return {{ lng: this.x, lat: this.y }}; }} }} }};
const square = {{ _x: 0, _y: 0, _z: 0, properties: {{ q2vt_orig_id: 1 }},
  _vectorTileFeature: {{ extent: 4096, loadGeometry: () => [[[1800, 1800], [2300, 1800], [2300, 2200], [1800, 2200]]
    .map(([x, y]) => ({{ x, y }}))] }} }};
const state = newLabelState();
const out = [];
let perPx = 2 / 4096;
for (const [step, view] of [[1, [0.3, 0.3, 0.7, 0.7]], [Math.SQRT2, [0.25, 0.25, 0.75, 0.75]],
                            [1.1, [0.25, 0.25, 0.75, 0.75]], [1.05, [0.25, 0.25, 0.75, 0.75]]]) {{
  perPx *= step;
  const text = [30 * perPx, 5 * perPx], box = [32 * perPx, 7 * perPx];
  box.text = text;
  const placed = labelPoints([square], view, maplibregl, {{ state, precision: perPx, keepDistance: 24 * perPx,
    labelBox: () => box, around: {{ anchors: [], distance: 0, outside: true }} }});
  for (const f of placed.features) {{
    const [x, y] = f.geometry.coordinates.map((v) => v * 4096);
    const [tx, ty] = text.map((v) => v * 4096);
    // How far the text reaches onto the polygon (tile units; <= 0: beside it).
    const depth = Math.min(x + tx - 1800, 2300 - (x - tx), y + ty - 1800, 2200 - (y - ty));
    out.push([depth, perPx * 4096]);
  }}
}}
console.log(JSON.stringify(out));
"""
    run = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    steps = json.loads(run.stdout)
    assert len(steps) == 4
    for depth, pixel in steps:
        assert depth <= pixel  # at most the 1 px rounding slack


@pytest.mark.parametrize("mode", [{}, {"anchor": "pole"}, {"around": {**AROUND, "distance": 0}}])
def test_labels_whose_margins_alone_meet_do_not_overlap(mode):
    """QGIS's "Never overlap" compares the bare text: labels side by side
    with a gap smaller than the two margins round their boxes are all
    drawn. Every other strip parcel lost its label."""
    pixel = 4 / 4096
    strips = [(col, [_square(400 + 134 * col, 1000, 534 + 134 * col, 1640)], {}) for col in range(10)]
    # Bare text 30 x 12 px in strips 33.5 px wide, with the 2 px margin.
    placed = _place(strips, [0, 0, 1, 1], {**mode, "precision": pixel, "box": [17 * pixel, 8 * pixel],
                                           "text": [15 * pixel, 6 * pixel]})
    assert len(placed) == len(strips)
    bare = [(fid, x, y, rot, [x - 15 * pixel, y - 6 * pixel, x + 15 * pixel, y + 6 * pixel])
            for fid, x, y, rot, _box in placed]
    assert _overlapping(bare) == 0


def test_outside_polygons_labels_keep_off_neighbouring_polygons():
    """"Outside polygons": the layer's other polygons are obstacles, as in
    QGIS. The label above a wide polygon (its cheapest spot) went over the
    small polygon just above it."""
    wide, small = _square(1000, 1000, 1800, 1300), _square(1350, 900, 1450, 970)
    placed = _place([(1, [wide], {}), (2, [small], {})], [0, 0, 1, 1],
                    {"box": [0.03, 0.006], "around": {**AROUND, "outside": True, "distance": 0}})
    label = next(box for (fid, *_rest, box) in placed if fid == 1)
    shape = [v / 4096 for v in small[0] + small[2]]
    assert not _overlaps(label, shape)


def test_labels_kept_while_moving_are_avoided_by_newcomers():
    """While the map moves (freeze) the labels already shown stay; a polygon
    arriving then keeps clear of them ("Never overlap"). It was placed on
    top of them, and at the next update the label shown first vanished."""
    script = f"""
import {{ labelPoints, newLabelState }} from {json.dumps('file://' + MODULE)};
const maplibregl = {{ MercatorCoordinate: class {{
  constructor(x, y) {{ this.x = x; this.y = y; }}
  toLngLat() {{ return {{ lng: this.x, lat: this.y }}; }} }} }};
const square = (id, x0, y0, x1, y1) => ({{ _x: 0, _y: 0, _z: 0, properties: {{ q2vt_orig_id: id }},
  _vectorTileFeature: {{ extent: 4096, loadGeometry: () => [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
    .map(([x, y]) => ({{ x, y }}))] }} }});
const options = {{ state: newLabelState(), precision: 0.0002, labelBox: () => [0.05, 0.01] }};
const a = square(1, 1000, 1000, 1200, 1300), b = square(2, 1250, 1000, 1500, 1350);
const ids = (out) => out.features.map((f) => f.properties.q2vt_orig_id);
console.log(JSON.stringify([ids(labelPoints([a], [0, 0, 1, 1], maplibregl, options)),
  ids(labelPoints([a, b], [0, 0, 1, 1], maplibregl, {{ ...options, freeze: true }}))]));
"""
    run = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    alone, moving = json.loads(run.stdout)
    assert alone == [1] and moving == [1]


def test_html_labels_get_a_box():
    """HTML labels ("format" text-field, one section per style run): measured
    by their sections (each with its font scale); they had no box, so they
    were not kept on the screen, clear of other labels or off short lines."""
    plain = _js("m.layoutBoxes({'text-field': ['get', 't'], 'text-size': 10}, 15, 1)({t: 'Parcel 12'})")
    fmt = ["format", ["coalesce", ["get", "h0"], ""], {"font-scale": ["to-number", ["get", "s0"], 1]},
           ["coalesce", ["get", "h1"], ""], {"font-scale": ["to-number", ["get", "s1"], 1]}]
    same = _js(f"m.layoutBoxes({{'text-field': {json.dumps(fmt)}, 'text-size': 10}}, 15, 1)"
               "({h0: 'Parcel ', h1: '12'})")
    assert same == pytest.approx(plain)
    small = _js(f"m.layoutBoxes({{'text-field': {json.dumps(fmt)}, 'text-size': 10}}, 15, 1)"
                "({h0: 'Parcel ', h1: '12', s1: 0.5})")
    assert small[0] == pytest.approx(plain[0] - 2 * 0.6 * 10 / 4) and small[1] == pytest.approx(plain[1])
    assert _js(f"m.evaluate({json.dumps(fmt)}, 15, {{h0: 'A', h1: 'B'}})") == "AB"
    # A line label in HTML on a line shorter than the label: no label.
    script_box = _js(f"m.layoutBoxes({{'text-field': {json.dumps(fmt)}, 'text-size': 10}}, 15, 0.001)"
                     "({h0: 'Long street ', h1: 'name'})")
    assert _lines([(1, [[1000, 1000], [1300, 1000]])], [0, 0, 1, 1], {"box": script_box}) == {}
