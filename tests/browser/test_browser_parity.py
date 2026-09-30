"""QGIS-vs-browser parity for geometry-sensitive symbols.

Each case exports a small layer, renders it with QGIS and with the bundled
MapLibre at the same Web Mercator viewport, and compares inked pixels (the
gallery score of ``tools/gallery``). These are the cases where a structural
check cannot tell whether the browser draws on the right side of a line.
"""

import importlib.util
import json
import os
import subprocess

import pytest
from qgis.core import (Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature,
                       QgsGeometry, QgsMarkerLineSymbolLayer, QgsMarkerSymbol,
                       QgsProcessingFeedback, QgsProject, QgsRectangle,
                       QgsSimpleLineSymbolLayer, QgsSimpleMarkerSymbolLayer,
                       QgsSingleSymbolRenderer, QgsVectorLayer, QgsFillSymbol,
                       QgsLineSymbol)
from qgis.PyQt.QtGui import QColor

import sys

from q2vt_fixtures import reset_project, to_geopackage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_browser_smoke import HERE, PORT, _serve  # noqa: E402 pylint: disable=wrong-import-position

ROOT = os.path.dirname(os.path.dirname(HERE))
CENTER = (2120500.0, 6020500.0)
ZOOM = 16.5
SIZE = 360
_spec = importlib.util.spec_from_file_location(
    "q2vt_gallery", os.path.join(ROOT, "tools", "gallery", "build_gallery.py"))
gallery = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gallery)

pytestmark = pytest.mark.browser


def _polygon_layer(path, clockwise):
    ring = [(-120, -90), (120, -90), (120, 80), (0, 130), (-120, 80)]
    if clockwise:  # y-up clockwise (ESRI shapefile convention)
        ring = list(reversed(ring))
    coords = ", ".join(f"{CENTER[0] + x} {CENTER[1] + y}" for x, y in ring + ring[:1])
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "rings", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt(f"POLYGON(({coords}))"))
    layer.dataProvider().addFeature(feature)
    return to_geopackage(layer, path)


def _compare(tmp_path, layer, metric="near", center=CENTER):
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-error
    reset_project(layer)
    half = SIZE / 2 * gallery.EARTH / (512 * 2 ** ZOOM)
    extent = QgsRectangle(center[0] - 2 * half, center[1] - 2 * half,
                          center[0] + 2 * half, center[1] + 2 * half)
    out = tmp_path / "out"
    out.mkdir()
    export_dir = QGIS2VectorTiles(min_zoom=15, max_zoom=17, extent=extent, output_dir=str(out),
                                  feedback=QgsProcessingFeedback(), serve=False,
                                  background_type=2).convert_project_to_vector_tiles()
    to_wgs = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:3857"),
                                    QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
    point = to_wgs.transform(*center)
    views = tmp_path / "views.json"
    views.write_text(json.dumps([{"id": "v", "lon": point.x(), "lat": point.y(), "zoom": ZOOM,
                                  "width": SIZE, "height": SIZE}]))
    qgis_png = str(tmp_path / "v_qgis.png")
    gallery.qgis_render(layer, center, ZOOM, SIZE, qgis_png)
    server = _serve(export_dir)
    try:
        run = subprocess.run(["node", os.path.join(HERE, "gallery_capture.mjs"), export_dir,
                              str(PORT), str(views), str(tmp_path)],
                             capture_output=True, text=True, cwd=HERE, timeout=120)
        assert run.returncode == 0, run.stderr
    finally:
        server.kill()
    browser_png = str(tmp_path / "v_browser.png")
    if metric == "shape":  # pixel-level mismatch (the gallery score)
        return gallery.score(qgis_png, browser_png)["shape"]
    if metric == "ink":  # inked pixel counts (qgis, browser)
        result = gallery.score(qgis_png, browser_png)
        return result["ink_qgis"], result["ink_browser"]
    return _near_fraction(qgis_png, browser_png)


def _near_fraction(qgis_png, browser_png, grow=5):
    """Share of browser ink within ``grow`` px of QGIS ink and vice versa
    (min of both): position-sensitive, tolerant to marker phase."""
    from PIL import Image, ImageChops, ImageFilter
    masks = [Image.open(p).convert("L").point(lambda v: 255 if v < 245 else 0)
             for p in (qgis_png, browser_png)]
    grown = [m.filter(ImageFilter.MaxFilter(2 * grow + 1)) for m in masks]
    shares = []
    for mask, other in ((masks[1], grown[0]), (masks[0], grown[1])):
        total = sum(1 for v in mask.getdata() if v)
        near = sum(1 for v in ImageChops.multiply(mask, other).getdata() if v)
        shares.append(near / total if total else 0.0)
    return min(shares)


def _outline_symbol(kind, offset):
    if kind == "line":
        line = QgsSimpleLineSymbolLayer(QColor("black"), 1.5)
        line.setWidthUnit(Qgis.RenderUnit.MapUnits)
        line.setOffset(offset)
        line.setOffsetUnit(Qgis.RenderUnit.MapUnits)
        return QgsFillSymbol([line])
    marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Square, 2)
    marker.setColor(QColor("red"))
    marker.setStrokeStyle(0)
    markers = QgsMarkerLineSymbolLayer(True, 4)
    markers.setIntervalUnit(Qgis.RenderUnit.Millimeters)
    markers.setSubSymbol(QgsMarkerSymbol([marker]))
    markers.setOffset(offset)
    markers.setOffsetUnit(Qgis.RenderUnit.MapUnits)
    return QgsFillSymbol([markers])


@pytest.mark.parametrize("clockwise", [False, True])
@pytest.mark.parametrize("kind", ["line", "markers"])
def test_polygon_outline_offsets_follow_the_source_ring(tmp_path, kind, clockwise):
    layer = _polygon_layer(str(tmp_path / "rings.gpkg"), clockwise)
    layer.setRenderer(QgsSingleSymbolRenderer(_outline_symbol(kind, -12)))
    # A wrong-side offset (24 m = 30 px away) puts all ink far from QGIS ink.
    assert _compare(tmp_path, layer) > 0.95


@pytest.mark.parametrize("cap", ["flat", "square", "round"])
@pytest.mark.parametrize("pattern", ["custom", "dash", "dashdot"])
def test_dash_patterns_follow_qt(tmp_path, cap, pattern):
    from qgis.PyQt.QtCore import Qt
    layer = _polygon_layer(str(tmp_path / "rings.gpkg"), False)
    line = QgsSimpleLineSymbolLayer(QColor("black"), 3.0)
    line.setWidthUnit(Qgis.RenderUnit.MapUnits)
    if pattern == "custom":
        line.setUseCustomDashPattern(True)
        line.setCustomDashVector([6.0, 10.0])
        line.setCustomDashPatternUnit(Qgis.RenderUnit.MapUnits)
    else:
        line.setPenStyle({"dash": Qt.PenStyle.DashLine,
                          "dashdot": Qt.PenStyle.DashDotLine}[pattern])
    line.setPenCapStyle({"flat": Qt.PenCapStyle.FlatCap, "square": Qt.PenCapStyle.SquareCap,
                         "round": Qt.PenCapStyle.RoundCap}[cap])
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([line])))
    # Pixel-level: dash lengths and caps (a 1-width error per dash fails).
    assert _compare(tmp_path, layer, metric="shape") < 0.08


@pytest.mark.parametrize("placement,gallery_cell", [
    ("Line", None), ("Curved", None), ("Curved", 209)])
def test_map_unit_line_labels_are_drawn(tmp_path, placement, gallery_cell):
    from qgis.core import QgsPalLayerSettings, QgsTextFormat, QgsVectorLayerSimpleLabeling
    from q2vt_fixtures import to_geopackage as save
    memory = QgsVectorLayer("LineString?crs=EPSG:3857&field=name:string", "roads", "memory")
    feature = QgsFeature(memory.fields())
    feature.setAttribute("name", "1ő")
    points = [(-120, -80), (-40, 100), (30, -40), (120, 70)]
    center = CENTER
    wkt = "LINESTRING(" + ", ".join(f"{center[0] + x} {center[1] + y}" for x, y in points) + ")"
    if gallery_cell is not None:  # where tile edges clipped the style gallery's label
        row, col = divmod(gallery_cell, gallery.COLUMNS)
        x0 = gallery.ORIGIN[0] + col * (gallery.CELL + gallery.GAP)
        y0 = gallery.ORIGIN[1] - row * (gallery.CELL + gallery.GAP)
        wkt = gallery.feature_geometries("LineString", x0, y0, 1)[0]
        center = (x0 + gallery.CELL / 2, y0 - gallery.CELL / 2)
    feature.setGeometry(QgsGeometry.fromWkt(wkt))
    memory.dataProvider().addFeature(feature)
    layer = save(memory, str(tmp_path / "roads.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({"color": "200,200,200"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "name"
    settings.placement = getattr(Qgis.LabelPlacement, placement)
    fmt = QgsTextFormat()
    fmt.setSize(40 if gallery_cell is None else 80)
    fmt.setSizeUnit(Qgis.RenderUnit.MapUnits)
    settings.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    # MapLibre fits line labels with the text size at zoom 18 (4x the z16
    # size here): without per-zoom sizes the label is dropped.
    ink_qgis, ink_browser = _compare(tmp_path, layer, metric="ink", center=center)
    line_only = 0.5 * ink_qgis
    assert ink_browser > line_only and ink_browser == pytest.approx(ink_qgis, rel=0.35)


@pytest.mark.parametrize("font", ["DejaVu Serif", "DejaVu Sans"])
def test_font_marker_text_sits_where_qgis_draws_it(tmp_path, font):
    """QGIS draws a font marker with its baseline half the font's ascent
    below the point; browser text is offset to match."""
    from qgis.core import QgsFontMarkerSymbolLayer
    from q2vt_fixtures import to_geopackage as save
    memory = QgsVectorLayer("Point?crs=EPSG:3857", "pts", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt(f"POINT({CENTER[0]} {CENTER[1]})"))
    memory.dataProvider().addFeature(feature)
    layer = save(memory, str(tmp_path / "pts.gpkg"))
    marker = QgsFontMarkerSymbolLayer(font, "VH", 40)
    marker.setSizeUnit(Qgis.RenderUnit.Points)
    marker.setColor(QColor("black"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsMarkerSymbol([marker])))
    # 0.16 em (8 px here) too high shifted most of the ink off its place.
    assert _compare(tmp_path, layer, metric="shape") < 0.12
