"""End-to-end exports through the real pipeline (processing + GDAL + style)."""

import json
import os

import pytest
from qgis.core import (QgsExpression, QgsExpressionContext, QgsExpressionContextUtils,
                       QgsFeature, QgsFillSymbol, QgsGeometry, QgsLinePatternFillSymbolLayer,
                       QgsPalLayerSettings, QgsProcessingException, QgsProcessingFeedback,
                       QgsProperty, QgsRectangle, QgsShapeburstFillSymbolLayer,
                       QgsSingleSymbolRenderer, QgsSymbolLayer, QgsTextFormat,
                       QgsVectorLayerSimpleLabeling, Qgis)
from qgis.PyQt.QtGui import QColor

from fidelity.validation import inspect_mbtiles
from q2vt_fixtures import reset_project, zoning_layer

EXTENT = QgsRectangle(2119000, 6019000, 2123000, 6023000)


class Feedback(QgsProcessingFeedback):
    def __init__(self):
        super().__init__()
        self.messages = []

    def pushInfo(self, info):  # noqa: N802
        self.messages.append(("info", info))

    def pushWarning(self, warning):  # noqa: N802
        self.messages.append(("warning", warning))

    def reportError(self, error, fatalError=False):  # noqa: N802,N803
        self.messages.append(("error", error))


@pytest.fixture
def export(plugin, tmp_path):
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-error

    def run(layer, min_zoom=10, max_zoom=14, **kwargs):
        reset_project(layer)
        out = tmp_path / "out"
        out.mkdir(exist_ok=True)
        exporter = QGIS2VectorTiles(min_zoom=min_zoom, max_zoom=max_zoom, extent=EXTENT,
                                    output_dir=str(out), feedback=Feedback(), serve=False,
                                    **kwargs)
        result = exporter.convert_project_to_vector_tiles()
        return exporter, result
    return run


def _hatched_labelled_layer(path):
    layer = zoning_layer(path=path)
    hatch = QgsLinePatternFillSymbolLayer()
    hatch.setLineAngle(45)
    hatch.setDistance(3)
    hatch.subSymbol().symbolLayer(0).setColor(QColor("black"))
    fill = QgsFillSymbol.createSimple({"color": "255,200,200", "outline_color": "0,0,0",
                                       "outline_width": "0.4"})
    fill.symbolLayer(0).setDataDefinedProperty(
        QgsSymbolLayer.Property.PropertyStrokeWidth, QgsProperty.fromField("width"))
    fill.appendSymbolLayer(hatch)
    layer.setRenderer(QgsSingleSymbolRenderer(fill))
    settings = QgsPalLayerSettings()
    settings.fieldName = "concat(\"zone\", ' ő ű')"
    settings.isExpression = True
    settings.placement = Qgis.LabelPlacement.OverPoint
    fmt = QgsTextFormat()
    fmt.setSize(10)
    settings.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    return layer


def test_vector_first_export(export, tmp_path):
    layer = _hatched_labelled_layer(str(tmp_path / "zoning.gpkg"))
    exporter, result = export(layer)
    assert result and os.path.isdir(result)
    report = json.loads(open(os.path.join(result, "fidelity_report.json"), encoding="utf-8").read())
    assert report["counts"]["error"] == 0, report
    assert os.path.exists(os.path.join(result, "fidelity_report.html"))

    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    source = style["sources"]["q2vt_tiles"]
    assert (source["minzoom"], source["maxzoom"]) == (10, 14)

    archive = inspect_mbtiles(os.path.join(result, "tiles.mbtiles"))
    assert set(archive["tile_counts"]) == {10, 11, 12, 13, 14}

    # Hatch: a real, non-transparent periodic cell at true 1x and 2x.
    patterns = [l for l in style["layers"] if "fill-pattern" in l.get("paint", {})]
    assert len(patterns) == 1
    name = patterns[0]["paint"]["fill-pattern"]
    one = json.load(open(os.path.join(result, "style", "sprite", "sprite.json")))[name]
    two = json.load(open(os.path.join(result, "style", "sprite", "sprite@2x.json")))[name]
    assert (two["width"], two["pixelRatio"]) == (one["width"] * 2, 2)
    from PIL import Image
    sheet = Image.open(os.path.join(result, "style", "sprite", "sprite.png"))
    crop = sheet.crop((one["x"], one["y"], one["x"] + one["width"], one["y"] + one["height"]))
    assert crop.getbbox() is not None

    # Data-defined outline width: typed, unit-converted, and its field survives pruning.
    outline = [l for l in style["layers"] if l["type"] == "line"][0]
    width = outline["paint"]["line-width"]
    assert width[0] == "*" and width[1][0] == "to-number"
    field = width[1][1][1]
    assert field in archive["vector_layers"][outline["source-layer"]]["fields"]

    # Labels: glyphs generated for the exact text-font, including ő/ű.
    label = [l for l in style["layers"] if "text-field" in l.get("layout", {})][0]
    stack = label["layout"]["text-font"][0]
    assert os.path.exists(os.path.join(result, "style", "glyphs", stack, "0-255.pbf"))
    assert os.path.exists(os.path.join(result, "style", "glyphs", stack, "256-511.pbf"))

    # Project untouched.
    assert type(layer.renderer()).__name__ == "QgsSingleSymbolRenderer"
    assert layer.labeling().settings().isExpression


def test_strict_mode_fails_before_publication(export, tmp_path):
    layer = zoning_layer(path=str(tmp_path / "zoning.gpkg"))
    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, QgsShapeburstFillSymbolLayer())
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    with pytest.raises(QgsProcessingException, match="Strict export failed"):
        export(layer, fidelity_mode=1)
    outputs = [d for d in (tmp_path / "out").iterdir() if d.is_dir()]
    assert len(outputs) == 1
    assert not (outputs[0] / "tiles.mbtiles").exists()
    assert not (outputs[0] / "style").exists()
    report = json.loads((outputs[0] / "fidelity_report.json").read_text(encoding="utf-8"))
    codes = {d["code"] for d in report["diagnostics"]}
    assert {"Q2VT_UNSUPPORTED_SYMBOL_LAYER", "Q2VT_STRICT_FAILED"} <= codes


def test_unsupported_fill_is_reported_not_drawn_black(export, tmp_path):
    layer = zoning_layer(path=str(tmp_path / "zoning.gpkg"))
    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, QgsShapeburstFillSymbolLayer())
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    exporter, result = export(layer)
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    assert not [l for l in style["layers"] if l["type"] == "fill"]
    assert exporter.diagnostics.by_code("Q2VT_UNSUPPORTED_SYMBOL_LAYER")


def test_zooms_above_16_are_generated(export, tmp_path):
    layer = zoning_layer(path=str(tmp_path / "zoning.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "red"})))
    exporter, result = export(layer, min_zoom=17, max_zoom=18)
    archive = inspect_mbtiles(os.path.join(result, "tiles.mbtiles"))
    assert archive["maxzoom"] == 18 and 18 in archive["tile_counts"]
    assert not exporter.diagnostics.by_code("Q2VT_TILES_ZOOM_MISMATCH")


def test_geometry_generator_uses_layer_crs_for_both_geometry_spellings(plugin):
    from q2vt_plugin.src.core.rules_exporter import RulesExporter  # pylint: disable=import-error

    class Flat:  # minimal stand-in for FlattenedRule
        class layer:  # noqa: N801
            @staticmethod
            def crs():
                from qgis.core import QgsCoordinateReferenceSystem
                return QgsCoordinateReferenceSystem("EPSG:23700")  # EOV

    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt("POINT(2120500 6020500)"))
    ctx = QgsExpressionContext()
    ctx.appendScope(QgsExpressionContextUtils.globalScope())
    ctx.setFeature(feature)
    for spelling in ("@geometry", "$geometry"):
        expr = QgsExpression(RulesExporter._generator_in_layer_crs(
            f"buffer({spelling}, 100, 64)", Flat()))
        assert not expr.hasParserError(), expr.parserErrorString()
        geom = expr.evaluate(ctx)
        assert not expr.hasEvalError(), expr.evalErrorString()
        # 100 EOV metres ≈ 100 / cos(47.5°) ≈ 148 Web Mercator units of radius.
        radius = (geom.boundingBox().width()) / 2
        assert 140 < radius < 155, (spelling, radius)
