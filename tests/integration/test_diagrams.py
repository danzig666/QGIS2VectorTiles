"""QGIS diagrams (pie, histogram, stacked bar, text) on the web map: the
tiles carry each feature's category values and size (a placeholder marker at
the diagram's position), and the style draws a symbol layer whose icons the
web viewer paints (resources/web_viewer/diagrams.mjs) from the diagram's
settings, sized and laid out as QGIS draws them."""

import json
import os
import sqlite3
import sys

import pytest
from qgis.core import (Qgis, QgsDiagramLayerSettings, QgsDiagramSettings, QgsFeature, QgsField,
                       QgsGeometry, QgsHistogramDiagram, QgsLinearlyInterpolatedDiagramRenderer,
                       QgsPieDiagram, QgsSingleCategoryDiagramRenderer, QgsStackedBarDiagram,
                       QgsVectorLayer)
from qgis.PyQt.QtCore import QSizeF, QVariant
from qgis.PyQt.QtGui import QColor

from publishing import mvt
from q2vt_fixtures import to_geopackage

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_end_to_end import export  # noqa: E402,F401  pylint: disable=wrong-import-position,unused-import

COLOURS = [QColor(220, 0, 0), QColor(0, 160, 0), QColor(0, 0, 220)]
VALUES = [(10, 20, 40), (5, 5, 0)]


def _layer(tmp_path, kind="Polygon", name="districts"):
    layer = QgsVectorLayer(f"{kind}?crs=EPSG:3857", name, "memory")
    layer.dataProvider().addAttributes([QgsField(n, QVariant.Double) for n in ("a", "b", "c")])
    layer.updateFields()
    features = []
    for (x, y), values in zip(((2120000, 6020000), (2122000, 6022000)), VALUES):
        feature = QgsFeature(layer.fields())
        feature.setAttributes(list(values))
        wkt = f"POINT({x} {y})" if kind == "Point" else \
            f"POLYGON(({x - 400} {y - 400}, {x + 400} {y - 400}, {x + 400} {y + 400}, {x - 400} {y + 400}, {x - 400} {y - 400}))"
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    return to_geopackage(layer, str(tmp_path / f"{name}.gpkg"))


def _settings(size=20.0):
    settings = QgsDiagramSettings()
    settings.enabled = True
    settings.categoryAttributes = ['"a"', '"b"', '"c"']
    settings.categoryColors = COLOURS
    settings.categoryLabels = ["A", "B", "C"]
    settings.size = QSizeF(size, size)
    settings.sizeType = Qgis.RenderUnit.Millimeters
    settings.barWidth = 5
    settings.penColor = QColor("black")
    settings.penWidth = 0.2
    return settings


def _diagrams(layer, diagram, interpolated=False, placement=None):
    if interpolated:
        renderer = QgsLinearlyInterpolatedDiagramRenderer()
        renderer.setLowerValue(0)
        renderer.setUpperValue(80)
        renderer.setLowerSize(QSizeF(0, 0))
        renderer.setUpperSize(QSizeF(30, 30))
        renderer.setClassificationAttributeIsExpression(True)
        renderer.setClassificationAttributeExpression('"a" + "b" + "c"')
    else:
        renderer = QgsSingleCategoryDiagramRenderer()
    renderer.setDiagram(diagram)
    renderer.setDiagramSettings(_settings())
    layer.setDiagramRenderer(renderer)
    settings = QgsDiagramLayerSettings()
    if placement is not None:
        settings.setPlacement(placement)
    layer.setDiagramLayerSettings(settings)
    return layer


def _diagram_layers(result):
    style = json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))
    return [l for l in style["layers"] if "q2vt:diagram" in (l.get("metadata") or {})], style


def _tile_features(result, source_layer):
    rows = []
    with sqlite3.connect(os.path.join(result, "tiles.mbtiles")) as conn:
        zoom = conn.execute("SELECT max(zoom_level) FROM tiles").fetchone()[0]
        for (data,) in conn.execute("SELECT tile_data FROM tiles WHERE zoom_level = ?", (zoom,)):
            layer = mvt.decode(data, geometry=True).get(source_layer)
            rows.extend((layer or {}).get("features", []))
    # One per feature (a point near a tile edge is in both tiles' buffers).
    return list({f["properties"].get("q2vt_orig_id"): f for f in rows}.values())


def test_pie_on_polygons_carries_values_and_interpolated_size(export, tmp_path):  # noqa: F811
    layer = _diagrams(_layer(tmp_path), QgsPieDiagram(), interpolated=True)
    _, result = export(layer)
    layers, style = _diagram_layers(result)
    assert len(layers) == 1
    diagram = layers[0]
    spec = diagram["metadata"]["q2vt:diagram"]
    assert spec["type"] == "pie" and spec["px"] == pytest.approx(96 / 25.4, abs=1e-3)
    assert spec["colors"][0] == "rgba(220,0,0,1.0)" and spec["rotation"] == 270 and not spec["clockwise"]
    assert spec["interpolated"] and spec["linePx"] == pytest.approx(96 / 25.4, abs=1e-3)
    image = diagram["layout"]["icon-image"]
    assert image[0] == "concat" and image[1].startswith("q2vt-diagram|")
    # Diagrams above every feature: the last style layers but the labels.
    assert style["layers"].index(diagram) == max(
        i for i, l in enumerate(style["layers"]) if "text-field" not in l.get("layout", {}))
    features = _tile_features(result, diagram["source-layer"])
    assert len(features) == 2
    props = [f["properties"] for f in features]
    values = sorted(v for p in props for k, v in p.items() if k.startswith("q2vt_property_name"))
    assert values == ["10;20;40", "5;5;0"]
    sizes = sorted(v for p in props for k, v in p.items() if k.startswith("q2vt_property_size"))
    # sqrt(70)/sqrt(80) * 30 and sqrt(10)/sqrt(80) * 30 (scaled by area)
    assert sizes == pytest.approx([30 * (10 / 80) ** 0.5, 30 * (70 / 80) ** 0.5], abs=0.01)
    assert all(f["geometry"][0] == 9 and len(f["geometry"]) == 3 for f in features)  # one point each


def test_histogram_on_points_fixed_size(export, tmp_path):  # noqa: F811
    layer = _diagrams(_layer(tmp_path, "Point", "stations"), QgsHistogramDiagram())
    _, result = export(layer)
    layers, _ = _diagram_layers(result)
    spec = layers[0]["metadata"]["q2vt:diagram"]
    assert spec["type"] == "histogram" and not spec["interpolated"]
    assert spec["width"] == 20 and spec["barWidth"] == 5 and spec["orientation"] == "up"
    assert layers[0]["layout"]["icon-image"][2] == "20.0"
    values = sorted(v for f in _tile_features(result, layers[0]["source-layer"])
                    for k, v in f["properties"].items() if k.startswith("q2vt_property_name"))
    assert values == ["10;20;40", "5;5;0"]


def test_scale_range_and_show_property(export, tmp_path):  # noqa: F811
    from qgis.core import QgsProperty  # pylint: disable=import-outside-toplevel
    layer = _diagrams(_layer(tmp_path), QgsStackedBarDiagram())
    settings = layer.diagramRenderer().diagramSettings()[0]
    settings.scaleBasedVisibility = True
    settings.minimumScale = 50000   # most zoomed out
    settings.maximumScale = 1000    # most zoomed in
    layer.diagramRenderer().setDiagramSettings(settings)
    layer_settings = layer.diagramLayerSettings()
    properties = layer_settings.dataDefinedProperties()
    properties.setProperty(QgsDiagramLayerSettings.Property.Show, QgsProperty.fromExpression('"a" > 6'))
    layer_settings.setDataDefinedProperties(properties)
    layer.setDiagramLayerSettings(layer_settings)
    _, result = export(layer, min_zoom=10, max_zoom=14)
    layers, _ = _diagram_layers(result)
    assert 12 < layers[0]["minzoom"] < 13  # 1:50 000 at this latitude
    values = [v for f in _tile_features(result, layers[0]["source-layer"])
              for k, v in f["properties"].items() if k.startswith("q2vt_property_name")]
    assert values == ["10;20;40"]  # the second feature's diagram is not shown (a = 5)
