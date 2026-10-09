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
from qgis.core import (Qgis, QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsFeature, QgsField,
                       QgsGeometry, QgsMarkerLineSymbolLayer, QgsMarkerSymbol,
                       QgsProcessingFeedback, QgsProject, QgsRectangle, QgsRendererCategory,
                       QgsSimpleLineSymbolLayer, QgsSimpleMarkerSymbolLayer,
                       QgsSingleSymbolRenderer, QgsVectorLayer, QgsFillSymbol,
                       QgsLineSymbol)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor

import sys

from q2vt_fixtures import reset_project, to_geopackage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_browser_smoke import HERE, _serve  # noqa: E402 pylint: disable=wrong-import-position

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
                              str(server.port), str(views), str(tmp_path)],
                             capture_output=True, text=True, cwd=HERE, timeout=120)
        assert run.returncode == 0, run.stderr
    finally:
        server.kill()
    browser_png = str(tmp_path / "v_browser.png")
    if metric == "shape":  # pixel-level mismatch (the gallery score)
        return gallery.score(qgis_png, browser_png)["shape"]
    if metric == "color":  # colour mismatch (the gallery's colour score)
        return gallery.score(qgis_png, browser_png)["color"]
    if metric == "darkness":  # total ink, 1 = one fully black pixel (qgis, browser)
        from PIL import Image
        return tuple(sum(255 - v for v in Image.open(path).convert("L").getdata()) / 255.0
                     for path in (qgis_png, browser_png))
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


@pytest.mark.parametrize("offset", [-3.0, 3.0])
@pytest.mark.parametrize("clockwise", [False, True])
def test_polygon_outline_screen_offsets_follow_qgis(tmp_path, clockwise, offset):
    """A millimetre offset stays a native line offset: a positive one moves
    the outline inside the polygon like QGIS, whatever the ring order."""
    layer = _polygon_layer(str(tmp_path / "rings.gpkg"), clockwise)
    line = QgsSimpleLineSymbolLayer(QColor("black"), 1.0)
    line.setOffset(offset)
    line.setOffsetUnit(Qgis.RenderUnit.Millimeters)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([line])))
    # The wrong side is 6 mm (about 22 px) away from QGIS's outline.
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


def test_repeated_curved_labels_of_a_wiggly_river_are_drawn(tmp_path):
    """Swellendam rivers: a curved label repeated along a river whose
    vertices turn 30 degrees every 5 px. MapLibre's own line placement
    found no anchor on it (its angle check adds up the wiggles); the export
    lays the label out as QGIS does and MapLibre draws it. (The river lies
    in the view: QGIS cuts the visible part at the repeat distance.)"""
    import math
    from qgis.core import QgsPalLayerSettings, QgsTextFormat, QgsVectorLayerSimpleLabeling
    from q2vt_fixtures import to_geopackage as save
    memory = QgsVectorLayer("LineString?crs=EPSG:3857&field=name:string", "rivers", "memory")
    feature = QgsFeature(memory.fields())
    feature.setAttribute("name", "Koornlands")
    x, y, points = CENTER[0] - 114.0, CENTER[1] - 20.0, []
    for i in range(49):  # 5 m segments heading 10 +- 15 degrees: 240 m
        points.append(f"{x} {y}")
        heading = math.radians(10 + (15 if i % 2 else -15))
        x, y = x + 5 * math.cos(heading), y + 5 * math.sin(heading)
    feature.setGeometry(QgsGeometry.fromWkt(f"LINESTRING({', '.join(points)})"))
    memory.dataProvider().addFeature(feature)
    layer = save(memory, str(tmp_path / "rivers.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsLineSymbol.createSimple({"line_style": "no"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "name"
    settings.placement = Qgis.LabelPlacement.Curved
    settings.repeatDistance = 70
    settings.repeatDistanceUnit = Qgis.RenderUnit.Millimeters
    fmt = QgsTextFormat()
    fmt.setSize(9)
    settings.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    ink_qgis, ink_browser = _compare(tmp_path, layer, metric="ink")
    assert ink_qgis > 100  # QGIS draws the label (the river itself is not drawn)
    assert ink_browser == pytest.approx(ink_qgis, rel=0.35)


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


def test_thin_lines_get_the_ink_qgis_gives_them(tmp_path):
    """Qt inks a 0.3 px line with 0.3 px of ink; MapLibre's antialiasing
    alone gives it ~0.43 px (1.2 px lines agree): opacity compensates."""
    from qgis.core import QgsCategorizedSymbolRenderer, QgsField, QgsRendererCategory
    from qgis.PyQt.QtCore import QVariant
    from q2vt_fixtures import to_geopackage as save
    res = gallery.EARTH / (512 * 2 ** ZOOM)
    widths = [0.15, 0.3, 0.45, 0.6, 0.75]
    memory = QgsVectorLayer("LineString?crs=EPSG:3857", "thin", "memory")
    memory.dataProvider().addAttributes([QgsField("k", QVariant.Int)])
    memory.updateFields()
    for k in range(len(widths)):
        for row in range(4):  # a spread of sub-pixel positions
            feature = QgsFeature(memory.fields())
            feature.setAttributes([k])
            y = CENTER[1] + ((k * 4 + row) - 10) * 7.3 * res
            feature.setGeometry(QgsGeometry.fromWkt(
                f"LINESTRING({CENTER[0] - 150 * res} {y}, {CENTER[0] + 150 * res} {y})"))
            memory.dataProvider().addFeature(feature)
    layer = save(memory, str(tmp_path / "thin.gpkg"))
    categories = []
    for k, width in enumerate(widths):
        line = QgsSimpleLineSymbolLayer(QColor("black"), width * res)
        line.setWidthUnit(Qgis.RenderUnit.MapUnits)
        categories.append(QgsRendererCategory(k, QgsLineSymbol([line]), str(k)))
    layer.setRenderer(QgsCategorizedSymbolRenderer("k", categories))
    qgis, browser = _compare(tmp_path, layer, metric="darkness")
    assert browser == pytest.approx(qgis, rel=0.12)


def _red_and_blue_boxes(path):
    from PIL import Image
    image = Image.open(path).convert("RGB")
    boxes = []
    for test in (lambda p: p[0] > 150 and p[1] < 150 and p[2] < 150,
                 lambda p: p[2] > 150 and p[0] < 150):
        points = [(x, y) for y in range(image.height) for x in range(image.width)
                  if test(image.getpixel((x, y)))]
        boxes.append((min(p[0] for p in points), min(p[1] for p in points),
                      max(p[0] for p in points), max(p[1] for p in points)))
    return boxes


@pytest.mark.parametrize("zoom", [16.0, 16.5])
def test_label_frames_follow_map_unit_text_between_zooms(tmp_path, monkeypatch, zoom):
    """A frame fitted to map-unit text: MapLibre reads a size curve only at
    the stops covering [tile zoom, +1], so each zoom has its own icon-size
    ramp; with one curve the frame kept its integer-zoom size."""
    from qgis.core import (QgsPalLayerSettings, QgsTextBackgroundSettings, QgsTextFormat,
                           QgsVectorLayerSimpleLabeling)
    from qgis.PyQt.QtCore import QSizeF
    from q2vt_fixtures import to_geopackage as save
    monkeypatch.setattr(sys.modules[__name__], "ZOOM", zoom)
    memory = QgsVectorLayer("Point?crs=EPSG:3857&field=t:string", "p", "memory")
    feature = QgsFeature(memory.fields())
    feature.setAttributes(["1ő"])
    feature.setGeometry(QgsGeometry.fromWkt(f"POINT({CENTER[0]} {CENTER[1]})"))
    memory.dataProvider().addFeature(feature)
    layer = save(memory, str(tmp_path / "p.gpkg"))
    hidden = QgsMarkerSymbol.createSimple({"size": "0"})
    hidden.setOpacity(0)
    layer.setRenderer(QgsSingleSymbolRenderer(hidden))
    settings = QgsPalLayerSettings()
    settings.fieldName = "t"
    settings.placement = Qgis.LabelPlacement.OverPoint
    fmt = QgsTextFormat()
    fmt.setSize(17)
    fmt.setSizeUnit(Qgis.RenderUnit.MapUnits)
    fmt.setColor(QColor("blue"))
    frame = QgsTextBackgroundSettings()
    frame.setEnabled(True)
    frame.setType(QgsTextBackgroundSettings.ShapeType.ShapeRectangle)
    frame.setSizeType(QgsTextBackgroundSettings.SizeType.SizeBuffer)
    frame.setSize(QSizeF(3, 3))
    frame.setSizeUnit(Qgis.RenderUnit.MapUnits)
    frame.setFillColor(QColor(255, 255, 255, 0))
    frame.setStrokeColor(QColor("red"))
    frame.setStrokeWidth(0.2)
    frame.setStrokeWidthUnit(Qgis.RenderUnit.Millimeters)
    fmt.setBackground(frame)
    settings.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    _compare(tmp_path, layer, metric="shape")
    (qf, qt), (bf, bt) = (_red_and_blue_boxes(str(tmp_path / f"v_{n}.png"))
                          for n in ("qgis", "browser"))
    for a, b in zip(qf, bf):  # frame edges within 2 px
        assert abs(a - b) <= 2, (qf, bf)


@pytest.mark.parametrize("zoom", [16.0, 16.5])
def test_label_frames_with_a_data_defined_size_keep_a_map_unit_border(tmp_path, monkeypatch, zoom):
    """Zone labels with a data-defined map-unit size and a frame bordered in
    map units: the size cannot be read per zoom, so one frame image was made
    for all zooms, its border converted at the lowest zoom (none drawn)."""
    from qgis.core import (QgsPalLayerSettings, QgsProperty, QgsTextBackgroundSettings,
                           QgsTextFormat, QgsVectorLayerSimpleLabeling)
    from qgis.PyQt.QtCore import QSizeF
    from q2vt_fixtures import to_geopackage as save
    monkeypatch.setattr(sys.modules[__name__], "ZOOM", zoom)
    memory = QgsVectorLayer("Point?crs=EPSG:3857&field=t:string&field=big:integer", "p", "memory")
    feature = QgsFeature(memory.fields())
    feature.setAttributes(["1ő", 1])
    feature.setGeometry(QgsGeometry.fromWkt(f"POINT({CENTER[0]} {CENTER[1]})"))
    memory.dataProvider().addFeature(feature)
    layer = save(memory, str(tmp_path / "p.gpkg"))
    hidden = QgsMarkerSymbol.createSimple({"size": "0"})
    hidden.setOpacity(0)
    layer.setRenderer(QgsSingleSymbolRenderer(hidden))
    settings = QgsPalLayerSettings()
    settings.fieldName = "t"
    settings.placement = Qgis.LabelPlacement.OverPoint
    settings.dataDefinedProperties().setProperty(
        QgsPalLayerSettings.Property.Size, QgsProperty.fromExpression('if("big" = 1, 17, 25)'))
    fmt = QgsTextFormat()
    fmt.setSize(25)
    fmt.setSizeUnit(Qgis.RenderUnit.MapUnits)
    fmt.setColor(QColor("blue"))
    frame = QgsTextBackgroundSettings()
    frame.setEnabled(True)
    frame.setType(QgsTextBackgroundSettings.ShapeType.ShapeRectangle)
    frame.setSizeType(QgsTextBackgroundSettings.SizeType.SizeBuffer)
    frame.setSize(QSizeF(3, 3))
    frame.setSizeUnit(Qgis.RenderUnit.MapUnits)
    frame.setFillColor(QColor(255, 255, 255, 0))
    frame.setStrokeColor(QColor("red"))
    frame.setStrokeWidth(2)
    frame.setStrokeWidthUnit(Qgis.RenderUnit.MapUnits)
    fmt.setBackground(frame)
    settings.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    _compare(tmp_path, layer, metric="shape")
    (qf, qt), (bf, bt) = (_red_and_blue_boxes(str(tmp_path / f"v_{n}.png"))
                          for n in ("qgis", "browser"))
    for a, b in zip(qf, bf):  # the border is drawn, its edges within 2 px
        assert abs(a - b) <= 2, (qf, bf, qt, bt)


def _period(png, axis=0):
    """Dominant repeat distance (px) of a pattern's ink along ``axis``."""
    import numpy as np
    from PIL import Image
    ink = np.asarray(Image.open(png).convert("L")) < 200
    ink = ink[SIZE // 4:3 * SIZE // 4, SIZE // 4:3 * SIZE // 4]
    profile = ink.sum(axis=axis).astype(float)
    spectrum = np.abs(np.fft.rfft(profile - profile.mean()))
    k = int(spectrum[3:].argmax()) + 3
    return len(profile) / k


@pytest.mark.parametrize("unit", ["map", "mm"])
@pytest.mark.parametrize("zoom", [16.25, 16.75])
def test_pattern_textures_keep_the_qgis_spacing_between_zooms(tmp_path, monkeypatch, unit, zoom):
    """MapLibre draws fill-pattern in the pixels of the tile's integer zoom,
    so a texture grows with the map until the next zoom (Kis szaggatott: a
    texture laid out for the middle of the zoom was 1.4x too sparse). Screen
    sizes are flagged for the patched MapLibre and keep their exact size."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    monkeypatch.setattr(sys.modules[__name__], "ZOOM", zoom)
    layer = _polygon_layer(str(tmp_path / "pp.gpkg"), False)
    marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Square, 2 if unit == "map" else 0.6)
    marker.setSizeUnit(Qgis.RenderUnit.MapUnits if unit == "map" else Qgis.RenderUnit.Millimeters)
    marker.setColor(QColor("black"))
    marker.setStrokeStyle(0)
    pattern = QgsPointPatternFillSymbolLayer()
    pattern.setSubSymbol(QgsMarkerSymbol([marker]))
    spacing = 5.0 if unit == "map" else 1.6
    pattern_unit = Qgis.RenderUnit.MapUnits if unit == "map" else Qgis.RenderUnit.Millimeters
    for name in ("DistanceX", "DistanceY"):
        getattr(pattern, f"set{name}")(spacing)
        getattr(pattern, f"set{name}Unit")(pattern_unit)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([pattern])))
    _compare(tmp_path, layer, metric="shape")
    qgis, browser = (_period(str(tmp_path / f"v_{n}.png")) for n in ("qgis", "browser"))
    assert browser == pytest.approx(qgis, rel=0.12 if unit == "map" else 0.03), (qgis, browser)


@pytest.mark.parametrize("kind", ["hatch", "points"])
def test_viewport_aligned_patterns_start_at_the_view_corner(tmp_path, kind):
    """QGIS "Align pattern to: Viewport" starts the pattern at the corner of
    the map view (lines at x = 0, 10, 20 ... px on screen whatever the pan):
    the browser anchors such layers at the canvas corner, so even the
    pattern's phase matches."""
    from qgis.core import QgsLinePatternFillSymbolLayer, QgsPointPatternFillSymbolLayer
    layer = _polygon_layer(str(tmp_path / "vp.gpkg"), False)
    if kind == "hatch":
        pattern = QgsLinePatternFillSymbolLayer()
        pattern.setLineAngle(90)
        pattern.setDistance(10)
        pattern.setDistanceUnit(Qgis.RenderUnit.Pixels)
        pattern.setSubSymbol(QgsLineSymbol.createSimple(
            {"color": "black", "width": "2", "width_unit": "Pixel"}))
    else:
        pattern = QgsPointPatternFillSymbolLayer()
        for name in ("DistanceX", "DistanceY"):
            getattr(pattern, f"set{name}")(12)
            getattr(pattern, f"set{name}Unit")(Qgis.RenderUnit.Pixels)
        marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Square, 4)
        marker.setSizeUnit(Qgis.RenderUnit.Pixels)
        marker.setColor(QColor("black"))
        marker.setStrokeStyle(0)
        pattern.setSubSymbol(QgsMarkerSymbol([marker]))
    pattern.setCoordinateReference(Qgis.SymbolCoordinateReference.Viewport)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([pattern])))
    assert _compare(tmp_path, layer, metric="shape") < 0.05


@pytest.mark.parametrize("brush", ["diagonal_x", "horizontal", "dense4"])
def test_qt_brush_patterns_are_drawn_like_qgis(tmp_path, brush):
    """A simple fill with a Qt brush style (crossed diagonals, horizontal
    lines, dense dots): QGIS fills with the brush, an 8 px pattern starting
    at the corner of the view. It was drawn as a solid fill."""
    layer = _polygon_layer(str(tmp_path / "brush.gpkg"), False)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
        {"color": "0,0,0", "style": brush, "outline_style": "no"})))
    assert _compare(tmp_path, layer, metric="shape") < 0.05
    from PIL import Image
    qgis, browser = (sum(255 - v for v in Image.open(str(tmp_path / f"v_{n}.png")).convert("L").getdata())
                     for n in ("qgis", "browser"))
    assert browser == pytest.approx(qgis, rel=0.1)


def _feature_pattern(kind, tmp_path):
    """A screen-unit pattern of the given kind, feature-aligned (QGIS default)."""
    from qgis.core import (QgsLinePatternFillSymbolLayer, QgsPointPatternFillSymbolLayer,
                           QgsRasterFillSymbolLayer, QgsSVGFillSymbolLayer)
    if kind.startswith("hatch"):
        pattern = QgsLinePatternFillSymbolLayer()
        pattern.setLineAngle({"hatch_v": 90, "hatch_h": 0, "hatch_d": 45}[kind])
        pattern.setDistance(10)
        pattern.setDistanceUnit(Qgis.RenderUnit.Pixels)
        pattern.setSubSymbol(QgsLineSymbol.createSimple(
            {"color": "black", "width": "2", "width_unit": "Pixel"}))
        return pattern
    if kind == "points":
        pattern = QgsPointPatternFillSymbolLayer()
        for name in ("DistanceX", "DistanceY"):
            getattr(pattern, f"set{name}")(12)
            getattr(pattern, f"set{name}Unit")(Qgis.RenderUnit.Pixels)
        marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Square, 4)
        marker.setSizeUnit(Qgis.RenderUnit.Pixels)
        marker.setColor(QColor("black"))
        marker.setStrokeStyle(0)
        pattern.setSubSymbol(QgsMarkerSymbol([marker]))
        return pattern
    if kind == "svg":
        svg = tmp_path / "corner.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" '
                       'viewBox="0 0 12 12"><rect width="4" height="4" fill="#000"/></svg>')
        pattern = QgsSVGFillSymbolLayer(str(svg), 12, 0)
        pattern.setPatternWidthUnit(Qgis.RenderUnit.Pixels)
        pattern.setSvgStrokeWidth(0)
        return pattern
    from PIL import Image
    image = Image.new("RGBA", (12, 12), (0, 0, 0, 0))
    for x in range(4):
        for y in range(4):
            image.putpixel((x, y), (0, 0, 0, 255))
    png = tmp_path / "corner.png"
    image.save(png)
    pattern = QgsRasterFillSymbolLayer(str(png))
    pattern.setWidth(12)
    pattern.setSizeUnit(Qgis.RenderUnit.Pixels) if hasattr(pattern, "setSizeUnit") \
        else pattern.setWidthUnit(Qgis.RenderUnit.Pixels)
    return pattern


@pytest.mark.parametrize("kind", ["hatch_v", "hatch_h", "hatch_d", "points", "svg", "raster"])
def test_feature_aligned_patterns_start_at_each_feature(tmp_path, kind):
    """QGIS "Align pattern to: Feature" (the default) starts a point, line or
    SVG pattern at the bottom-left of each feature's bounding box and a
    raster fill at the top-left of each part, rounded to whole pixels. Three
    features (one of two parts) at fractional pixel offsets: the browser
    draws every pattern with QGIS's phase."""
    px = gallery.EARTH / (512 * 2 ** ZOOM)
    shapes = [
        "MULTIPOLYGON((({a} {b}, {c} {b}, {c} {d}, {a} {d}, {a} {b})))".format(
            a=-140 * px, b=-130 * px, c=-30.6 * px, d=-17.3 * px),
        "MULTIPOLYGON((({a} {b}, {c} {b}, {c} {d}, {a} {d}, {a} {b})))".format(
            a=-13.4 * px, b=-127.7 * px, c=140 * px, d=-35 * px),
        "MULTIPOLYGON((({a} {b}, {c} {b}, {c} {d}, {a} {d}, {a} {b})), "
        "(({e} {f}, {g} {f}, {g} {h}, {e} {h}, {e} {f})))".format(
            a=-137.7 * px, b=5.6 * px, c=-20 * px, d=133.2 * px,
            e=7.3 * px, f=21.4 * px, g=136 * px, h=128 * px),
    ]
    layer = QgsVectorLayer("MultiPolygon?crs=EPSG:3857", "anchors", "memory")
    for wkt in shapes:
        feature = QgsFeature()
        geometry = QgsGeometry.fromWkt(wkt)
        geometry.translate(*CENTER)
        feature.setGeometry(geometry)
        layer.dataProvider().addFeature(feature)
    layer = to_geopackage(layer, str(tmp_path / "anchors.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([_feature_pattern(kind, tmp_path)])))
    assert _compare(tmp_path, layer, metric="shape") < 0.05


@pytest.mark.parametrize("kind", ["hatch_d", "points", "raster"])
def test_feature_aligned_patterns_of_features_beyond_the_view(tmp_path, kind):
    """A feature reaching far beyond the view: point / line / SVG patterns
    still start at its own corner; a raster fill starts where QGIS clips the
    part (the view grown by 10 %), so its phase follows the view."""
    px = gallery.EARTH / (512 * 2 ** ZOOM)
    layer = QgsVectorLayer("MultiPolygon?crs=EPSG:3857", "big", "memory")
    feature = QgsFeature()
    geometry = QgsGeometry.fromWkt(
        "MULTIPOLYGON((({a} {b}, {c} {b}, {c} {d}, {a} {d}, {a} {b})))".format(
            a=-2113.3 * px, b=-1907.7 * px, c=150 * px, d=2201.6 * px))
    geometry.translate(*CENTER)
    feature.setGeometry(geometry)
    layer.dataProvider().addFeature(feature)
    layer = to_geopackage(layer, str(tmp_path / "big.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([_feature_pattern(kind, tmp_path)])))
    assert _compare(tmp_path, layer, metric="shape") < 0.05


def _overlapping_squares(clip_mode):
    """Point pattern of outlined squares larger than their spacing, so their
    stacking order shows: QGIS draws them column by column from the left,
    each column from the top (later markers on top)."""
    from qgis.core import QgsPointPatternFillSymbolLayer
    pattern = QgsPointPatternFillSymbolLayer()
    for name in ("DistanceX", "DistanceY"):
        getattr(pattern, f"set{name}")(15.3)
        getattr(pattern, f"set{name}Unit")(Qgis.RenderUnit.Pixels)
    marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Square, 19)
    marker.setSizeUnit(Qgis.RenderUnit.Pixels)
    marker.setColor(QColor("#e0b040"))
    marker.setStrokeColor(QColor("#202060"))
    marker.setStrokeWidth(2)
    marker.setStrokeWidthUnit(Qgis.RenderUnit.Pixels)
    pattern.setSubSymbol(QgsMarkerSymbol([marker]))
    pattern.setClipMode(clip_mode)
    pattern.setCoordinateReference(Qgis.SymbolCoordinateReference.Feature)
    return pattern


@pytest.mark.parametrize("clip,metric,limit", [("centroid", "shape", 0.03),
                                                ("shape", "color", 0.03)])
def test_overlapping_pattern_markers_stack_like_qgis(tmp_path, clip, metric, limit):
    """Point patterns whose markers overlap. "Centroid within": whole
    markers, also the row centred on the top edge (4.16: cut at the edge,
    4.8 % of the pixels off); the rest is the spacing kept per eighth of a
    zoom (±4.5 %). "Shape": QGIS's own texture, two spacings truncated to
    whole pixels, markers stacked in its drawing order (4.16: 19 % of the
    colours off)."""
    px = gallery.EARTH / (512 * 2 ** ZOOM)
    layer = QgsVectorLayer("MultiPolygon?crs=EPSG:3857", "squares", "memory")
    feature = QgsFeature()
    geometry = QgsGeometry.fromWkt(
        "MULTIPOLYGON((({a} {b}, {c} {b}, {c} {d}, {a} {d}, {a} {b})))".format(
            a=-120.4 * px, b=-110 * px, c=104.3 * px, d=95.6 * px))
    geometry.translate(*CENTER)
    feature.setGeometry(geometry)
    layer.dataProvider().addFeature(feature)
    layer = to_geopackage(layer, str(tmp_path / "squares.gpkg"))
    mode = {"centroid": Qgis.MarkerClipMode.CentroidWithin,
            "shape": Qgis.MarkerClipMode.Shape}[clip]
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([_overlapping_squares(mode)])))
    assert _compare(tmp_path, layer, metric=metric) < limit


def test_overlapping_features_of_different_rules_keep_qgis_order(tmp_path):
    """Without symbol levels QGIS draws a categorized layer feature by
    feature: forest B, drawn after conservation area C, covers it; forest
    A, drawn before C, stays under it. The style drew every forest below
    every conservation area (one style layer per category), so the overlap
    of B and C was the wrong colour (10 % of the pixels)."""
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "landuse", "memory")
    layer.dataProvider().addAttributes([QgsField("landuse", QVariant.String)])
    layer.updateFields()
    for use, low in (("forest", -130), ("conservation", -70), ("forest", -10)):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([use])
        feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(
            CENTER[0] + low, CENTER[1] + low, CENTER[0] + low + 120, CENTER[1] + low + 120)))
        layer.dataProvider().addFeature(feature)
    layer = to_geopackage(layer, str(tmp_path / "landuse.gpkg"))
    layer.setRenderer(QgsCategorizedSymbolRenderer("landuse", [
        QgsRendererCategory(value, QgsFillSymbol.createSimple(
            {"color": color, "outline_style": "no"}), value)
        for value, color in (("forest", "40,140,40"), ("conservation", "60,60,200"))]))
    assert _compare(tmp_path, layer, metric="color") < 0.01
