"""Expressions are evaluated with QGIS semantics on the source layer."""

import pytest
from qgis.core import (QgsFeature, QgsField, QgsFillSymbol, QgsGeometry, QgsMarkerSymbol,
                       QgsPalLayerSettings, QgsProcessingFeedback, QgsProject, QgsProperty,
                       QgsRectangle, QgsSingleSymbolRenderer, QgsSymbolLayer, QgsTextFormat,
                       QgsVectorLayer, QgsVectorLayerSimpleLabeling, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform)
from qgis.PyQt.QtCore import QVariant

from q2vt_fixtures import reset_project, to_geopackage, zoning_layer

# 100 m x 100 m square in EOV (EPSG:23700) near Budapest.
EOV_SQUARE = "POLYGON((650000 240000, 650100 240000, 650100 240100, 650000 240100, 650000 240000))"


def _export(tmp_path, layer, extent, ellipsoid="NONE"):
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener
    from q2vt_plugin.src.core.rules_exporter import RulesExporter
    from fidelity.diagnostics import DiagnosticCollector
    project = reset_project(layer)
    project.setEllipsoid(ellipsoid)
    diags = DiagnosticCollector()
    rules = RulesFlattener(0, 22, str(tmp_path), QgsProcessingFeedback(), diags).flatten_all_rules()
    utils = tmp_path / "utils"
    utils.mkdir()
    layers, rules = RulesExporter(rules, extent, 0, 22, str(utils), 0, QgsProcessingFeedback(),
                                  diagnostics=diags).export()
    return layers, rules


def _eov_layer(tmp_path):
    layer = QgsVectorLayer("Polygon?crs=EPSG:23700", "eov", "memory")
    layer.dataProvider().addAttributes([QgsField("id", QVariant.Int)])
    layer.updateFields()
    feature = QgsFeature(layer.fields())
    feature.setAttributes([1])
    feature.setGeometry(QgsGeometry.fromWkt(EOV_SQUARE))
    layer.dataProvider().addFeatures([feature])
    return to_geopackage(layer, str(tmp_path / "eov.gpkg"))


def _extent_3857():
    transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:23700"),
                                       QgsCoordinateReferenceSystem("EPSG:3857"),
                                       QgsProject.instance())
    box = transform.transformBoundingBox(QgsGeometry.fromWkt(EOV_SQUARE).boundingBox())
    box.grow(500)
    return box


def _area_label(layer):
    settings = QgsPalLayerSettings()
    settings.fieldName = "round($area)"
    settings.isExpression = True
    settings.setFormat(QgsTextFormat())
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "red"})))


@pytest.mark.parametrize("ellipsoid,expected,tolerance", [
    ("NONE", 10000, 0.5),          # planar in the layer CRS (EOV), like QGIS
    ("EPSG:7019", 10000, 60),      # ellipsoidal (GRS80); EOV scale factor ~0.9999
])
def test_area_labels_use_qgis_measurement(tmp_path, plugin, ellipsoid, expected, tolerance):
    layer = _eov_layer(tmp_path)
    _area_label(layer)
    extent = _extent_3857()
    layers, rules = _export(tmp_path, layer, extent, ellipsoid)
    labels = [l for l, r in zip(layers, rules) if r.get_attr("t") == 1] or \
        [l for l in layers if "q2vt_label" in l.fields().names()]
    values = [float(f["q2vt_label"]) for f in labels[0].getFeatures()]
    # Legacy: planar area of the Web Mercator copy, ~2.2x too large here.
    assert values and abs(values[0] - expected) <= tolerance, values


def test_feature_dependent_enabled_filters_features(tmp_path, plugin):
    layer = zoning_layer(path=str(tmp_path / "z.gpkg"))
    symbol = QgsMarkerSymbol.createSimple({"name": "circle", "size": "3"})
    symbol.symbolLayer(0).setDataDefinedProperty(
        QgsSymbolLayer.Property.PropertyLayerEnabled,
        QgsProperty.fromExpression("CASE WHEN \"zone\" = 'K1' THEN 1 WHEN \"zone\" IS NULL THEN NULL ELSE 0 END"))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    layers, _ = _export(tmp_path, layer, QgsRectangle(2119000, 6019000, 2123000, 6023000))
    ids = sorted(f["id"] for f in QgsVectorLayer(layers[0].source()).getFeatures()) \
        if "id" in layers[0].fields().names() else None
    count = layers[0].featureCount()
    # K1 enabled, NULL zone falls back to the static "enabled", K2/Lk disabled.
    assert count == 2, ids


def test_label_show_property_filters_labels(tmp_path, plugin):
    layer = zoning_layer(path=str(tmp_path / "z.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple({"color": "red"})))
    settings = QgsPalLayerSettings()
    settings.fieldName = "zone"
    settings.setFormat(QgsTextFormat())
    settings.dataDefinedProperties().setProperty(
        QgsPalLayerSettings.Property.Show, QgsProperty.fromExpression("\"zone\" <> 'K2'"))
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    layers, rules = _export(tmp_path, layer, QgsRectangle(2119000, 6019000, 2123000, 6023000))
    label_layer = [l for l, r in zip(layers, rules) if r.get_attr("t") == 1]
    label_layer = label_layer or [l for l in layers if "q2vt_label" in l.fields().names()]
    labels = sorted(str(f["q2vt_label"]) for f in label_layer[0].getFeatures())
    # NULL zone: `NULL <> 'K2'` is NULL -> shown (QGIS default); K2 hidden.
    assert "K2" not in labels and "K1" in labels and len(labels) == 3, labels
