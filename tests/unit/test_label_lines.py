"""Repeated curved line labels laid out per zoom (core/label_lines.py): QGIS's
repeat parts and character chords, MapLibre's angle check ported exactly."""

import json
import math
import os
import random
import re
import shutil
import subprocess

import pytest

import label_lines as ll

SHARED_MJS = os.path.join(os.path.dirname(__file__), "..", "..", "resources", "ml_viewer",
                          "maplibre-gl-shared.mjs")


def _zigzag(x, y, count, segment, half_turn, direction=0.0):
    """A line turning ``2 * half_turn`` degrees at every vertex, one way then
    the other (a river's wiggles)."""
    xs, ys = [x], [y]
    for i in range(count):
        heading = math.radians(direction + (half_turn if i % 2 else -half_turn))
        xs.append(xs[-1] + segment * math.cos(heading))
        ys.append(ys[-1] + segment * math.sin(heading))
    return xs, ys


def test_repeat_parts_cut_the_line_as_pal_does():
    parts = ll.repeat_parts(1000, 80, 264.6)
    assert [round(b - a, 2) for a, b in parts] == [333.33] * 3
    assert parts[0][0] == 0 and parts[-1][1] == pytest.approx(1000)
    # A label longer than the repeat distance: whole multiples of it (400).
    assert ll.repeat_parts(1000, 300, 200) == [(0.0, 500.0), (500.0, 1000.0)]
    # One part only, or a line not longer than the repeat: the whole line.
    assert ll.repeat_parts(300, 80, 264.6) == [(0.0, 300)]
    assert ll.repeat_parts(200, 80, 264.6) == [(0.0, 200)]


def test_characters_end_on_the_line_their_advance_away():
    """QGIS nextCharPosition: on a straight segment the chord follows it; at
    a corner the chord ends where the circle of the advance leaves the line."""
    xs, ys = [0.0, 10.0, 10.0], [0.0, 0.0, 10.0]
    points, reach = ll.chord_points(xs, ys, ll.cumulative_lengths(xs, ys), 2.0, [5.0, 5.0])
    assert points[:2] == [(2.0, 0.0), (7.0, 0.0)]
    assert points[2] == pytest.approx((10.0, 4.0))          # 3 along, 4 up: 5 away
    assert reach == pytest.approx(14.0)
    assert ll.chord_points(xs, ys, ll.cumulative_lengths(xs, ys), 12.0, [5.0, 5.0]) is None


def test_character_turn_limits_inside_and_outside():
    """maxCurvedCharAngleIn limits turns to the left (on screen, y down),
    maxCurvedCharAngleOut (negative) turns to the right."""
    left = ll.char_turns([(0, 0), (10, 0), (10 + 10 * math.cos(0.5), -10 * math.sin(0.5))])
    assert left[0] == pytest.approx(0.5)
    assert not ll.char_turns_ok(left, 25, -40) and ll.char_turns_ok(left, 30, -20)
    assert not ll.char_turns_ok([-0.5], 30, -20) and ll.char_turns_ok([-0.5], 0, 0)


def test_labels_sit_in_the_middle_of_their_parts_on_a_straight_line():
    xs, ys = [20.0, 500.0], [100.0, 100.0]
    windows = ll.label_windows(xs, ys, [6.0] * 10, 150.0, 2.0, angle_window=7.2, margin=2.0)
    parts = ll.repeat_parts(480, 60, 150)
    assert len(windows) == len(parts) == 3
    for window, (start, end) in zip(windows, parts):
        middle = (window[0][0] + window[-1][0]) / 2 - 20
        assert middle == pytest.approx((start + end) / 2, abs=1.0)
        assert window[-1][0] - window[0][0] == pytest.approx(60 + 2 * 2.0)  # the margins


def test_a_wiggly_river_gets_its_labels():
    """Koornlands at zoom 12: a vertex every 4 px turning 30 degrees.
    MapLibre's check rejects the river itself (two turns per 0.6 em); the
    characters' chords average the wiggles out, as in QGIS."""
    xs, ys = _zigzag(10.0, 100.0, 120, 4.0, 15.0)
    advances = [7.0] * 12
    river = [(x * 16, y * 16) for x, y in zip(xs, ys)]
    assert ll.center_anchor(river, 84 * 16, 7.2 * 16, math.radians(25)) is None
    windows = ll.label_windows(xs, ys, advances, 150.0, 7.5, angle_window=7.2)
    assert len(windows) == len(ll.repeat_parts(ll.cumulative_lengths(xs, ys)[-1], 84, 150)) == 3
    for window in windows:
        line = ll.tile_line(window)
        assert ll.center_anchor(line, 84 * 16, 7.2 * 16, math.radians(25)) is not None
        assert max(abs(t) for t in ll.char_turns(window)) <= math.radians(25)


def test_no_label_where_no_placement_keeps_qgis_character_angles():
    """Wiggles longer than a character (12 px, 30 degrees): every placement
    turns too much between two characters somewhere, so QGIS draws none."""
    xs, ys = _zigzag(10.0, 100.0, 40, 12.5, 15.0)
    assert ll.label_windows(xs, ys, [7.0] * 12, 150.0, 7.5, angle_window=7.2) == []


def test_windows_never_cross_a_tile_edge():
    rng = random.Random(7)
    xs, ys = _zigzag(30.0, 40.0, 900, 3.0, 12.0, direction=31.0)
    xs = [x + rng.uniform(-0.3, 0.3) for x in xs]
    windows = ll.label_windows(xs, ys, [6.5] * 9, 120.0, 7.5, angle_window=7.2)
    assert len(windows) >= 15
    for window in windows:
        tiles = {(math.floor(x / 512), math.floor(y / 512)) for x, y in window}
        assert len(tiles) == 1
        tx, ty = tiles.pop()
        assert all(1 <= x - 512 * tx <= 511 and 1 <= y - 512 * ty <= 511 for x, y in window)


def _maplibre_functions():
    """The vendored MapLibre's checkMaxAngle and getCenterAnchor (and the
    helpers they call), as JavaScript source."""
    source = open(SHARED_MJS, encoding="utf-8").read()

    def function(name):
        start = source.index(f"function {name}(")
        depth, i = 0, source.index("{", start)
        while True:
            depth += {"{": 1, "}": -1}.get(source[i], 0)
            i += 1
            if depth == 0:
                return source[start:i]
    check = re.search(r"function (\w+)\(e,t,n,r,i\)\{if\(t\.segment===void 0\|\|n===0\)return!0;",
                      source)
    center = re.search(r"function (\w+)\(e,t,n,r,i,a\)\{let o=([\w$]+)\(n,i,a\),s=([\w$]+)\(n,r\)"
                       r"\*a,c=0,l=([\w$]+)\(e\)/2;", source)
    assert check and center, "MapLibre changed: check the port in core/label_lines.py"
    body = function(center.group(1))
    anchor, interpolate = re.search(r"new ([\w$]+)\(([\w$]+)\.number\(", body).groups()
    names = [check.group(1), center.group(1), center.group(2), center.group(3), center.group(4)]
    return "\n".join(function(name) for name in names), names[0], names[1], anchor, interpolate


def test_check_max_angle_is_maplibre_s(tmp_path):
    """The port gives MapLibre's answers (the vendored maplibre-gl-shared.mjs
    run in Node) on random lines, labels and anchors."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is not installed")
    functions, check, center, anchor, interpolate = _maplibre_functions()
    rng = random.Random(3)
    cases = []
    for _ in range(400):
        xs, ys = [rng.uniform(1000, 7000)], [rng.uniform(1000, 7000)]
        heading, wiggle = rng.uniform(-math.pi, math.pi), rng.choice([0.02, 0.1, 0.4])
        for _ in range(rng.randint(2, 30)):
            heading += rng.gauss(0, wiggle)
            step = rng.uniform(0, 6) if rng.random() < 0.1 else rng.uniform(30, 250)
            xs.append(round(xs[-1] + step * math.cos(heading)))
            ys.append(round(ys[-1] + step * math.sin(heading)))
        line = list(zip(xs, ys))
        segment = rng.randrange(len(line) - 1)
        t = rng.random()
        a, b = line[segment], line[segment + 1]
        total = sum(math.dist(line[i], line[i + 1]) for i in range(len(line) - 1))
        cases.append({"line": line, "length": rng.uniform(0, total / 2), "max": math.radians(
            rng.uniform(10, 60)), "window": rng.uniform(60, 250), "segment": segment,
                      "anchor": [a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])]})
    script = tmp_path / "check.mjs"
    script.write_text(f"""
class Point {{
  constructor(x, y) {{ this.x = x; this.y = y; }}
  distSqr(e) {{ let t = e.x - this.x, n = e.y - this.y; return t * t + n * n; }}
  dist(e) {{ return Math.sqrt(this.distSqr(e)); }}
  angleTo(e) {{ return Math.atan2(this.y - e.y, this.x - e.x); }}
  _round() {{ this.x = Math.round(this.x); this.y = Math.round(this.y); return this; }}
}}
class {anchor} extends Point {{
  constructor(x, y, angle, segment) {{ super(x, y); this.angle = angle;
    if (segment !== undefined) this.segment = segment; }}
}}
const {interpolate} = {{number: (e, t, n) => e + n * (t - e)}};
{functions}
const cases = JSON.parse(require("fs").readFileSync(process.argv[2], "utf8"));
const out = cases.map(c => {{
  const line = c.line.map(p => new Point(p[0], p[1]));
  const anchor = new {anchor}(c.anchor[0], c.anchor[1], 0, c.segment);
  const found = {center}(line, c.max, {{left: 0, right: c.length}}, undefined, 24, c.window / 14.4);
  return [{check}(line, anchor, c.length, c.window, c.max),
          found ? [found.x, found.y, found.segment] : null];
}});
console.log(JSON.stringify(out));
""".replace("require(\"fs\")", "(await import('fs'))"))
    data = tmp_path / "cases.json"
    data.write_text(json.dumps(cases))
    answers = json.loads(subprocess.run([node, str(script), str(data)], check=True,
                                        capture_output=True, text=True).stdout)
    checked = {True: 0, False: 0, "found": 0}
    for case, (accepted, found) in zip(cases, answers):
        anchor = (case["anchor"][0], case["anchor"][1], case["segment"])
        assert ll.check_max_angle(case["line"], anchor, case["length"], case["window"],
                                  case["max"]) is accepted, case
        checked[accepted] += 1
        # getCenterAnchor: glyph size 24, box scale window / (0.6 * 24)
        mine = ll.center_anchor(case["line"], case["length"] * (case["window"] / 14.4),
                                case["window"], case["max"])
        assert (list(mine) if mine else None) == found, case
        checked["found"] += found is not None
    assert min(checked.values()) > 50, checked  # every answer is exercised
