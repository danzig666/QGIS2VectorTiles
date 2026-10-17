"""The style gallery's along-the-line score (tools/gallery/along.py): markers
shifted along their line are forgiven, missing or wrongly spaced ones are
not."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "tools", "gallery"))
import along  # noqa: E402  pylint: disable=wrong-import-position

PATH = [(40, 80), (160, 320), (240, 140), (360, 300)]


def _draw(spacing, phase, skip=0):
    """A hairline along PATH with dots of radius 3 px every ``spacing`` px
    from ``phase`` (every ``skip``-th one left out)."""
    yy, xx = np.mgrid[0:400, 0:400] + 0.5
    img = np.zeros((400, 400), dtype=bool)
    pts = np.array(PATH, dtype=float)
    arc = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(pts, axis=0).T))])
    for a, b in zip(pts[:-1], pts[1:]):
        d = (b - a) / np.hypot(*(b - a))
        u = (xx - a[0]) * d[0] + (yy - a[1]) * d[1]
        t = (yy - a[1]) * d[0] - (xx - a[0]) * d[1]
        img |= (u >= 0) & (u <= np.hypot(*(b - a))) & (abs(t) <= 0.6)
    for k, s in enumerate(np.arange(phase, arc[-1], spacing)):
        if skip and k % skip == 1:
            continue
        i = min(np.searchsorted(arc, s, side="right") - 1, len(pts) - 2)
        p = pts[i] + (pts[i + 1] - pts[i]) * (s - arc[i]) / (arc[i + 1] - arc[i])
        img |= (xx - p[0]) ** 2 + (yy - p[1]) ** 2 <= 9
    return img


def _scores(qgis, browser):
    unmatched, total = along.unmatched_along(qgis, browser, [(PATH, False)], along.LINE_HALF_WIDTH)
    strict = int((qgis & ~along.grow(browser)).sum() + (browser & ~along.grow(qgis)).sum())
    return strict / total, unmatched / total


def test_markers_shifted_along_the_line_are_forgiven():
    qgis = _draw(20, 0)
    assert _scores(qgis, qgis) == (0.0, 0.0)
    strict, along_ = _scores(qgis, _draw(20, 7))
    assert strict > 0.15 and along_ < 0.03
    strict, along_ = _scores(qgis, _draw(20.4, 0))   # the banded spacing's drift
    assert strict > 0.1 and along_ < 0.02


def test_missing_or_misspaced_markers_still_count():
    qgis = _draw(20, 0)
    strict, along_ = _scores(qgis, _draw(20, 7, skip=2))
    assert along_ > 0.04 and along_ <= strict
    strict, along_ = _scores(qgis, _draw(22, 0))     # 10 % too far apart
    assert along_ > 0.04 and along_ <= strict


def _dots(spacing, phase, size=3):
    """A square of dots ``spacing`` px apart from ``phase`` (x and y)."""
    yy, xx = np.mgrid[0:400, 0:400]
    on = (((xx - phase) % spacing) < size) & (((yy - phase) % spacing) < size)
    return on & (xx > 50) & (xx < 350) & (yy > 50) & (yy < 350)


def test_fill_patterns_shifted_are_forgiven():
    ring = [[(50, 50), (350, 50), (350, 350), (50, 350), (50, 50)], True]
    qgis = _dots(12, 0)
    shifted = along.along_score(qgis, _dots(12, 5), [ring], "Polygon")
    denser = along.along_score(qgis, _dots(9, 0), [ring], "Polygon")
    assert shifted < 0.02
    assert denser > 0.15
