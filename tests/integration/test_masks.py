"""QGIS selective masking on the web map (fidelity.masks): a label's mask is
its halo in the map's background colour, a mask marker its shape in that
colour; both reported as approximate."""

import json
import os
import sys

import pytest
from qgis.core import (Qgis, QgsMarkerSymbol, QgsMaskMarkerSymbolLayer, QgsPalLayerSettings,
                       QgsSingleSymbolRenderer, QgsSymbolLayerReference, QgsTextFormat,
                       QgsVectorLayerSimpleLabeling)
from qgis.PyQt.QtGui import QColor

from q2vt_fixtures import reset_project, zoning_layer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_end_to_end import export  # noqa: E402,F401  pylint: disable=wrong-import-position,unused-import

BACKGROUND = QColor(250, 240, 200)


def _masked_labels(path, buffer=False):
    layer = zoning_layer(path=path)
    symbol = layer.renderer().symbol()
    target = symbol.symbolLayer(0)
    settings = QgsPalLayerSettings()
    settings.fieldName = "zone"
    fmt = QgsTextFormat()
    fmt.setSize(10)
    mask = fmt.mask()
    mask.setEnabled(True)
    mask.setSize(2.0)  # mm
    mask.setOpacity(0.8)
    mask.setMaskedSymbolLayers([QgsSymbolLayerReference(layer.id(), target.id())])
    fmt.setMask(mask)
    if buffer:
        fmt.buffer().setEnabled(True)
    settings.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    return layer


def _style(result):
    return json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))


def _run(export, layer):  # noqa: F811
    project = reset_project(layer)
    project.setBackgroundColor(BACKGROUND)
    return export(layer, keep_project=True)


def test_label_mask_is_a_background_halo(export, tmp_path):  # noqa: F811
    exporter, result = _run(export, _masked_labels(str(tmp_path / "zones.gpkg")))
    labels = [l for l in _style(result)["layers"] if "text-field" in l.get("layout", {})]
    assert labels
    for label in labels:
        assert label["paint"]["text-halo-color"].replace(" ", "") in (
            "rgba(250,240,200,0.8)", "rgba(250,240,200,0.80)")
        # QGIS strokes the mask 2 mm wide around the glyphs: 1 mm beyond them.
        assert label["paint"]["text-halo-width"] == pytest.approx(96 / 25.4, rel=0.05)
    assert exporter.diagnostics.by_code("Q2VT_MASK_APPROXIMATE")


def test_label_buffer_stays_the_halo(export, tmp_path):  # noqa: F811
    _, result = _run(export, _masked_labels(str(tmp_path / "zones.gpkg"), buffer=True))
    labels = [l for l in _style(result)["layers"] if "text-field" in l.get("layout", {})]
    assert labels and all("250" not in str(l["paint"]["text-halo-color"]) for l in labels)


def test_mask_marker_is_its_shape_in_the_background_colour(plugin):
    from q2vt_plugin.src.core.fidelity import masks  # pylint: disable=import-error
    shape = QgsMarkerSymbol.createSimple({"name": "circle", "color": "255,0,0", "size": "6"})
    mask = QgsMaskMarkerSymbolLayer()
    mask.setSubSymbol(shape)
    mask.setMasks([QgsSymbolLayerReference("roads", "line-1")])
    symbol = QgsMarkerSymbol.createSimple({"name": "square", "color": "0,0,255", "size": "3"})
    symbol.insertSymbolLayer(0, mask)
    assert masks.knock_out_mask_markers(symbol, BACKGROUND) == 1
    drawn = symbol.symbolLayers()
    assert [l.layerType() for l in drawn] == ["SimpleMarker", "SimpleMarker"]
    knockout, own = drawn
    assert knockout.color() == BACKGROUND and knockout.strokeColor() == BACKGROUND
    assert knockout.size() == 6 and own.color() == QColor(0, 0, 255)
    # Masking nothing: QGIS draws nothing.
    idle = QgsMaskMarkerSymbolLayer()
    idle.setSubSymbol(QgsMarkerSymbol.createSimple({"name": "circle", "size": "6"}))
    other = QgsMarkerSymbol([idle])
    assert masks.knock_out_mask_markers(other, BACKGROUND) == 0
    assert not other.symbolLayer(0).enabled()  # skipped by the export
    QgsSingleSymbolRenderer(symbol)  # still a valid symbol


def test_mask_marker_exports_as_a_knockout(export, tmp_path):  # noqa: F811
    from qgis.core import QgsFeature, QgsGeometry, QgsVectorLayer  # pylint: disable=import-outside-toplevel
    from q2vt_fixtures import to_geopackage  # pylint: disable=import-outside-toplevel
    layer = QgsVectorLayer("Point?crs=EPSG:3857", "signs", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt("POINT(2121000 6021000)"))
    layer.dataProvider().addFeatures([feature])
    layer = to_geopackage(layer, str(tmp_path / "signs.gpkg"))
    mask = QgsMaskMarkerSymbolLayer()
    mask.setSubSymbol(QgsMarkerSymbol.createSimple({"name": "circle", "color": "255,0,0", "size": "6"}))
    mask.setMasks([QgsSymbolLayerReference("roads", "line-1")])
    symbol = QgsMarkerSymbol.createSimple({"name": "square", "color": "0,0,255", "size": "3"})
    symbol.insertSymbolLayer(0, mask)
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    exporter, result = _run(export, layer)
    assert not [d for d in exporter.diagnostics.by_code("Q2VT_UNSUPPORTED_SYMBOL_LAYER")
                if "MaskMarker" in str(d.message) + str(getattr(d, "detail", ""))]
    assert exporter.diagnostics.by_code("Q2VT_MASK_APPROXIMATE")
    assert layer.renderer().symbol().symbolLayer(0).layerType() == "MaskMarker"  # project unchanged
    assert [l for l in _style(result)["layers"] if l.get("source-layer") and l["type"] in ("symbol", "circle")]


def test_unsupported_renderer_is_reported(export, tmp_path):  # noqa: F811
    """A renderer QGIS cannot turn into rules (here 2.5D) drew nothing and
    was not in the report."""
    from qgis.core import Qgs25DRenderer  # pylint: disable=import-outside-toplevel
    layer = zoning_layer(path=str(tmp_path / "blocks.gpkg"))
    layer.setRenderer(Qgs25DRenderer())
    exporter, _ = _run(export, layer)
    found = exporter.diagnostics.by_code("Q2VT_UNSUPPORTED_RENDERER")
    assert found and "25dRenderer" in found[0].message and found[0].layer_id == layer.id()


def test_marker_sized_in_meters_at_scale_is_drawn(export, tmp_path):  # noqa: F811
    """A marker sized in "meters at scale" (QGIS Hub military symbols: a
    300 m square with a 40 m outline): the sprite's render context had no
    CRS for distances, QGIS drew it a thousandth of a pixel wide and every
    per-zoom sprite was empty."""
    from qgis.core import QgsFeature, QgsGeometry, QgsVectorLayer  # pylint: disable=import-outside-toplevel
    from q2vt_fixtures import to_geopackage  # pylint: disable=import-outside-toplevel
    layer = QgsVectorLayer("Point?crs=EPSG:3857", "points", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromWkt("POINT(2121000 6021000)"))
    layer.dataProvider().addFeatures([feature])
    layer = to_geopackage(layer, str(tmp_path / "points.gpkg"))
    symbol = QgsMarkerSymbol.createSimple({"name": "square", "color": "0,0,0,0", "outline_color": "0,0,0",
                                           "size": "300", "size_unit": "RenderMetersInMapUnits",
                                           "outline_width": "40",
                                           "outline_width_unit": "RenderMetersInMapUnits"})
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    exporter, result = _run(export, layer)
    assert not exporter.diagnostics.by_code("Q2VT_SPRITE_TRANSPARENT")
    assert [l for l in _style(result)["layers"] if l.get("type") == "symbol" and "icon-image" in l.get("layout", {})]


def test_pattern_marker_too_large_for_a_sprite_does_not_stop_the_export(export, tmp_path):  # noqa: F811
    """A random marker fill whose marker is sized in map units and is
    kilometres wide (QGIS Hub "Mondriaan"): too large for a sprite even at
    the lowest zoom. The pattern is reported and left out; the export
    finishes (it stopped with "Sprite 'pattern-marker' would be 51519 px
    wide")."""
    from qgis.core import QgsFillSymbol, QgsRandomMarkerFillSymbolLayer  # pylint: disable=import-outside-toplevel
    layer = zoning_layer(path=str(tmp_path / "zones.gpkg"))
    fill = QgsRandomMarkerFillSymbolLayer(10, Qgis.PointCountMethod.DensityBased, 250000.0)
    fill.setSubSymbol(QgsMarkerSymbol.createSimple({"name": "square", "size": "200000",
                                                    "size_unit": "MapUnit"}))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol([fill])))
    exporter, result = _run(export, layer)
    assert result and os.path.exists(os.path.join(result, "style", "style.json"))
    assert any("RandomMarkerFill left out" in d.message
               for d in exporter.diagnostics.by_code("Q2VT_SPRITE_RENDER_FAILED"))
