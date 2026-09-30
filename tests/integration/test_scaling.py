"""Pattern materialization on a large, detailed layer: completes in bounded
time and draws every pattern (no silent drops).

Measured before pieces / native random points (one 0.8 km² polygon with 200
vertices): clipped cross grid 1.5 s, random fill 6.4 s; a 25 km² polygon lost
its grid entirely (per-feature cap), and clipping a detailed polygon could
return nothing (GEOS). Timings are printed for reference; the bound is generous
so slower machines pass.
"""

import math
import time

import pytest
from qgis.core import (Qgis, QgsFeature, QgsField, QgsFillSymbol, QgsGeometry,
                       QgsPointPatternFillSymbolLayer, QgsProcessingFeedback,
                       QgsRandomMarkerFillSymbolLayer, QgsRectangle, QgsSimpleLineSymbolLayer,
                       QgsSimpleMarkerSymbolLayer, QgsMarkerSymbol, QgsSingleSymbolRenderer,
                       QgsVectorLayer)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor

from q2vt_fixtures import reset_project, to_geopackage

SIDE = 4  # 4 x 4 polygons of ~0.3 km², 600 vertices each
RADIUS = 300.0
EXTENT = QgsRectangle(-400, -400, SIDE * 700, SIDE * 700)


def _blob(cx, cy, vertices=600):
    ring = []
    for k in range(vertices):
        a = 2 * math.pi * k / vertices
        r = RADIUS * (1 + 0.15 * math.sin(11 * a) + 0.05 * math.sin(37 * a))
        ring.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    ring.append(ring[0])
    return "POLYGON((" + ", ".join(f"{x:.3f} {y:.3f}" for x, y in ring) + "))"


def _big_layer(path):
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "big", "memory")
    layer.dataProvider().addAttributes([QgsField("id", QVariant.Int)])
    layer.updateFields()
    features = []
    for i in range(SIDE):
        for j in range(SIDE):
            feature = QgsFeature(layer.fields())
            feature.setAttributes([i * SIDE + j])
            feature.setGeometry(QgsGeometry.fromWkt(_blob(i * 700, j * 700)))
            features.append(feature)
    layer.dataProvider().addFeatures(features)
    return to_geopackage(layer, path)


def _symbol():
    cross = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Cross, 4)
    cross.setSizeUnit(Qgis.RenderUnit.MapUnits)
    cross.setStrokeWidth(0.3)
    cross.setStrokeWidthUnit(Qgis.RenderUnit.MapUnits)
    grid = QgsPointPatternFillSymbolLayer()
    grid.setSubSymbol(QgsMarkerSymbol([cross]))
    for name in ("DistanceX", "DistanceY"):
        getattr(grid, f"set{name}")(6)
        getattr(grid, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    grid.setClipMode(Qgis.MarkerClipMode.Shape)
    dot = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, 1)
    dot.setSizeUnit(Qgis.RenderUnit.MapUnits)
    random_fill = QgsRandomMarkerFillSymbolLayer(1, Qgis.PointCountMethod.DensityBased, 0, 7)
    random_fill.setDensityArea(40)
    random_fill.setDensityAreaUnit(Qgis.RenderUnit.MapUnits)
    random_fill.setSubSymbol(QgsMarkerSymbol([dot]))
    outline = QgsSimpleLineSymbolLayer(QColor("black"), 0.5)
    outline.setWidthUnit(Qgis.RenderUnit.MapUnits)
    outline.setUseCustomDashPattern(True)
    outline.setCustomDashVector([4.0, 2.0])
    outline.setCustomDashPatternUnit(Qgis.RenderUnit.MapUnits)
    return QgsFillSymbol([grid, random_fill, outline])


def test_large_detailed_layer_patterns_scale(plugin, tmp_path):
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener
    from q2vt_plugin.src.core.rules_exporter import RulesExporter
    from fidelity.diagnostics import DiagnosticCollector
    layer = _big_layer(str(tmp_path / "big.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(_symbol()))
    reset_project(layer)
    diags = DiagnosticCollector()
    started = time.time()
    rules = RulesFlattener(14, 18, str(tmp_path), QgsProcessingFeedback(), diags).flatten_all_rules()
    utils = tmp_path / "utils"
    utils.mkdir()
    layers, rules = RulesExporter(rules, EXTENT, 14, 18, str(utils), 0, QgsProcessingFeedback(),
                                  diagnostics=diags).export()
    elapsed = time.time() - started
    by_name = {l.name(): l for l in layers}
    produced = {}
    for rule in rules:
        if rule.recipe is not None:
            produced[rule.recipe.kind] = produced.get(rule.recipe.kind, 0) + \
                by_name[rule.output_dataset].featureCount()
    print(f"\nexport of {SIDE * SIDE} polygons: {elapsed:.1f} s; features: {produced}")
    assert set(produced) >= {"grid_points", "random_points", "dash_segments"}
    area = sum(f.geometry().area() for f in layer.getFeatures())
    # QGIS count per feature: ceil(area / 40) (one point per 40 m²).
    assert produced["random_points"] == pytest.approx(area / 40, rel=0.01)
    assert produced["grid_points"] > area / 36  # every cell of every polygon has line work
    assert not diags.by_code("Q2VT_RULE_OUTPUT_EMPTY")
    assert elapsed < 240


def test_very_large_polygon_keeps_its_pattern(plugin, tmp_path):
    """A 25 km² polygon with a 10 m pattern (250 000 markers) is built piece
    by piece; a per-feature cap used to return nothing for it."""
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener
    from q2vt_plugin.src.core.rules_exporter import RulesExporter
    from fidelity.diagnostics import DiagnosticCollector
    memory = QgsVectorLayer("Polygon?crs=EPSG:3857", "forest", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt("POLYGON((0 0, 5000 0, 5000 5000, 0 5000, 0 0))"))
    memory.dataProvider().addFeature(feature)
    layer = to_geopackage(memory, str(tmp_path / "forest.gpkg"))
    dot = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Circle, 2)
    dot.setSizeUnit(Qgis.RenderUnit.MapUnits)
    grid = QgsPointPatternFillSymbolLayer()
    grid.setSubSymbol(QgsMarkerSymbol([dot]))
    for name in ("DistanceX", "DistanceY"):
        getattr(grid, f"set{name}")(10)
        getattr(grid, f"set{name}Unit")(Qgis.RenderUnit.MapUnits)
    grid.setClipMode(Qgis.MarkerClipMode.CentroidWithin)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([grid])))
    reset_project(layer)
    diags = DiagnosticCollector()
    started = time.time()
    rules = RulesFlattener(16, 17, str(tmp_path), QgsProcessingFeedback(), diags).flatten_all_rules()
    utils = tmp_path / "utils"
    utils.mkdir()
    layers, rules = RulesExporter(rules, QgsRectangle(-100, -100, 5100, 5100), 16, 17,
                                  str(utils), 0, QgsProcessingFeedback(),
                                  diagnostics=diags).export()
    by_name = {l.name(): l for l in layers}
    points = sum(by_name[r.output_dataset].featureCount() for r in rules if r.recipe)
    print(f"\n25 km² grid: {points} markers in {time.time() - started:.1f} s")
    assert points == pytest.approx(500 * 500, rel=0.01)
