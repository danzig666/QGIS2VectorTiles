"""Translucent gradient / shapeburst bands in the browser, with the
Publish defaults (tiles 0-16, the map overzoomed past 16): QGIS shows one
band colour per pixel. Nested translucent bands stacked into an opaque
tint over the inside QGIS leaves see-through, at every zoom."""

import importlib.util
import json
import os
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image
from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature,
                       QgsFillSymbol, QgsGeometry, QgsProcessingFeedback, QgsProject, QgsRectangle,
                       QgsShapeburstFillSymbolLayer, QgsSingleSymbolRenderer, QgsVectorLayer)
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


def _views(tmp_path, layer):
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
                                  "width": SIZE, "height": SIZE} for z in ZOOMS]))
    for zoom in ZOOMS:
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
    return {z: (pixels(f"z{z}_qgis.png"), pixels(f"z{z}_browser.png")) for z in ZOOMS}


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
