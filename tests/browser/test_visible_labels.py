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
