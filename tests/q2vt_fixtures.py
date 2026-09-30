"""Programmatic QGIS fixtures (no binary project files)."""

from qgis.core import (QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransformContext,
                       QgsVectorFileWriter, QgsFeature, QgsField, QgsFillSymbol,
                       QgsGeometry, QgsProject, QgsRendererCategory, QgsRuleBasedRenderer,
                       QgsVectorLayer)
from qgis.PyQt.QtCore import QVariant

# Four 1 km squares around Budapest in EPSG:3857, one crossing tile corners.
SQUARES = [
    ("A", "K1", "POLYGON((2120000 6020000, 2121000 6020000, 2121000 6021000, 2120000 6021000, 2120000 6020000))"),
    ("B", "K2", "POLYGON((2121000 6020000, 2122000 6020000, 2122000 6021000, 2121000 6021000, 2121000 6020000))"),
    ("C", None, "POLYGON((2120000 6021000, 2121000 6021000, 2121000 6022000, 2120000 6022000, 2120000 6021000),(2120300 6021300, 2120700 6021300, 2120700 6021700, 2120300 6021700, 2120300 6021300))"),
    ("D", "Lk", "POLYGON((2121000 6021000, 2122000 6021000, 2122000 6022000, 2121000 6022000, 2121000 6021000))"),
]


def to_geopackage(layer: QgsVectorLayer, path: str) -> QgsVectorLayer:
    """Persist a fixture layer (the exporter re-opens sources by URI)."""
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = layer.name()
    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, path, QgsCoordinateTransformContext(), options)
    if result[0] != QgsVectorFileWriter.NoError:
        raise RuntimeError(f"Could not write fixture {path}: {result}")
    saved = QgsVectorLayer(f"{path}|layername={layer.name()}", layer.name(), "ogr")
    assert saved.isValid() and saved.featureCount() == layer.featureCount()
    return saved


def zoning_layer(name="zoning", crs="EPSG:3857", path=None) -> QgsVectorLayer:
    """Zoning squares; written to ``path`` (GeoPackage) when given."""
    layer = QgsVectorLayer(f"Polygon?crs={crs}", name, "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("id", QVariant.String), QgsField("zone", QVariant.String),
                            QgsField("width", QVariant.Double)])
    layer.updateFields()
    features = []
    for i, (fid, zone, wkt) in enumerate(SQUARES):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([fid, zone, 0.2 * (i + 1)])
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        features.append(feature)
    provider.addFeatures(features)
    layer.updateExtents()
    return to_geopackage(layer, path) if path else layer


def categorized(layer: QgsVectorLayer) -> QgsVectorLayer:
    categories = []
    for value, color in (("K1", "255,0,0"), ("K2", "0,255,0"), ("Lk", "0,0,255")):
        categories.append(QgsRendererCategory(
            value, QgsFillSymbol.createSimple({"color": color}), value, value != "Lk"))
    layer.setRenderer(QgsCategorizedSymbolRenderer("zone", categories))
    return layer


def rule_based(layer: QgsVectorLayer, rules) -> QgsVectorLayer:
    """rules: [(label, filter, min_scale, max_scale, active, color)]"""
    root = QgsRuleBasedRenderer.Rule(None)
    for label, flt, min_scale, max_scale, active, color in rules:
        rule = QgsRuleBasedRenderer.Rule(QgsFillSymbol.createSimple({"color": color}),
                                         0, 0, flt, label)
        rule.setMinimumScale(min_scale)
        rule.setMaximumScale(max_scale)
        rule.setActive(active)
        root.appendChild(rule)
    layer.setRenderer(QgsRuleBasedRenderer(root))
    return layer


def reset_project(*layers):
    project = QgsProject.instance()
    project.clear()
    project.setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    for layer in layers:
        project.addMapLayer(layer)
    return project
