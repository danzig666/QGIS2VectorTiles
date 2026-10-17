"""Publish defaults (tiles 0-16) seen deep in overzoom: the project
background is drawn up to the deepest browser zoom, and the labels the
viewer places stay on their computed spot (no grid rounding of the label
point source)."""

import json
import math
import os
import subprocess

import pytest
from qgis.core import (Qgis, QgsFeature, QgsField, QgsFillSymbol, QgsGeometry, QgsLabelLineSettings,
                       QgsLineSymbol, QgsPalLayerSettings, QgsPointXY, QgsRectangle,
                       QgsSingleSymbolRenderer, QgsTextFormat, QgsVectorLayer,
                       QgsVectorLayerSimpleLabeling)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor, QFont

from publishing.controller import export_local
from publishing.models import LayerConfig, PublicationProfile
from publishing.preview_server import PreviewServer

HERE = os.path.dirname(os.path.abspath(__file__))
EXTENT = QgsRectangle(2119000, 6019000, 2123000, 6023000)
R = 6378137.0
# A diagonal line off the tile grid (label in the middle of a segment).
X0, Y0, ANGLE, LENGTH = 2120000.3, 6020000.7, math.radians(33.0), 400.0
SQUARE = QgsRectangle(2121000, 6021000, 2121500, 6021500)
EMPTY = (2122500, 6019500)  # neither the square nor the line


def _lnglat(x, y):
    return [math.degrees(x / R), math.degrees(2 * math.atan(math.exp(y / R)) - math.pi / 2)]


def _layers():
    square = QgsVectorLayer("Polygon?crs=EPSG:3857", "Square", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromRect(SQUARE))
    square.dataProvider().addFeatures([feature])
    square.setRenderer(QgsSingleSymbolRenderer(
        QgsFillSymbol.createSimple({"color": "0,0,255,255", "outline_style": "no"})))
    line = QgsVectorLayer("LineString?crs=EPSG:3857", "Road", "memory")
    line.dataProvider().addAttributes([QgsField("name", QVariant.String)])
    line.updateFields()
    dx, dy = LENGTH / 2 * math.cos(ANGLE), LENGTH / 2 * math.sin(ANGLE)
    feature = QgsFeature(line.fields())
    feature.setGeometry(QgsGeometry.fromPolylineXY([QgsPointXY(X0 - dx, Y0 - dy), QgsPointXY(X0 + dx, Y0 + dy)]))
    feature.setAttributes(["HHHHHHHH"])
    line.dataProvider().addFeatures([feature])
    line.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({"color": "0,0,0,255", "width": "0.3"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "name"
    settings.placement = Qgis.LabelPlacement.Line
    line_settings = settings.lineSettings()
    line_settings.setPlacementFlags(Qgis.LabelLinePlacementFlag.OnLine)
    settings.setLineSettings(line_settings)
    fmt = QgsTextFormat()
    fmt.setFont(QFont("DejaVu Sans"))
    fmt.setSize(10)
    fmt.setColor(QColor(255, 0, 0))
    settings.setFormat(fmt)
    line.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    line.setLabelsEnabled(True)
    return square, line


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    from q2vt_fixtures import reset_project
    base = tmp_path_factory.mktemp("overzoom")
    square, line = _layers()
    project = reset_project(square, line)
    project.setBackgroundColor(QColor(255, 255, 0))
    profile = PublicationProfile(title="Overzoom", slug="overzoom", locale="en")
    profile.view.min_zoom, profile.view.max_zoom, profile.view.max_view_zoom = 0, 16, 24
    profile.output.local_directory = str(base / "out")
    profile.layers = [LayerConfig(square.id(), initially_visible=True),
                      LayerConfig(line.id(), initially_visible=True)]
    result = export_local(project, profile, EXTENT)
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        yield {"url": server.url(f"{profile.slug}/index.html")}


def _run(url, actions, tmp_path):
    path = tmp_path / "actions.json"
    path.write_text(json.dumps(actions))
    run = subprocess.run(["node", "interact.mjs", url, str(path), "800", "600"],
                         capture_output=True, text=True, cwd=HERE, timeout=600)
    assert run.returncode == 0, run.stderr[-3000:]
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert not out["pageErrors"], out
    return out["results"]


PIXEL = """const m = q2vtViewer.map; m.redraw();
  const gl = m.getCanvas().getContext('webgl2') || m.getCanvas().getContext('webgl');
  const px = new Uint8Array(4);
  gl.readPixels(gl.drawingBufferWidth >> 1, gl.drawingBufferHeight >> 1, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);
  return { zoom: m.getZoom(), max: m.getMaxZoom(), px: Array.from(px) };"""


def test_background_and_layers_drawn_to_the_deepest_zoom(site, tmp_path):
    """The background layer used to end at 22 (MapLibre hides a layer at its
    maxzoom) and the data layers at 24: the page showed through."""
    actions = []
    for zoom in (21.9, 22, 23, 24):  # 24: clamped to the map's maximum
        for xy in (EMPTY, SQUARE.center()):
            xy = (xy[0], xy[1]) if isinstance(xy, tuple) else (xy.x(), xy.y())
            actions += [{"eval": f"q2vtViewer.map.jumpTo({{center: {json.dumps(_lnglat(*xy))}, zoom: {zoom}}}); return 1;"},
                        {"idle": True}, {"eval": PIXEL}]
    results = [r for r in _run(site["url"], actions, tmp_path) if r != 1]
    for empty, square in zip(results[::2], results[1::2]):
        assert empty["px"] == [255, 255, 0, 255], empty
        assert square["px"] == [0, 0, 255, 255], square
    assert 23.9 < results[-1]["zoom"] == results[-1]["max"] < 24


def test_label_points_keep_sub_pixel_precision_in_overzoom(site, tmp_path):
    """The label point source stopped at z15: its 1/8192 tile grid moved the
    points up to 4 px at z22 and 15 px near z24, off their line."""
    actions = []
    for zoom in (16, 22, 23.9):
        for t in (-40.0, 3.7, 21.3):  # metres along the line
            xy = (X0 + t * math.cos(ANGLE), Y0 + t * math.sin(ANGLE))
            actions += [{"eval": f"q2vtViewer.map.jumpTo({{center: {json.dumps(_lnglat(*xy))}, zoom: {zoom}}}); return 1;"},
                        {"wait": 400}, {"idle": True}, {"eval": """
const v = q2vtViewer, m = v.map;
const measure = () => {
  const out = { zoom: m.getZoom(), pairs: [] }, exact = new Map();
  for (const features of v.labels.snapshot().values()) {
    for (const f of features) exact.set(f.id, m.project(f.geometry.coordinates));
  }
  for (const group of v.labels.groups.values()) {
    for (const f of m.querySourceFeatures(group.source)) {
      const want = exact.get(f.id), got = m.project(f.geometry.coordinates);
      if (want) out.pairs.push(Math.hypot(got.x - want.x, got.y - want.y));
    }
  }
  return out;
};
// The label points are written once the tiles are in (a loaded machine:
// later than the map's idle).
let out = measure();
for (let i = 0; i < 50 && !out.pairs.length; i++) {
  await new Promise((r) => setTimeout(r, 100));
  out = measure();
}
return out;"""}]
    results = [r for r in _run(site["url"], actions, tmp_path) if r != 1]
    for result in results:
        assert result["pairs"], result
        assert max(result["pairs"]) <= 0.75, result  # half a pixel per axis
