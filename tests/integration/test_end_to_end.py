"""End-to-end exports through the real pipeline (processing + GDAL + style)."""

import json
import os

import pytest
from qgis.core import (QgsExpression, QgsExpressionContext, QgsExpressionContextUtils,
                       QgsFeature, QgsFillSymbol, QgsGeometry, QgsLinePatternFillSymbolLayer,
                       QgsPalLayerSettings, QgsProcessingException, QgsProcessingFeedback,
                       QgsLineSymbolLayer, QgsProperty, QgsRectangle,
                       QgsSingleSymbolRenderer, QgsSymbolLayer, QgsTextFormat,
                       QgsVectorLayerSimpleLabeling, Qgis)
from qgis.PyQt.QtGui import QColor

from fidelity.validation import inspect_mbtiles
from q2vt_fixtures import reset_project, zoning_layer

EXTENT = QgsRectangle(2119000, 6019000, 2123000, 6023000)


class UnknownLine(QgsLineSymbolLayer):
    """A symbol layer type the exporter does not know (as from a plugin):
    every built-in QGIS type is converted now."""

    def __init__(self):
        super().__init__()

    def layerType(self):  # noqa: N802
        return "Q2vtTestUnknownLine"

    def startRender(self, context):  # noqa: N802
        pass

    def stopRender(self, context):  # noqa: N802
        pass

    def renderPolyline(self, points, context):  # noqa: N802
        pass

    def properties(self):
        return {}

    def clone(self):
        return UnknownLine()


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

    def run(layer, min_zoom=10, max_zoom=14, keep_project=False, **kwargs):
        if not keep_project:
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
    assert width[0] == "case"  # zero width -> one-pixel hairline, as QGIS
    width = width[3]
    assert width[0] == "*" and width[1][0] == "to-number"
    field = width[1][1][1][1]
    assert field in archive["vector_layers"][outline["source-layer"]]["fields"]

    # Labels: glyphs generated for the exact text-font, including ő/ű.
    label = [l for l in style["layers"] if "text-field" in l.get("layout", {})][0]
    stack = label["layout"]["text-font"][0]
    assert os.path.exists(os.path.join(result, "style", "glyphs", stack, "0-255.pbf"))
    assert os.path.exists(os.path.join(result, "style", "glyphs", stack, "256-511.pbf"))

    # Project untouched.
    assert type(layer.renderer()).__name__ == "QgsSingleSymbolRenderer"
    assert layer.labeling().settings().isExpression

    # The export log survives a crash: every step, flushed, and closed after.
    log = open(os.path.join(result, "export_log.txt"), encoding="utf-8").read()
    assert "Crash tracebacks are written to this file." in log
    assert ". Exporting rules to datasets..." in log
    assert ". Process completed successfully" in log
    assert "native:" in log  # each processing step of the workers


def test_algorithm_runs_on_the_main_thread(plugin):
    """The export changes the project (result layer), which QGIS only allows
    from the main thread: from a background task QGIS can close silently."""
    from qgis.core import QgsProcessingAlgorithm
    from q2vt_plugin.src.processing.algorithms import (  # pylint: disable=import-error
        QGIS2VectorTilesAlgorithm)
    flags = QGIS2VectorTilesAlgorithm().flags()
    assert flags & QgsProcessingAlgorithm.FlagNoThreading


def test_other_processing_temp_outputs_are_kept(plugin):
    """Only the plugin's own old working folders are removed from the
    Processing temp folder: temporary layers of the project live there."""
    from qgis.core import QgsProcessingUtils
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-error
    temp = QgsProcessingUtils.tempFolder()
    other = os.path.join(temp, "other_tool_output")
    stale = os.path.join(temp, "q2styledtiles_stale")
    os.makedirs(other, exist_ok=True)
    os.makedirs(stale, exist_ok=True)
    QGIS2VectorTiles(extent=EXTENT, output_dir=temp, serve=False)
    assert os.path.isdir(other)
    assert not os.path.exists(stale)


def test_strict_mode_fails_before_publication(export, tmp_path):
    layer = zoning_layer(path=str(tmp_path / "zoning.gpkg"))
    symbol = QgsFillSymbol()
    symbol.changeSymbolLayer(0, UnknownLine())  # an unsupported outline
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
    symbol.changeSymbolLayer(0, UnknownLine())  # an unsupported outline
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


def test_zoom_split_datasets_only_fill_their_own_tiles(export, tmp_path):
    import sqlite3
    from fidelity import zoom as zm
    from fidelity.validation import tile_layers
    from q2vt_fixtures import rule_based
    layer = rule_based(zoning_layer(path=str(tmp_path / "zoning.gpkg")), [
        ("mid", "", zm.zoom_to_scale(12.0) * 0.999, zm.zoom_to_scale(14.0) * 1.001, True,
         "255,0,0")])
    exporter, result = export(layer)
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    fill = [l for l in style["layers"] if l["type"] == "fill"][0]
    zooms = set()
    with sqlite3.connect(os.path.join(result, "tiles.mbtiles")) as conn:
        for zoom, data in conn.execute("SELECT zoom_level, tile_data FROM tiles"):
            if fill["source-layer"] in tile_layers(bytes(data)):
                zooms.add(zoom)
    assert zooms == {12, 13}
    assert not exporter.diagnostics.by_code("Q2VT_SOURCE_LAYER_MISSING")


def test_symbols_reaching_into_the_extent_are_kept(export, tmp_path):
    import math
    import sqlite3
    from qgis.core import QgsMarkerSymbol, QgsVectorLayer
    from fidelity.validation import tile_layers
    from q2vt_fixtures import to_geopackage
    layer = QgsVectorLayer("Point?crs=EPSG:3857", "big", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt(f"POINT({EXTENT.xMaximum() + 300} "
                                            f"{EXTENT.center().y()})"))
    layer.dataProvider().addFeature(feature)
    layer = to_geopackage(layer, str(tmp_path / "big.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol.createSimple(
        {"name": "square", "size": "20", "color": "red"})))
    _, result = export(layer)
    half = 20037508.342789244
    with sqlite3.connect(os.path.join(result, "tiles.mbtiles")) as conn:
        rows = conn.execute("SELECT zoom_level, tile_column, tile_data FROM tiles").fetchall()
    assert any(tile_layers(bytes(data)) for _, _, data in rows)
    for zoom, column, _ in rows:
        size = 2 * half / 2 ** zoom
        assert math.floor((EXTENT.xMinimum() + half) / size) <= column
        assert column <= math.floor((EXTENT.xMaximum() + half) / size)


def test_pinned_labels_and_callouts(export, tmp_path):
    from qgis.core import QgsSimpleLineCallout
    layer = zoning_layer(path=str(tmp_path / "zoning.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "white"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "id"
    fmt = QgsTextFormat()
    fmt.setSize(12)
    settings.setFormat(fmt)
    props = settings.dataDefinedProperties()
    props.setProperty(QgsPalLayerSettings.Property.PositionX, QgsProperty.fromExpression(
        "if(\"id\" = 'A', x(centroid($geometry)) + 200, NULL)"))
    props.setProperty(QgsPalLayerSettings.Property.PositionY, QgsProperty.fromExpression(
        "if(\"id\" = 'A', y(centroid($geometry)) + 200, NULL)"))
    settings.setDataDefinedProperties(props)
    callout = QgsSimpleLineCallout()
    callout.setEnabled(True)
    settings.setCallout(callout)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    exporter, result = export(layer)
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    labels = [l for l in style["layers"] if "text-field" in l.get("layout", {})]
    pinned = [l for l in labels if l["layout"]["text-anchor"] == "bottom-left"]
    free = [l for l in labels if l not in pinned]
    assert len(pinned) == 1 and free
    assert pinned[0]["layout"]["text-allow-overlap"] is True
    lines = [l for l in style["layers"] if l["type"] == "line"]
    assert lines and style["layers"].index(lines[-1]) > max(
        style["layers"].index(l) for l in style["layers"] if l["type"] == "fill")
    assert exporter.diagnostics.by_code("Q2VT_CALLOUT_APPROX")
    archive = inspect_mbtiles(os.path.join(result, "tiles.mbtiles"),
                              {l["source-layer"] for l in pinned + lines})
    assert pinned[0]["source-layer"] in archive["vector_layers"]
    assert lines[-1]["source-layer"] in archive["vector_layers"]


def test_pinned_label_geometry_is_the_data_defined_point(plugin):
    from q2vt_plugin.src.core.rules_exporter import callout_leader_expression
    from qgis.core import QgsExpression, QgsExpressionContext, QgsFeature, QgsGeometry
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt("POLYGON((0 0, 10 0, 10 10, 0 10, 0 0))"))
    context = QgsExpressionContext()
    context.setFeature(feature)
    expr = QgsExpression(callout_leader_expression("make_point(30, 5)", 2, 3))
    assert expr.evaluate(context).asWkt() == "LineString (5 5, 30 5)"
    expr = QgsExpression(callout_leader_expression("make_point(30, 5)", 2, 1))
    assert expr.evaluate(context).asWkt() == "LineString (10 5, 30 5)"


def test_workers_never_touch_the_live_project(export, tmp_path, monkeypatch):
    """QGIS 3.44 / Windows closed with heap corruption (0xc0000374): every
    worker called QgsProject.instance().createExpressionContext(), which
    rebuilds a cached project scope without a lock. Workers now copy scopes
    taken on the main thread; project variables still reach expressions."""
    import threading
    from qgis.core import QgsExpressionContextUtils, QgsProject
    import q2vt_plugin.src.core.rules_exporter as rules_exporter  # pylint: disable=import-error
    calls = []

    class GuardedProject:
        @staticmethod
        def instance():
            if threading.current_thread() is not threading.main_thread():
                calls.append(threading.current_thread().name)
            return QgsProject.instance()
    monkeypatch.setattr(rules_exporter, "QgsProject", GuardedProject)

    layer = zoning_layer(path=str(tmp_path / "vars.gpkg"))
    settings = QgsPalLayerSettings()
    settings.fieldName = "@q2vt_test_prefix || \"zone\""
    settings.isExpression = True
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    reset_project(layer)
    QgsExpressionContextUtils.setProjectVariable(QgsProject.instance(), "q2vt_test_prefix", "Z-")
    try:
        exporter, result = export(layer, keep_project=True, parallel=True)
    finally:
        QgsExpressionContextUtils.removeProjectVariable(QgsProject.instance(), "q2vt_test_prefix")
    assert result and not calls, calls
    archive = inspect_mbtiles(os.path.join(result, "tiles.mbtiles"))
    labels = [name for name in archive["vector_layers"] if "t01" in name]
    assert labels
    texts = set()
    from osgeo import ogr  # pylint: disable=import-outside-toplevel
    dataset = ogr.Open(os.path.join(result, "tiles.mbtiles"))
    for index in range(dataset.GetLayerCount()):
        mvt_layer = dataset.GetLayer(index)
        if "t01" not in mvt_layer.GetName():
            continue
        for feature in mvt_layer:
            texts.add(feature.GetField("q2vt_label"))
    texts.discard(None)  # the square without a zone: 'Z-' || NULL is NULL, as in QGIS
    assert texts == {"Z-K1", "Z-K2", "Z-Lk"}, texts


def test_viewer_starts_on_the_exported_area(export, tmp_path):
    """With minimum zoom 0 the viewer opened on the whole earth."""
    layer = zoning_layer(path=str(tmp_path / "view.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "red"})))
    exporter, result = export(layer, min_zoom=0, max_zoom=14)
    viewer = open(os.path.join(result, "utils", "viewer", "viewer.html"), encoding="utf-8").read()
    assert "_Q2VT_" not in viewer
    assert "map.fitBounds([[" in viewer and "maxZoom: 14" in viewer


def test_export_runs_processing_on_the_main_thread_by_default(export, tmp_path, monkeypatch):
    """Parallel worker threads still crashed QGIS 3.44 / Windows with heap
    corruption after the project scope fix: by default every processing
    algorithm now runs on the main thread, one after another."""
    import threading
    import q2vt_plugin.src.core.rules_exporter as rules_exporter  # pylint: disable=import-error
    threads = set()
    original = rules_exporter.run_processing

    def recording(*args, **kwargs):
        threads.add(threading.current_thread().name)
        return original(*args, **kwargs)
    monkeypatch.setattr(rules_exporter, "run_processing", recording)
    layer = _hatched_labelled_layer(str(tmp_path / "serial.gpkg"))
    exporter, result = export(layer)
    assert result and threads == {threading.main_thread().name}, threads
    from q2vt_plugin.src.processing.algorithms import (  # pylint: disable=import-error
        QGIS2VectorTilesAlgorithm)
    algorithm = QGIS2VectorTilesAlgorithm()
    algorithm.initAlgorithm()
    assert algorithm.parameterDefinition("PARALLEL").defaultValue() is False


def test_polygon_labels_on_the_visible_part_ship_their_polygons(export, tmp_path):
    """QGIS's default "Centroid: visible polygon": the viewer places the label
    on the visible part, so the export ships the polygons (``_vp``) and marks
    the style layer; "whole polygon" labels stay static centroids."""
    layer = zoning_layer(path=str(tmp_path / "vp.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "red"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "zone"
    settings.placement = Qgis.LabelPlacement.OverPoint
    settings.centroidWhole = False
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    exporter, result = export(layer)
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    labels = [l for l in style["layers"] if "text-field" in l.get("layout", {})]
    polygons = labels[0]["metadata"]["q2vt:visible-polygons"]
    assert polygons.endswith("_vp") and labels[0]["source-layer"] + "_vp" == polygons
    archive = inspect_mbtiles(os.path.join(result, "tiles.mbtiles"))
    assert polygons in archive["vector_layers"]
    assert "q2vt_label" in archive["vector_layers"][polygons]["fields"]

    layer = zoning_layer(path=str(tmp_path / "vp_whole.gpkg"))  # the project owned the first
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "red"})))
    settings.centroidWhole = True
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    exporter, result = export(layer)
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    labels = [l for l in style["layers"] if "text-field" in l.get("layout", {})]
    assert not labels[0].get("metadata", {}).get("q2vt:visible-polygons")

    # Free (angled) with "whole polygon" (a user's QML): QGIS places Free
    # labels on the polygon clipped to the extent whatever the centroid
    # setting, angled where they do not fit flat: the viewer places them
    # (they used to stay static and horizontal).
    layer = zoning_layer(path=str(tmp_path / "vp_free.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "red"})))
    settings.placement = Qgis.LabelPlacement.Free
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    exporter, result = export(layer)
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    labels = [l for l in style["layers"] if "text-field" in l.get("layout", {})]
    metadata = labels[0].get("metadata", {})
    assert metadata.get("q2vt:visible-polygons", "").endswith("_vp")
    assert metadata["q2vt:label-orient"] == "free" and metadata["q2vt:label-anchor"] == "pole"


def test_viewer_placed_labels_get_the_advance_of_each_character(export, tmp_path):
    """Whether a label fits in its polygon depends on its length: the style
    carries the label font's own advance per character (Qt's metrics, as
    QGIS measures the label), not only a mean width."""
    from qgis.PyQt.QtGui import QFontMetricsF
    layer = zoning_layer(path=str(tmp_path / "metrics.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "red"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "zone"
    settings.placement = Qgis.LabelPlacement.Free
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    exporter, result = export(layer)
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    label = [l for l in style["layers"] if "text-field" in l.get("layout", {})][0]
    font = label["metadata"]["q2vt:font"]
    assert font == label["layout"]["text-font"][0]
    entry = style["metadata"]["q2vt:font-metrics"][font]
    advance = dict(zip(entry["chars"], entry["advances"]))
    text = "t_felirat 1203/4 ő"
    qt_font = settings.format().font()
    qt_font.setPixelSize(1000)
    expected = QFontMetricsF(qt_font).horizontalAdvance(text)
    assert sum(advance[c] for c in text) == pytest.approx(expected, rel=0.02)
    assert advance["i"] < advance["e"] < advance["W"]
    assert 0.9 < entry["height"] < 1.5


def test_around_point_polygon_labels_tell_the_viewer_their_distance(export, tmp_path):
    """QGIS "Around point" on polygons: the label goes beside its point at
    the label distance (the viewer places it, see visible_labels.mjs), not
    on it; other placements carry no such hint."""
    def labels_for(placement, name):
        layer = zoning_layer(path=str(tmp_path / f"{name}.gpkg"))
        layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "red"})))
        settings = QgsPalLayerSettings()
        settings.fieldName = "zone"
        settings.placement = placement
        settings.dist = 3
        settings.distUnits = Qgis.RenderUnit.Millimeters
        settings.centroidWhole = False
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        _, result = export(layer)
        style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
        return [l for l in style["layers"] if "text-field" in l.get("layout", {})]

    around = labels_for(Qgis.LabelPlacement.AroundPoint, "around")[0]["metadata"]["q2vt:label-around"]
    assert around["px"] == pytest.approx(3 * 96 / 25.4, rel=0.01) and "zoom" not in around
    assert around["anchors"][0] == "bottom-left"  # first try: above right of the point, as QGIS
    over = labels_for(Qgis.LabelPlacement.OverPoint, "over")[0]["metadata"]
    assert "q2vt:visible-polygons" in over and "q2vt:label-around" not in over


def test_detail_below_a_metre_survives_at_the_max_zoom(export, tmp_path):
    """Geometry is simplified only within the tiles' own rounding at the max
    zoom. A fixed 1 m tolerance moved shared boundaries of two layers (a zone
    boundary on a parcel edge) apart by up to a metre, pixels when zoomed in."""
    from osgeo import gdal  # pylint: disable=import-outside-toplevel
    from q2vt_fixtures import to_geopackage  # pylint: disable=import-outside-toplevel
    from qgis.core import QgsVectorLayer  # pylint: disable=import-outside-toplevel
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "parcels", "memory")
    feature = QgsFeature(layer.fields())
    # The south edge bends 0.4 m at its middle (5 tile units at zoom 17).
    feature.setGeometry(QgsGeometry.fromWkt(
        "POLYGON((2120000 6020000, 2120050 6020000.4, 2120100 6020000, "
        "2120100 6020100, 2120000 6020100, 2120000 6020000))"))
    layer.dataProvider().addFeatures([feature])
    layer = to_geopackage(layer, str(tmp_path / "bend.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "red"})))
    exporter, result = export(layer, min_zoom=17, max_zoom=17)
    tiles = gdal.OpenEx(os.path.join(result, "tiles.mbtiles"), gdal.OF_VECTOR,
                        open_options=["ZOOM_LEVEL=17", "CLIP=NO"])
    south = []
    for i in range(tiles.GetLayerCount()):
        for f in tiles.GetLayer(i):
            ring = f.GetGeometryRef()
            while ring.GetGeometryCount():
                ring = ring.GetGeometryRef(0)
            south += [ring.GetPoint(k)[1] for k in range(ring.GetPointCount())
                      if 2120001 < ring.GetPoint(k)[0] < 2120099 and ring.GetPoint(k)[1] < 6020050]
    assert south and max(south) - 6020000 == pytest.approx(0.4, abs=0.08)


def test_line_labels_once_per_line_ship_their_lines(export, tmp_path):
    """A line label drawn once per line (no repeat distance) is exported at the
    line's middle, and its lines (``_vl``) ship with the label's fields: the
    viewer moves the label to the middle of the visible part of the line."""
    from q2vt_fixtures import to_geopackage  # pylint: disable=import-outside-toplevel
    from qgis.core import QgsField, QgsLineSymbol, QgsVectorLayer  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtCore import QVariant  # pylint: disable=import-outside-toplevel
    layer = QgsVectorLayer("LineString?crs=EPSG:3857", "contours", "memory")
    layer.dataProvider().addAttributes([QgsField("height", QVariant.Double)])
    layer.updateFields()
    feature = QgsFeature(layer.fields())
    feature.setAttributes([187.5])
    feature.setGeometry(QgsGeometry.fromWkt(
        "LineString (2119500 6019500, 2121000 6021000, 2122500 6020000)"))
    layer.dataProvider().addFeatures([feature])
    layer = to_geopackage(layer, str(tmp_path / "contours.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({"color": "orange"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "height"
    settings.placement = Qgis.LabelPlacement.Line
    settings.repeatDistance = 0
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    exporter, result = export(layer)
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    labels = [l for l in style["layers"] if "text-field" in l.get("layout", {})]
    metadata = labels[0]["metadata"]
    assert metadata["q2vt:visible-kind"] == "line"
    lines = metadata["q2vt:visible-polygons"]
    assert lines == labels[0]["source-layer"] + "_vl"
    assert labels[0]["layout"]["symbol-placement"] == "point"   # the midpoint, rotated
    assert "labelrotation" in json.dumps(labels[0]["layout"]["text-rotate"])
    archive = inspect_mbtiles(os.path.join(result, "tiles.mbtiles"))
    assert lines in archive["vector_layers"]
    assert "q2vt_label" in archive["vector_layers"][lines]["fields"]


def test_perimeter_labels_follow_the_polygon_outline(export, tmp_path):
    """Polygon labels "Using perimeter (curved)" follow the outline. They
    were exported at the polygon's centroid, a point MapLibre's line
    placement has no line to put the label on: no label was drawn. Once per
    feature, the label sits at the middle of the outline, along it (and the
    viewer gets the outlines); repeated, the outlines are exported as lines."""
    from osgeo import gdal, ogr  # pylint: disable=import-outside-toplevel

    def labels_for(repeat, name):
        layer = zoning_layer(path=str(tmp_path / f"{name}.gpkg"))
        layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "red"})))
        settings = QgsPalLayerSettings()
        settings.fieldName = "zone"
        settings.placement = Qgis.LabelPlacement.PerimeterCurved
        settings.repeatDistance = repeat
        layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
        layer.setLabelsEnabled(True)
        _, result = export(layer)
        style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
        tiles = gdal.OpenEx(os.path.join(result, "tiles.mbtiles"), gdal.OF_VECTOR,
                            open_options=["ZOOM_LEVEL=14"])
        kinds = {}
        for i in range(tiles.GetLayerCount()):
            for feature in tiles.GetLayer(i):
                kinds.setdefault(tiles.GetLayer(i).GetName(), set()).add(
                    ogr.GT_Flatten(feature.GetGeometryRef().GetGeometryType()))
        return [l for l in style["layers"] if "text-field" in l.get("layout", {})], kinds

    once, kinds = labels_for(0, "once")
    assert once and once[0]["layout"]["symbol-placement"] == "point"
    assert "labelrotation" in json.dumps(once[0]["layout"]["text-rotate"])
    assert once[0]["metadata"]["q2vt:visible-kind"] == "line"
    assert kinds[once[0]["source-layer"]] <= {ogr.wkbPoint, ogr.wkbMultiPoint}
    assert kinds[once[0]["metadata"]["q2vt:visible-polygons"]] <= {ogr.wkbLineString,
                                                                     ogr.wkbMultiLineString}
    repeated, kinds = labels_for(20, "repeated")
    assert repeated and repeated[0]["layout"]["symbol-placement"] == "line"
    assert kinds[repeated[0]["source-layer"]] <= {ogr.wkbLineString, ogr.wkbMultiLineString}


def _glyph_advances(path):
    """{code point: advance} of a glyph PBF (24 px em)."""
    from publishing import mvt  # pylint: disable=import-outside-toplevel
    advances = {}
    for number, _, stack in mvt._fields(open(path, "rb").read()):  # pylint: disable=protected-access
        if number != 1:
            continue
        for field, _, glyph in mvt._fields(stack):  # pylint: disable=protected-access
            if field == 3:
                values = {key: value for key, _, value in mvt._fields(glyph)}  # pylint: disable=protected-access
                advances[values[1]] = values.get(7, 0)
    return advances


def test_upper_case_labels_have_their_glyphs(export, tmp_path):
    """A label shown in capitals (QGIS "All uppercase", MapLibre
    text-transform) of text stored in lower case: the glyphs of the capitals
    are generated. Only the stored letters were, and MapLibre silently left
    out every capital it had no glyph for."""
    layer = zoning_layer(path=str(tmp_path / "caps.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "red"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "lower(zone) || ' ry'"
    settings.isExpression = True
    text_format = settings.format()
    text_format.setCapitalization(Qgis.Capitalization.AllUppercase)
    settings.setFormat(text_format)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    _, result = export(layer)
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    layout = [l for l in style["layers"] if "text-field" in l.get("layout", {})][0]["layout"]
    assert layout["text-transform"] == "uppercase"
    glyphs = _glyph_advances(os.path.join(result, "style", "glyphs", layout["text-font"][0],
                                           "0-255.pbf"))
    assert {ord(c) for c in "KLRY"} <= set(glyphs)


def test_curved_repeated_line_labels_are_placed_per_zoom(export, tmp_path):
    """Swellendam rivers: a curved label repeated along a wiggly river (a
    vertex every 60 m turning 30 degrees one way, then the other) is laid
    out at export time per zoom, as QGIS lays it out: each label is a short
    line inside one tile that MapLibre centres the label on ("line-center")
    and accepts (its angle check, with the label's own glyph advances), one
    per repeat part. MapLibre's line placement on the river itself found no
    anchor at these zooms (the wiggles break its angle check)."""
    import math  # pylint: disable=import-outside-toplevel
    import sqlite3  # pylint: disable=import-outside-toplevel
    from publishing import mvt  # pylint: disable=import-outside-toplevel
    from q2vt_fixtures import to_geopackage  # pylint: disable=import-outside-toplevel
    from qgis.core import QgsField, QgsLineSymbol, QgsVectorLayer  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtCore import QVariant  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtGui import QFont  # pylint: disable=import-outside-toplevel
    layer = QgsVectorLayer("LineString?crs=EPSG:3857", "rivers", "memory")
    layer.dataProvider().addAttributes([QgsField("name", QVariant.String)])
    layer.updateFields()
    points = []  # a U: 3.4 km east, 1.5 km north, 3.4 km west
    for (x0, y0), (x1, y1) in (((2119300, 6020000), (2122700, 6020000)),
                               ((2122700, 6020000), (2122700, 6021500)),
                               ((2122700, 6021500), (2119300, 6021500))):
        direction = math.atan2(y1 - y0, x1 - x0)
        x, y = x0, y0
        for i in range(int(math.hypot(x1 - x0, y1 - y0) // 58)):
            points.append(f"{x} {y}")
            heading = direction + math.radians(15 if i % 2 else -15)
            x, y = x + 60 * math.cos(heading), y + 60 * math.sin(heading)
    points.append(f"{x} {y}")
    feature = QgsFeature(layer.fields())
    feature.setAttributes(["Koornlands"])
    feature.setGeometry(QgsGeometry.fromWkt(f"LineString ({', '.join(points)})"))
    layer.dataProvider().addFeatures([feature])
    layer = to_geopackage(layer, str(tmp_path / "rivers.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({"color": "blue"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "name"
    settings.placement = Qgis.LabelPlacement.Curved
    settings.repeatDistance = 70
    settings.repeatDistanceUnit = Qgis.RenderUnit.Millimeters
    fmt = QgsTextFormat()
    fmt.setSize(9)
    font = fmt.font()
    font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
    fmt.setFont(font)
    settings.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    _, result = export(layer, min_zoom=11, max_zoom=13)
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    labels = [l for l in style["layers"] if "text-field" in l.get("layout", {})]
    assert sorted(l["minzoom"] for l in labels) == [11, 12, 13]   # one layer per zoom
    assert {l["layout"]["symbol-placement"] for l in labels} == {"line-center"}
    assert labels[0]["layout"]["text-max-angle"] == 25

    from q2vt_plugin.src.core import label_lines  # pylint: disable=import-error,import-outside-toplevel
    layout = labels[0]["layout"]
    glyphs = _glyph_advances(os.path.join(result, "style", "glyphs", layout["text-font"][0],
                                          "0-255.pbf"))
    size, spacing = layout["text-size"], layout["text-letter-spacing"]
    label_px = sum(glyphs[ord(c)] for c in "Koornlands") * size / 24 + spacing * size * 9
    placed = {}
    with sqlite3.connect(os.path.join(result, "tiles.mbtiles")) as conn:
        for label in labels:
            zoom = label["minzoom"]
            for (data,) in conn.execute("SELECT tile_data FROM tiles WHERE zoom_level = ?", (zoom,)):
                tile = mvt.decode(data, geometry=True).get(label["source-layer"])
                for feature in (tile or {}).get("features", []):
                    for line in mvt.lines(feature["geometry"]):
                        scale = 8192 / tile["extent"]   # MapLibre's tile units
                        line = [(px * scale, py * scale) for px, py in line]
                        anchor = label_lines.center_anchor(line, label_px * 16, 0, 0)
                        if not (0 <= anchor[0] < 8192 and 0 <= anchor[1] < 8192):
                            continue  # a label of the next tile, in this one's buffer
                        assert all(0 < px < 8192 and 0 < py < 8192 for px, py in line)
                        assert label_lines.center_anchor(  # MapLibre draws the label
                            line, label_px * 16, 0.6 * size * 16, math.radians(25)), line
                        turns = label_lines.char_turns(line)  # QGIS's limit
                        assert max(abs(t) for t in turns) <= math.radians(25.5), turns
                        placed[zoom] = placed.get(zoom, 0) + 1
    # 8.3 km of river, cut into parts of the repeat distance (70 mm as at
    # zoom + 0.5: 187 px of the tile zoom): 224 px at zoom 11, 447 at 12,
    # 895 at 13.
    assert placed == {11: 1, 12: 2, 13: 4}


def test_labels_avoid_each_other_and_overlap_only_if_required(export, tmp_path):
    """Every label avoids the others in MapLibre; those QGIS may overlap are
    marked for the viewer's fallback, and horizontal polygon labels can move
    aside."""
    layer = zoning_layer(path=str(tmp_path / "ov.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "red"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "zone"
    settings.placement = Qgis.LabelPlacement.Horizontal
    placement = settings.placementSettings()
    placement.setOverlapHandling(Qgis.LabelOverlapHandling.AllowOverlapIfRequired)
    settings.setPlacementSettings(placement)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    _, result = export(layer)
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    [label] = [l for l in style["layers"] if "text-field" in l.get("layout", {})]
    assert label["layout"]["text-allow-overlap"] is False
    assert label["metadata"]["q2vt:overlap"] == "if-required"
    assert label["layout"]["text-variable-anchor"][0] == "center"
    assert label["layout"]["text-radial-offset"] == 1


def test_properties_on_missing_fields_are_ignored_as_in_qgis(export, tmp_path):
    """Szabályozás övezetkódok: a colour rule on a field the layer does not
    have is ignored by QGIS (static colour); the export evaluated it."""
    layer = zoning_layer(path=str(tmp_path / "mf.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "red"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "zone"
    fmt = QgsTextFormat()
    fmt.setColor(QColor("#232323"))
    settings.setFormat(fmt)
    settings.dataDefinedProperties().setProperty(
        QgsPalLayerSettings.Property.Color, QgsProperty.fromExpression(
            "-- note\ncase when beep_szant=1 then color_rgba(255,0,0,255) else color_rgba(0,0,255,255) end"))
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    exporter, result = export(layer)
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    label = [l for l in style["layers"] if "text-field" in l.get("layout", {})][0]
    assert label["paint"]["text-color"] == "rgba(35, 35, 35, 1.0)"
    assert exporter.diagnostics.by_code("Q2VT_DDP_MISSING_FIELD")
    # Only the export's copy is changed, never the project's labels.
    assert not exporter.diagnostics.by_code("Q2VT_PROJECT_MUTATED")


def test_layer_opacity_fades_the_symbols_not_the_labels(export, tmp_path):
    """QGIS's layer opacity (Layer Rendering) was never exported: a layer at
    50 % came out fully opaque on the web. Its symbols now get it; its labels
    do not, as in QGIS."""
    layer = zoning_layer(path=str(tmp_path / "op.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
        {"color": "255,0,0,200", "outline_color": "black", "outline_width": "0.5"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "zone"
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    layer.setOpacity(0.5)
    _, result = export(layer)
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    fills = [l for l in style["layers"] if l["type"] == "fill"]
    lines = [l for l in style["layers"] if l["type"] == "line"]
    labels = [l for l in style["layers"] if "text-field" in l.get("layout", {})]
    assert fills and lines and labels
    assert all(l["paint"]["fill-opacity"] == pytest.approx(0.5) for l in fills)
    assert all(l["paint"]["line-opacity"] == pytest.approx(0.5) for l in lines)
    assert all(l.get("paint", {}).get("text-opacity", 1) == 1 for l in labels)


def test_source_without_prj_uses_the_project_crs(export, tmp_path):
    """Épületek: a shapefile without .prj (CRS set in the project) reopened
    without a CRS; the extent filter then dropped every feature."""
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransformContext,
                           QgsVectorFileWriter, QgsVectorLayer)
    source = zoning_layer()
    path = str(tmp_path / "noprj.shp")
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "ESRI Shapefile"
    QgsVectorFileWriter.writeAsVectorFormatV3(source, path, QgsCoordinateTransformContext(), options)
    os.remove(str(tmp_path / "noprj.prj"))
    layer = QgsVectorLayer(path, "noprj", "ogr")
    layer.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "red"})))
    exporter, result = export(layer)
    assert result
    archive = inspect_mbtiles(os.path.join(result, "tiles.mbtiles"))
    assert archive["vector_layers"], archive
