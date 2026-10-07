"""Point cluster and point displacement renderers: the exported datasets
(grouped per zoom band) drawn with their converted symbols look like QGIS
drawing the original renderer at the same scale."""

import math
import os
import sys

import pytest
from qgis.core import (QgsCoordinateReferenceSystem, QgsMapSettings, QgsMarkerSymbol,
                       QgsProcessingFeedback, QgsPointClusterRenderer,
                       QgsPointDisplacementRenderer, QgsRectangle, QgsSingleSymbolRenderer)
from qgis.PyQt.QtCore import QSize

from q2vt_render import ink_mask, mask_difference, render

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_materialize import EXTENT, _layer  # noqa: E402  pylint: disable=wrong-import-position

# A tight group of six, a pair 5 m apart and a lone point (3 mm tolerance
# is about 9 m at the test scale).
POINTS = ([f"POINT({-50 + 3 * math.cos(k)} {3 * math.sin(k)})" for k in range(6)]
          + ["POINT(0 -60)", "POINT(5 -60)", "POINT(60 40)"])


def _zoom(extent, size=(300, 300)) -> float:
    from q2vt_plugin.src.utils.zoom_levels import ZoomLevels  # pylint: disable=import-error
    settings = QgsMapSettings()
    settings.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    settings.setExtent(extent)
    settings.setOutputSize(QSize(*size))
    return math.log2(ZoomLevels.zoom_to_scale(0) / settings.scale())


def _export(layer, tmp_path, low, high):
    """Like test_materialize._export, for the zooms around the test scale
    (point groups are exported per zoom band)."""
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener  # pylint: disable=import-error
    from q2vt_plugin.src.core.rules_exporter import RulesExporter  # pylint: disable=import-error
    from fidelity.diagnostics import DiagnosticCollector
    from q2vt_fixtures import reset_project
    reset_project(layer)
    diags = DiagnosticCollector()
    rules = RulesFlattener(low, high, str(tmp_path), QgsProcessingFeedback(), diags,
                           extent=EXTENT).flatten_all_rules()
    utils = tmp_path / "utils"
    utils.mkdir()
    layers, rules = RulesExporter(rules, EXTENT, low, high, str(utils), 0, QgsProcessingFeedback(),
                                  diagnostics=diags).export()
    by_name = {l.name(): l for l in layers}
    rendered = []
    for rule in rules:
        out = by_name[rule.output_dataset]
        out.setRenderer(QgsSingleSymbolRenderer(rule.rule.symbol().clone()))
        rendered.append(out)
    return rendered, rules, diags


def _compare(plugin, tmp_path, renderer):
    layer = _layer("Point", POINTS, str(tmp_path / "pts.gpkg"))
    renderer.setEmbeddedRenderer(QgsSingleSymbolRenderer(
        QgsMarkerSymbol.createSimple({"name": "circle", "size": "2", "color": "#2b83ba"})))
    layer.setRenderer(renderer)
    expected = render([layer], EXTENT)
    zoom = _zoom(EXTENT)
    rendered, rules, diags = _export(layer, tmp_path, int(zoom) - 1, int(zoom) + 1)
    visible = [(out, rule) for out, rule in zip(rendered, rules)
               if rule.point_group and rule.visibility is not None and rule.visibility.contains(zoom)]
    assert visible, [r.visibility for r in rules]
    got = render([out for out, _ in reversed(visible)], EXTENT)  # first rule = bottom
    return expected, got, [rule for _, rule in visible], diags


def test_point_cluster_groups_like_qgis(plugin, tmp_path):
    expected, got, rules, _ = _compare(plugin, tmp_path, QgsPointClusterRenderer())
    roles = {rule.point_group[1] for rule in rules}
    assert roles == {"cluster", "members"}, roles
    # Two clusters (6 and 2 points) and the lone point, like QGIS.
    assert mask_difference(ink_mask(expected), ink_mask(got)) < 0.08


def test_point_displacement_places_members_like_qgis(plugin, tmp_path):
    renderer = QgsPointDisplacementRenderer()
    renderer.setCircleRadiusAddition(0.5)
    expected, got, rules, _ = _compare(plugin, tmp_path, renderer)
    roles = {rule.point_group[1] for rule in rules}
    assert {"members", "center", "circle"} <= roles, roles
    assert mask_difference(ink_mask(expected), ink_mask(got)) < 0.08


def test_point_displacement_grid(plugin, tmp_path):
    renderer = QgsPointDisplacementRenderer()
    renderer.setPlacement(QgsPointDisplacementRenderer.Placement.Grid)
    expected, got, rules, _ = _compare(plugin, tmp_path, renderer)
    assert "grid" in {rule.point_group[1] for rule in rules}
    assert mask_difference(ink_mask(expected), ink_mask(got)) < 0.08


def test_grouping_follows_qgis_order_and_tolerance():
    from fidelity import point_groups as pg
    # The second point joins the first group; the third is within tolerance
    # of the group's centre but not of its first point: a new group.
    groups = pg.group_points([(0, 0), (4, 0), (9, 0), (100, 0)], 5)
    assert groups == [[0, 1], [2], [3]]
    # Ring: radius max(diagonal / 2, n * diagonal / 2 pi); the first member
    # straight below the centre (painter y down), then clockwise on screen.
    positions, radius, _ = pg.displaced((0, 0), 4, pg.RING, 2.0, 2.0, 0.0)
    assert radius == pytest.approx(4 / math.pi)
    assert positions[0] == pytest.approx((0, -radius))
    assert positions[1] == pytest.approx((radius, 0), abs=1e-9)
    # Grid: rows of 2 for 4 members, centred; QGIS joins row and column neighbours.
    positions, _, size = pg.displaced((0, 0), 4, pg.GRID, 2.0, 2.0, 0.0)
    assert size == 2 and len(pg.grid_lines(positions, size)) == 4
