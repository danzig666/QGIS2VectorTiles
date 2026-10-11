"""Sub-symbols drawn with the parent symbol's opacity, as QGIS draws them
(QgsMarkerLineSymbolLayer, QgsPointPatternFillSymbolLayer, pattern images
multiplied by the symbol's opacity): materialized markers, hatch lines and
SVG cells, and marker-line sprites, at the Publish zooms (0-16)."""

import json
import os
import sys

import pytest
from qgis.core import (Qgis, QgsFillSymbol, QgsLinePatternFillSymbolLayer, QgsLineSymbol,
                       QgsMarkerLineSymbolLayer, QgsMarkerSymbol, QgsPointPatternFillSymbolLayer,
                       QgsProcessingFeedback, QgsProperty, QgsSimpleLineSymbolLayer,
                       QgsSimpleMarkerSymbolLayer, QgsSingleSymbolRenderer, QgsSymbol,
                       QgsSVGFillSymbolLayer)
from qgis.PyQt.QtGui import QColor

from q2vt_fixtures import reset_project

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_materialize import EXTENT, _layer  # noqa: E402  pylint: disable=wrong-import-position

SQUARE = "POLYGON((-90 -90, 90 -90, 90 90, -90 90, -90 -90))"
LINE = "LINESTRING(-100 -60, 100 40)"
OPACITY_KEY = getattr(QgsSymbol.Property, "Opacity", getattr(QgsSymbol.Property, "PropertyOpacity", None))


def _square_marker(size=3.0):
    marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Square, size)
    marker.setColor(QColor("#d7191c"))
    marker.setStrokeStyle(0)
    return QgsMarkerSymbol([marker])


def _marker_line(unit):
    line = QgsMarkerLineSymbolLayer(True, 20 if unit == Qgis.RenderUnit.MapUnits else 6)
    line.setIntervalUnit(unit)
    line.setSubSymbol(_square_marker())
    return QgsLineSymbol([QgsSimpleLineSymbolLayer(QColor("black"), 0.5), line])


def _flatten(layer, tmp_path):
    from q2vt_plugin.src.core.rules_flattener import RulesFlattener  # pylint: disable=import-error
    from fidelity.diagnostics import DiagnosticCollector
    reset_project(layer)
    return RulesFlattener(0, 16, str(tmp_path), QgsProcessingFeedback(),
                          DiagnosticCollector()).flatten_all_rules()


def _case(name):
    if name == "marker_line":
        return "LineString", LINE, _marker_line(Qgis.RenderUnit.MapUnits), 0
    if name == "hatch":
        hatch = QgsLinePatternFillSymbolLayer()
        hatch.setDistance(10)
        hatch.setDistanceUnit(Qgis.RenderUnit.MapUnits)
        hatch.setLineAngle(45)
        hatch.subSymbol().setColor(QColor("#2b83ba"))
        return "Polygon", SQUARE, QgsFillSymbol([hatch]), 1
    if name == "point_grid":
        grid = QgsPointPatternFillSymbolLayer()
        grid.setDistanceX(20)
        grid.setDistanceY(20)
        grid.setDistanceXUnit(Qgis.RenderUnit.MapUnits)
        grid.setDistanceYUnit(Qgis.RenderUnit.MapUnits)
        grid.setSubSymbol(_square_marker())
        grid.setClipMode(Qgis.MarkerClipMode.CentroidWithin)
        return "Polygon", SQUARE, QgsFillSymbol([grid]), 0
    svg = QgsSVGFillSymbolLayer("/usr/share/qgis/svg/symbol/landuse_coniferous.svg", 20, 0)
    svg.setPatternWidthUnit(Qgis.RenderUnit.MapUnits)
    return "Polygon", SQUARE, QgsFillSymbol([svg]), 0


@pytest.mark.parametrize("name", ["marker_line", "hatch", "point_grid", "svg_fill"])
def test_materialized_sub_symbol_keeps_the_parent_opacity(plugin, tmp_path, name):
    """A 30 % symbol whose markers, hatch lines or SVG cells are exported
    as their own rules: they are drawn at 30 % too (they were opaque from
    the zoom where materialization starts)."""
    kind, wkt, symbol, geometry = _case(name)
    symbol.setOpacity(0.3)
    layer = _layer(kind, [wkt], str(tmp_path / "src.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    rules = _flatten(layer, tmp_path)
    derived = [r for r in rules if r.recipe is not None and r.get_attr("c") == geometry]
    assert derived
    for rule in derived:
        assert rule.rule.symbol().opacity() == pytest.approx(0.3), rule.rule.description()


def test_hatch_drawn_line_by_line_keeps_its_own_opacity(plugin, tmp_path):
    """QGIS draws a hatch clipped other than at the painter line by line,
    without the fill symbol's opacity."""
    _, wkt, symbol, _ = _case("hatch")
    symbol.symbolLayer(0).setClipMode(Qgis.LineClipMode.ClipToIntersection)
    symbol.setOpacity(0.3)
    layer = _layer("Polygon", [wkt], str(tmp_path / "src.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    lines = [r for r in _flatten(layer, tmp_path) if r.recipe is not None and r.get_attr("c") == 1]
    assert lines and all(r.rule.symbol().opacity() == pytest.approx(1.0) for r in lines)


def _evaluate(expression, properties):
    """A MapLibre expression of the kinds the style's opacities use."""
    if not isinstance(expression, list):
        return expression
    op, args = expression[0], expression[1:]
    if op == "get":
        return properties.get(args[0])
    values = [_evaluate(a, properties) for a in args]
    if op == "coalesce":
        return next((v for v in values if v is not None), None)
    if op == "to-number":
        return float(values[0]) if values[0] is not None else 0.0
    if op == "*":
        return values[0] * values[1]
    return {"max": max, "min": min}[op](values)


def _export(layer, tmp_path):
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-error
    reset_project(layer)
    out = tmp_path / "out"
    out.mkdir()
    result = QGIS2VectorTiles(min_zoom=0, max_zoom=16, extent=EXTENT, output_dir=str(out),
                              serve=False).convert_project_to_vector_tiles()
    return result, json.load(open(os.path.join(result, "style", "style.json"), encoding="utf-8"))


def test_data_defined_parent_opacity_reaches_the_markers(plugin, tmp_path):
    """The parent's data-defined opacity (0-100) replaces its own (a NULL
    keeps it) and multiplies the marker's (QgsSymbol::renderFeature). The
    materialized markers draw it per feature (icon-opacity over an opaque
    sprite): they were opaque for any value read from a field."""
    from osgeo import gdal
    symbol = _marker_line(Qgis.RenderUnit.MapUnits)
    symbol.deleteSymbolLayer(0)
    symbol.setOpacity(0.3)
    symbol.setDataDefinedProperty(OPACITY_KEY, QgsProperty.fromExpression('CASE WHEN "id" > 0 THEN 60 END'))
    symbol.symbolLayer(0).subSymbol().setOpacity(0.5)
    layer = _layer("LineString", [LINE, "LINESTRING(-100 60, 100 100)"], str(tmp_path / "src.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    result, style = _export(layer, tmp_path)
    markers = [l for l in style["layers"] if l["type"] == "symbol" and "icon-image" in l.get("layout", {})
               and l.get("minzoom", 0) <= 15 < l.get("maxzoom", 24)]
    assert markers
    tiles = gdal.OpenEx(os.path.join(result, "tiles.mbtiles"), open_options=["ZOOM_LEVEL=15"])
    for style_layer in markers:
        source = tiles.GetLayerByName(style_layer["source-layer"])
        if source is None:
            continue
        values = {round(_evaluate(style_layer["paint"]["icon-opacity"],
                                  {f.GetFieldDefnRef(i).GetName(): f.GetField(i)
                                   for i in range(f.GetFieldCount())}), 3)
                  for f in source}
        assert values == {0.15, 0.3}, (style_layer["id"], values)
        break
    else:
        pytest.fail("no marker layer in the zoom 15 tiles")


def test_centroid_fill_marker_keeps_the_parent_opacity(plugin, tmp_path):
    """QgsCentroidFillSymbolLayer draws its marker with the fill symbol's
    opacity multiplied in: the sprite is drawn at 30 % (it was opaque)."""
    from qgis.core import QgsCentroidFillSymbolLayer
    from PIL import Image
    centroid = QgsCentroidFillSymbolLayer()
    centroid.setSubSymbol(_square_marker(8.0))
    symbol = QgsFillSymbol([centroid])
    symbol.setOpacity(0.3)
    layer = _layer("Polygon", [SQUARE], str(tmp_path / "src.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    result, style = _export(layer, tmp_path)
    markers = [l for l in style["layers"] if l["type"] == "symbol" and "icon-image" in l.get("layout", {})]
    assert len(markers) == 1 and markers[0]["paint"]["icon-opacity"] == 1
    sprite = os.path.join(result, "style", "sprite", "sprite")
    frame = json.load(open(sprite + ".json", encoding="utf-8"))[markers[0]["layout"]["icon-image"]]
    image = Image.open(sprite + ".png").convert("RGBA").crop(
        (frame["x"], frame["y"], frame["x"] + frame["width"], frame["y"] + frame["height"]))
    assert max(a for *_rgb, a in image.getdata()) == pytest.approx(0.3 * 255, abs=3)


def test_marker_line_sprite_layer_takes_the_line_opacity(plugin, tmp_path):
    """A marker line in screen units stays a symbol layer along the line:
    its icon-opacity is the line symbol's (it was 1 under a 30 % line)."""
    symbol = _marker_line(Qgis.RenderUnit.Millimeters)
    symbol.setOpacity(0.3)
    layer = _layer("LineString", [LINE], str(tmp_path / "src.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    _, style = _export(layer, tmp_path)
    # Markers along the line (markers placed at exact positions are points
    # whose sprite carries the opacity, see the tests above).
    along = [l for l in style["layers"] if l["type"] == "symbol"
             and l.get("layout", {}).get("symbol-placement") == "line"]
    lines = [l for l in style["layers"] if l["type"] == "line"]
    assert along and lines
    assert all(l["paint"]["icon-opacity"] == pytest.approx(0.3) for l in along)
    assert all(l["paint"]["line-opacity"] == pytest.approx(0.3) for l in lines)
