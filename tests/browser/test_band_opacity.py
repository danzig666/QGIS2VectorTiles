"""Translucent gradient / shapeburst bands in the browser, with the
Publish defaults (tiles 0-16, the map overzoomed past 16): QGIS shows one
band colour per pixel. Nested translucent bands stacked into an opaque
tint over the inside QGIS leaves see-through, at every zoom. Also the
markers QGIS draws with their parent symbol's opacity multiplied in."""

import importlib.util
import json
import os
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image
from qgis.core import (Qgis, QgsCentroidFillSymbolLayer, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsFeature, QgsField, QgsFillSymbol, QgsGeometry,
                       QgsLineSymbol, QgsMarkerLineSymbolLayer, QgsMarkerSymbol, QgsProcessingFeedback,
                       QgsProject, QgsProperty, QgsRectangle, QgsShapeburstFillSymbolLayer,
                       QgsSimpleMarkerSymbolLayer, QgsSingleSymbolRenderer, QgsSymbol, QgsVectorLayer)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor

from q2vt_fixtures import reset_project, to_geopackage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_browser_smoke import HERE, _serve  # noqa: E402 pylint: disable=wrong-import-position

ROOT = os.path.dirname(os.path.dirname(HERE))
CENTER = (2120500.0, 6020500.0)
SIZE = 320
ZOOMS = (13, 14, 16, 18)
_spec = importlib.util.spec_from_file_location(
    "q2vt_gallery", os.path.join(ROOT, "tools", "gallery", "build_gallery.py"))
gallery = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gallery)

pytestmark = pytest.mark.browser


def _square(path, half=350.0):
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "square", "memory")
    feature = QgsFeature()
    x, y = CENTER
    feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(x - half, y - half, x + half, y + half)))
    layer.dataProvider().addFeature(feature)
    return to_geopackage(layer, path)


def _views(tmp_path, layer, zooms=ZOOMS):
    """{zoom: (QGIS pixels, browser pixels)} of a 0-16 export."""
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-error
    reset_project(layer)
    x, y = CENTER
    out = tmp_path / "out"
    out.mkdir()
    export_dir = QGIS2VectorTiles(min_zoom=0, max_zoom=16, extent=QgsRectangle(x - 600, y - 600, x + 600, y + 600),
                                  output_dir=str(out), feedback=QgsProcessingFeedback(), serve=False,
                                  background_type=2).convert_project_to_vector_tiles()
    to_wgs = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:3857"),
                                    QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
    point = to_wgs.transform(*CENTER)
    views = tmp_path / "views.json"
    views.write_text(json.dumps([{"id": f"z{z}", "lon": point.x(), "lat": point.y(), "zoom": z,
                                  "width": SIZE, "height": SIZE} for z in zooms]))
    for zoom in zooms:
        gallery.qgis_render(layer, CENTER, zoom, SIZE, str(tmp_path / f"z{zoom}_qgis.png"))
    server = _serve(export_dir)
    try:
        run = subprocess.run(["node", os.path.join(HERE, "gallery_capture.mjs"), export_dir,
                              str(server.port), str(views), str(tmp_path)],
                             capture_output=True, text=True, cwd=HERE, timeout=180)
        assert run.returncode == 0, run.stderr
    finally:
        server.kill()

    def pixels(name):
        return np.asarray(Image.open(tmp_path / name).convert("RGB")).astype(int)
    return {z: (pixels(f"z{z}_qgis.png"), pixels(f"z{z}_browser.png")) for z in zooms}


@pytest.mark.parametrize("case", ["fade_to_transparent", "half_opacity"])
def test_translucent_shapeburst_matches_qgis_at_every_zoom(tmp_path, case):
    fill = QgsShapeburstFillSymbolLayer(QColor(0, 90, 200), QColor(255, 255, 255, 0)
                                        if case == "fade_to_transparent" else QColor(255, 255, 0))
    fill.setUseWholeShape(False)
    fill.setMaxDistance(3)
    fill.setDistanceUnit(Qgis.RenderUnit.Millimeters)
    symbol = QgsFillSymbol([fill])
    if case == "half_opacity":
        symbol.setOpacity(0.5)
    layer = _square(str(tmp_path / "square.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    for zoom, (qgis, browser) in _views(tmp_path, layer).items():
        middle = SIZE // 2
        # Beyond the distance: see-through (or 50 % yellow) in both.
        centre = np.abs(qgis[middle, middle] - browser[middle, middle]).max()
        assert centre <= 6, (zoom, qgis[middle, middle], browser[middle, middle])
        # The glow along the edge: its colours, pixel for pixel.
        diff = np.abs(qgis - browser).max(axis=2)
        assert diff.mean() < 4, (zoom, diff.mean())


def _features(kind, wkts, path):
    """A layer of ``wkts`` (around CENTER) with a field v = 1, 2, ..."""
    layer = QgsVectorLayer(f"{kind}?crs=EPSG:3857", "features", "memory")
    layer.dataProvider().addAttributes([QgsField("v", QVariant.Int)])
    layer.updateFields()
    features = []
    for index, wkt in enumerate(wkts):
        feature = QgsFeature(layer.fields())
        geometry = QgsGeometry.fromWkt(wkt)
        geometry.translate(*CENTER)
        feature.setGeometry(geometry)
        feature.setAttributes([index + 1])
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    return to_geopackage(layer, path)


def _marker_core(pixels):
    """The red markers' fully covered pixels: their green level (255 for
    none), 0 for an opaque red, 178 for red at 30 % over white."""
    red = pixels[..., 0] - pixels[..., 1] > 20
    return int(np.percentile(pixels[..., 1][red], 5)) if red.sum() > 20 else 255


@pytest.mark.parametrize("case", ["marker_line_field_opacity", "centroid_fill"])
def test_markers_take_the_parent_opacity(tmp_path, case):
    """Markers of a marker line whose opacity is read from a field (30 % and
    60 %), and a centroid fill's marker under a 30 % fill: QGIS multiplies
    the parent's opacity in. They were drawn opaque in the browser."""
    marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Square, 3 if case != "centroid_fill" else 8)
    marker.setColor(QColor("red"))
    marker.setStrokeStyle(0)
    if case == "centroid_fill":
        centroid = QgsCentroidFillSymbolLayer()
        centroid.setSubSymbol(QgsMarkerSymbol([marker]))
        symbol = QgsFillSymbol([centroid])
        symbol.setOpacity(0.3)
        layer = _features("Polygon", ["POLYGON((-150 -150, 150 -150, 150 150, -150 150, -150 -150))"],
                          str(tmp_path / "features.gpkg"))
        halves = [slice(None)]
    else:
        line = QgsMarkerLineSymbolLayer(True, 20)
        line.setIntervalUnit(Qgis.RenderUnit.MapUnits)
        line.setSubSymbol(QgsMarkerSymbol([marker]))
        symbol = QgsLineSymbol([line])
        key = getattr(QgsSymbol.Property, "Opacity", getattr(QgsSymbol.Property, "PropertyOpacity", None))
        symbol.setDataDefinedProperty(key, QgsProperty.fromExpression('"v" * 30'))
        layer = _features("LineString", ["LINESTRING(-150 50, 150 50)", "LINESTRING(-150 -50, 150 -50)"],
                          str(tmp_path / "features.gpkg"))
        halves = [slice(0, SIZE // 2), slice(SIZE // 2, SIZE)]  # v = 1 above, v = 2 below
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    for zoom, (qgis, browser) in _views(tmp_path, layer, (15, 17)).items():
        for rows in halves:
            expected, got = _marker_core(qgis[rows]), _marker_core(browser[rows])
            assert expected < 230 and abs(expected - got) <= 12, (zoom, expected, got)
