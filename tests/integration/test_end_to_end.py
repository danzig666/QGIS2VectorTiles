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
