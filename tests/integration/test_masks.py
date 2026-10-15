"""QGIS selective masking on the web map (fidelity.masks): a label's mask is
its halo in the map's background colour, a mask marker its shape in that
colour; both reported as approximate."""

import json
import os
import sys

import pytest
from qgis.core import (QgsMarkerSymbol, QgsMaskMarkerSymbolLayer, QgsPalLayerSettings,
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
