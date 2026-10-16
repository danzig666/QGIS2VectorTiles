"""QGIS label shadows: polygon and line labels (placed by the viewer) get a
copy under them in the shadow colour, moved by the shadow's offset and
softened by its blur; point labels and background shapes are reported."""

import json
import math
import os
import sys

import pytest
from qgis.core import (Qgis, QgsPalLayerSettings, QgsTextFormat, QgsTextShadowSettings,
                       QgsVectorLayerSimpleLabeling)
from qgis.PyQt.QtGui import QColor

from q2vt_fixtures import reset_project, zoning_layer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_end_to_end import export  # noqa: E402,F401  pylint: disable=wrong-import-position,unused-import


def _labelled(layer, placement=QgsTextShadowSettings.ShadowPlacement.ShadowLowest, buffer=False):
    settings = QgsPalLayerSettings()
    settings.fieldName = "zone"
    fmt = QgsTextFormat()
    fmt.setSize(10)
    shadow = fmt.shadow()
    shadow.setEnabled(True)
    shadow.setColor(QColor(20, 30, 40))
    shadow.setOpacity(0.5)
    shadow.setOffsetDistance(2.0)
    shadow.setOffsetUnit(Qgis.RenderUnit.Millimeters)
    shadow.setOffsetAngle(135)
    shadow.setBlurRadius(1.0)
    shadow.setBlurRadiusUnit(Qgis.RenderUnit.Millimeters)
    shadow.setShadowPlacement(placement)
    fmt.setShadow(shadow)
    if buffer:
        fmt.buffer().setEnabled(True)
    settings.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    return layer


def _style(result):
    return json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))


def test_polygon_label_gets_a_shadow_copy_under_it(export, tmp_path):  # noqa: F811
    layer = _labelled(zoning_layer(path=str(tmp_path / "zones.gpkg")))
    exporter, result = export(layer)
    layers = _style(result)["layers"]
    shadows = [l for l in layers if (l.get("metadata") or {}).get("q2vt:shadow-of")]
    assert shadows
    ids = [l["id"] for l in layers]
    mm = 96 / 25.4
    for shadow in shadows:
        label = next(l for l in layers if l["id"] == shadow["metadata"]["q2vt:shadow-of"])
        assert ids.index(shadow["id"]) < ids.index(label["id"])  # drawn under its label
        assert shadow["metadata"]["q2vt:visible-polygons"] == label["metadata"]["q2vt:visible-polygons"]
        assert shadow["layout"]["text-field"] == label["layout"]["text-field"]
        dx, dy = shadow["paint"]["text-translate"]
        assert dx == pytest.approx(2 * mm * math.sin(math.radians(135)), abs=0.05)
        assert dy == pytest.approx(2 * mm * math.sin(math.radians(45)), abs=0.05)  # down-right
        assert shadow["paint"]["text-color"].replace(" ", "") in ("rgba(20,30,40,0.5)", "rgba(20,30,40,0.50)")
        assert shadow["paint"]["text-halo-blur"] == pytest.approx(min(mm / 2, 1.0), abs=0.05)  # capped
    found = exporter.diagnostics.by_code("Q2VT_LABEL_SHADOW")
    assert found and all(d.severity.value == "info" for d in found)


def test_point_labels_and_shapes_are_reported(export, tmp_path):  # noqa: F811
    from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsVectorLayer  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtCore import QVariant  # pylint: disable=import-outside-toplevel
    from q2vt_fixtures import to_geopackage  # pylint: disable=import-outside-toplevel
    points = QgsVectorLayer("Point?crs=EPSG:3857", "towns", "memory")
    points.dataProvider().addAttributes([QgsField("zone", QVariant.String)])
    points.updateFields()
    feature = QgsFeature(points.fields())
    feature.setAttributes(["Town"])
    feature.setGeometry(QgsGeometry.fromWkt("POINT(2121000 6021000)"))
    points.dataProvider().addFeatures([feature])
    points = _labelled(to_geopackage(points, str(tmp_path / "towns.gpkg")))
    exporter, result = export(points)
    assert not [l for l in _style(result)["layers"] if (l.get("metadata") or {}).get("q2vt:shadow-of")]
    assert any("point label" in d.message for d in exporter.diagnostics.by_code("Q2VT_LABEL_SHADOW"))
