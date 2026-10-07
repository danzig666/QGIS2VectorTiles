"""Gradient and shapeburst fills become colour bands that look like QGIS:
the original layer and the exported band datasets (with their converted
symbols) are both rendered by QGIS and compared pixel by pixel."""

import json
import os
import sys

import numpy as np
import pytest
from qgis.core import (Qgis, QgsFeatureRequest, QgsFillSymbol, QgsGradientColorRamp, QgsGradientFillSymbolLayer, QgsGradientStop,
                       QgsLineSymbol, QgsShapeburstFillSymbolLayer, QgsSimpleLineSymbolLayer,
                       QgsSingleSymbolRenderer)
from qgis.PyQt.QtGui import QColor

from q2vt_render import render

BAND_FIELD = "q2vt_mat_band"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_materialize import EXTENT, _layer  # noqa: E402  pylint: disable=wrong-import-position

HOUSE = "POLYGON((-90 -80, 90 -80, 90 40, 0 90, -90 40, -90 -80), (-50 -50, -20 -50, -20 -20, -50 -20, -50 -50))"


def _pixels(image):
    image = image.convertToFormat(image.Format.Format_RGB32)
    ptr = image.constBits()
    ptr.setsize(image.sizeInBytes())
    return np.frombuffer(ptr, np.uint8).reshape(image.height(), image.width(), 4)[..., :3].astype(int)


def _export(layer, tmp_path):
    """Like test_materialize._export, at a few zooms (bands are per rule)."""
    from qgis.core import QgsProcessingFeedback
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener  # pylint: disable=import-error
    from q2vt_plugin.src.core.rules_exporter import RulesExporter  # pylint: disable=import-error
    from fidelity.diagnostics import DiagnosticCollector
    from q2vt_fixtures import reset_project
    reset_project(layer)
    diags = DiagnosticCollector()
    rules = RulesFlattener(14, 15, str(tmp_path), QgsProcessingFeedback(), diags).flatten_all_rules()
    utils = tmp_path / "utils"
    utils.mkdir()
    layers, rules = RulesExporter(rules, EXTENT, 14, 15, str(utils), 0, QgsProcessingFeedback(),
                                  diagnostics=diags).export()
    by_name = {l.name(): l for l in layers}
    rendered = []
    for rule in rules:
        out = by_name[rule.output_dataset]
        renderer = QgsSingleSymbolRenderer(rule.rule.symbol().clone())
        if out.fields().indexOf(BAND_FIELD) >= 0:
            # Bands in order, as the style's fill-sort-key draws them (QGIS
            # would iterate the dataset in spatial index order).
            renderer.setOrderBy(QgsFeatureRequest.OrderBy([
                QgsFeatureRequest.OrderByClause(BAND_FIELD, True)]))
            renderer.setOrderByEnabled(True)
        out.setRenderer(renderer)
        rendered.append(out)
    return rendered, rules, diags


def _compare(plugin, tmp_path, fill_layer):
    from scipy import ndimage
    layer = _layer("Polygon", [HOUSE], str(tmp_path / "src.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([fill_layer])))
    expected = _pixels(render([layer], EXTENT))
    rendered, rules, diags = _export(layer, tmp_path)
    got = _pixels(render(list(reversed(rendered)), EXTENT))  # first rule = bottom band
    # Inside the polygon, away from its anti-aliased edge.
    inside = ndimage.binary_erosion((expected != 255).any(axis=2), iterations=2)
    diff = np.abs(expected - got).max(axis=2)
    return rules, diags, float(diff[inside].mean()), float(np.percentile(diff[inside], 99))


@pytest.mark.parametrize("kind, spread", [
    (Qgis.GradientType.Linear, Qgis.GradientSpread.Pad),
    (Qgis.GradientType.Linear, Qgis.GradientSpread.Reflect),
    (Qgis.GradientType.Linear, Qgis.GradientSpread.Repeat),
    (Qgis.GradientType.Radial, Qgis.GradientSpread.Pad),
    (Qgis.GradientType.Radial, Qgis.GradientSpread.Repeat),
    (Qgis.GradientType.Conical, Qgis.GradientSpread.Pad),
])
def test_gradient_fill_bands_match_qgis(plugin, tmp_path, kind, spread):
    fill = QgsGradientFillSymbolLayer(QColor("#1f4e9c"), QColor("#ffd23f"),
                                      Qgis.GradientColorSource.SimpleTwoColor, kind)
    fill.setGradientSpread(spread)
    fill.setReferencePoint1(fill.referencePoint1().__class__(0.3, 0.2))
    fill.setReferencePoint2(fill.referencePoint2().__class__(0.6, 0.8) if spread == Qgis.GradientSpread.Pad
                            else fill.referencePoint2().__class__(0.45, 0.5))
    rules, diags, mean, p99 = _compare(plugin, tmp_path, fill)
    assert len(rules) == 1 and rules[0].recipe.kind == "color_bands"
    assert len(dict(rules[0].recipe.params)["bands"]) >= 8
    assert not [d for d in diags.items if d.code == "Q2VT_UNSUPPORTED_SYMBOL_LAYER"]
    # Colour steps of ~1 level; repeat and conical gradients also have sharp
    # colour jumps, anti-aliased a pixel differently.
    assert mean < 3.5 and p99 < (40 if spread == Qgis.GradientSpread.Repeat or kind == Qgis.GradientType.Conical
                                 else 6), (mean, p99)


def test_colour_ramp_gradient_matches_qgis(plugin, tmp_path):
    ramp = QgsGradientColorRamp(QColor("#0d0887"), QColor("#f0f921"))
    ramp.setStops([QgsGradientStop(0.5, QColor("#cc4778"))])
    fill = QgsGradientFillSymbolLayer()
    fill.setGradientColorType(Qgis.GradientColorSource.ColorRamp)
    fill.setColorRamp(ramp)
    fill.setGradientType(Qgis.GradientType.Radial)
    fill.setReferencePoint1(fill.referencePoint1().__class__(0, 0))
    fill.setReferencePoint2(fill.referencePoint2().__class__(1, 1))
    _, _, mean, p99 = _compare(plugin, tmp_path, fill)
    assert mean < 3.5 and p99 < 30, (mean, p99)


@pytest.mark.parametrize("whole", [True, False])
def test_shapeburst_fill_bands_match_qgis(plugin, tmp_path, whole):
    fill = QgsShapeburstFillSymbolLayer(QColor("#08306b"), QColor("#deebf7"))
    fill.setUseWholeShape(whole)
    fill.setMaxDistance(25)
    fill.setDistanceUnit(Qgis.RenderUnit.MapUnits)
    fill.setBlurRadius(0)
    rules, _, mean, p99 = _compare(plugin, tmp_path, fill)
    assert len(rules) == 1 and rules[0].recipe.kind == "color_bands"
    assert mean < 8 and p99 < 40, (mean, p99)


def test_line_glow_and_shadow_become_line_layers(plugin, tmp_path):
    from qgis.core import QgsDrawSourceEffect, QgsDropShadowEffect, QgsEffectStack, QgsOuterGlowEffect
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    layer = _layer("LineString", ["LINESTRING(-80 -60, 0 60, 80 -40)"], str(tmp_path / "l.gpkg"))
    line = QgsSimpleLineSymbolLayer(QColor("#e31a1c"), 1.0)
    glow = QgsOuterGlowEffect()
    glow.setColor(QColor("#ef2929"))
    glow.setSpread(2)
    glow.setBlurLevel(0.8)
    glow.setOpacity(0.5)
    shadow = QgsDropShadowEffect()
    shadow.setOffsetAngle(135)
    shadow.setOffsetDistance(2)
    stack = QgsEffectStack()
    for effect in (shadow, glow, QgsDrawSourceEffect()):
        stack.appendEffect(effect)
    line.setPaintEffect(stack)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([line])))
    reset_project(layer)
    out = tmp_path / "out"
    out.mkdir()
    exporter = QGIS2VectorTiles(min_zoom=12, max_zoom=14, extent=EXTENT, output_dir=str(out), serve=False)
    result = exporter.convert_project_to_vector_tiles()
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    lines = [l for l in style["layers"] if l["type"] == "line"]
    # Shadow and glow per zoom (from a copy simplified at the effect's size,
    # zooms 12-14), then the line itself without effects.
    effects, main_def = lines[:-1], lines[-1]
    assert len(effects) == 6 and all(l["id"].endswith(("_fx0", "_fx1")) for l in effects)
    assert not main_def["id"].endswith(("_fx0", "_fx1"))
    assert main_def["source-layer"] not in {l["source-layer"] for l in effects}
    main = main_def["paint"]
    shadow_paint, glow_paint = effects[0]["paint"], effects[1]["paint"]
    assert shadow_paint["line-translate"][0] > 0 and shadow_paint["line-translate"][1] > 0  # down right
    assert glow_paint["line-color"] == "#ef2929" and glow_paint["line-blur"] > 0
    assert glow_paint["line-width"] > main["line-width"] and glow_paint["line-opacity"] == pytest.approx(0.5)
    report = json.load(open(os.path.join(result, "fidelity_report.json"), encoding="utf-8"))
    assert not [d for d in report["diagnostics"] if d["code"] == "Q2VT_UNSUPPORTED_EFFECT"]


def test_arrow_draws_every_fill_layer_with_its_screen_offset(plugin, tmp_path):
    """"pointing arrow": a black copy shifted on screen (drop shadow) under
    the orange arrow, as QGIS draws the two fill layers."""
    from qgis.core import QgsArrowSymbolLayer, QgsSimpleFillSymbolLayer
    from qgis.PyQt.QtCore import QPointF
    layer = _layer("LineString", ["LINESTRING(-80 -60, 0 60, 80 -40)"], str(tmp_path / "a.gpkg"))
    arrow = QgsArrowSymbolLayer()
    shadow = QgsSimpleFillSymbolLayer(QColor("black"))
    shadow.setOffset(QPointF(-1.2, 1.4))
    top = QgsSimpleFillSymbolLayer(QColor("#ff7f00"))
    arrow.setSubSymbol(QgsFillSymbol([shadow, top]))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol([arrow])))
    _, rules, _ = _export(layer, tmp_path)
    colors = [r.rule.symbol().symbolLayer(0).color().name() for r in rules]
    shadow_rules = [r for r in rules if r.translate]
    assert colors[0] == "#000000" and colors[-1] == "#ff7f00"
    assert shadow_rules and all(r.translate == (-1.2, 1.4, "MM") for r in shadow_rules)
    assert {r.rule.symbol().symbolLayer(0).color().name() for r in shadow_rules} == {"#000000"}
    assert all(r.translate is None for r in rules if r not in shadow_rules)
