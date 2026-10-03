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
    # Too narrow for the label's height, or too short for its length: no label
    # (QGIS drops candidates that stick out of the polygon).
    narrow = _strip(2048, 2048, 3000, 60, a)
    short = _strip(2048, 2048, 300, 300, a + 0.0)
    assert _free([(2, narrow), (3, short)], [0, 0, 1, 1], box) == {}


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
    assert 1 not in off and 2 not in off  # without the option: no label (as before)
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
