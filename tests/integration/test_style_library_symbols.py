"""Symbols from shared QGIS style libraries (barbed wire, random "Fantasia"
diamonds, SVG brick fills, legend patch shapes) that used to fail or differ
on the web."""

import os
import sys
from types import SimpleNamespace

from qgis.core import (Qgis, QgsEllipseSymbolLayer, QgsFillSymbol, QgsGeometry,
                       QgsLegendPatchShape, QgsLineSymbol, QgsMarkerLineSymbolLayer,
                       QgsMarkerSymbol, QgsPointPatternFillSymbolLayer, QgsProject, QgsProperty,
                       QgsSimpleMarkerSymbolLayer, QgsSingleSymbolRenderer, QgsSymbolLayer,
                       QgsSVGFillSymbolLayer)
from qgis.PyQt.QtGui import QColor

from fidelity.diagnostics import DiagnosticCollector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_gradient_fills import _export  # noqa: E402  pylint: disable=wrong-import-position
from test_materialize import _layer  # noqa: E402  pylint: disable=wrong-import-position

SQUARE = "POLYGON((-80 -80, 80 -80, 80 80, -80 80, -80 -80))"
LINE = "LINESTRING(-90 -60, -20 50, 40 -40, 90 60)"
BRICK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="20" viewBox="0 0 40 20">'
    '<rect x="0" y="0" width="40" height="20" fill="#c0392b"/>'
    '<rect x="1" y="1" width="38" height="8" fill="#e74c3c"/></svg>')


def _exporter():
    from q2vt_plugin.src.core.maplibre_converter import QgisMapLibreStyleExporter  # pylint: disable=import-error
    from q2vt_plugin.src.core import maplibre_converter as mc  # pylint: disable=import-error
    exporter = QgisMapLibreStyleExporter.__new__(QgisMapLibreStyleExporter)
    exporter.pattern_images, exporter.marker_counter = {}, 0
    exporter.profile = mc.ExportProfile()
    exporter.context = mc.ConversionContext(DiagnosticCollector())
    mc.PropertyExtractor.context = exporter.context
    return exporter


def test_symbol_color_in_a_sub_symbol_exports(plugin, tmp_path):
    """@symbol_color (set by QGIS only while drawing) in a marker line's
    marker failed the whole layer ("Cannot convert '' to int")."""
    marker = QgsSimpleMarkerSymbolLayer()
    marker.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyStrokeColor,
                                  QgsProperty.fromExpression("@symbol_color"))
    line = QgsMarkerLineSymbolLayer(True, 6)
    line.setSubSymbol(QgsMarkerSymbol([marker]))
    symbol = QgsLineSymbol([line])
    symbol.setColor(QColor("#336699"))
    layer = _layer("LineString", [LINE], str(tmp_path / "src.gpkg"))
    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
    rendered, rules, diags = _export(layer, tmp_path)
    assert rules and all(l.featureCount() > 0 for l in rendered)
    assert not [d for d in diags.items if d.code == "Q2VT_RULE_EXPORT_FAILED"]


def _fantasia():
    diamond = QgsEllipseSymbolLayer()
    if hasattr(diamond, "setSymbolName"):
        diamond.setSymbolName("diamond")
    else:
        diamond.setShape(QgsEllipseSymbolLayer.Shape.Diamond)
    diamond.setColor(QColor(149, 183, 255, 109))
    diamond.setStrokeStyle(0)
    for key, expression in ((QgsSymbolLayer.Property.PropertyAngle, "rand(0,360)"),
                            (QgsSymbolLayer.Property.PropertyFillColor,
                             "set_color_part(@symbol_color, 'hue', rand(0,360))"),
                            (QgsSymbolLayer.Property.PropertyWidth, "randf(2,5)"),
                            (QgsSymbolLayer.Property.PropertyHeight, "randf(2,5)")):
        diamond.setDataDefinedProperty(key, QgsProperty.fromExpression(expression))
    pattern = QgsPointPatternFillSymbolLayer()
    pattern.setSubSymbol(QgsMarkerSymbol([diamond]))
    for name in ("DistanceX", "DistanceY"):
        getattr(pattern, f"set{name}")(4)
        getattr(pattern, f"set{name}Unit")(Qgis.RenderUnit.Millimeters)
    return pattern


def test_random_marker_values_are_drawn_per_marker(plugin):
    """rand() in a pattern marker: QGIS draws a new value for every marker.
    The data step leaves it alone (no per-polygon field) and the texture
    holds many markers, each with its own colour."""
    from q2vt_plugin.src.core.ddp_fetcher import DataDefinedPropertiesFetcher, is_random_only  # pylint: disable=import-error
    assert is_random_only("rand(0,360)") and is_random_only("set_color_part('1,2,3,255','hue',rand(0,9))")
    assert not is_random_only('rand(0, "size")') and not is_random_only("$area * rand(0,1)")
    symbol = QgsFillSymbol([_fantasia()])
    assert DataDefinedPropertiesFetcher(symbol, 1000).fetch() == []
    exporter = _exporter()
    cells = exporter.pattern_images[exporter._register_point_pattern(symbol.symbolLayer(0))]
    one = cells.img_1x.convert("RGBA")
    assert one.width >= 200 and one.height >= 200  # several pattern cells
    hues = {tuple(c // 32 for c in px[:3]) for px in one.getdata() if px[3] > 20}
    assert len(hues) >= 6  # random hues, not one colour


def test_svg_fill_cells_have_no_seams(plugin, tmp_path):
    """The SVG fills its whole cell, as QGIS draws it: no transparent
    anti-aliased edge between neighbouring cells."""
    svg = tmp_path / "brick.svg"
    svg.write_text(BRICK_SVG)
    fill = QgsSVGFillSymbolLayer(str(svg), 7.3)
    fill.setPatternWidthUnit(Qgis.RenderUnit.Millimeters)
    exporter = _exporter()
    cells = exporter.pattern_images[exporter._register_svg_pattern(fill)]
    for image in (cells.img_1x, cells.img_2x):
        image = image.convert("RGBA")
        edges = [image.getpixel((x, y)) for x in (0, image.width - 1) for y in range(image.height)] + \
                [image.getpixel((x, y)) for y in (0, image.height - 1) for x in range(image.width)]
        assert min(px[3] for px in edges) >= 250, image.size


def test_legend_swatch_uses_the_patch_shape(plugin, tmp_path):
    from q2vt_plugin.src.publishing import qgis_model  # pylint: disable=import-error
    layer = _layer("Polygon", [SQUARE], str(tmp_path / "src.gpkg"))
    QgsProject.instance().clear()
    QgsProject.instance().addMapLayer(layer)
    profile = SimpleNamespace(layers=[SimpleNamespace(included=True, layer_id=layer.id())],
                              view=SimpleNamespace(max_zoom=16))
    from PIL import Image

    def ink(folder):
        swatches = qgis_model.render_swatches(QgsProject.instance(), profile, str(folder))
        image = Image.open(folder / next(iter(swatches.values())).split("/")[-1]).convert("RGBA")
        return sum(1 for px in image.getdata() if px[3] > 128)
    default = ink(tmp_path / "a")
    node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
    node.setPatchShape(QgsLegendPatchShape(
        Qgis.SymbolType.Fill, QgsGeometry.fromWkt("POLYGON((0 0, 10 0, 5 10, 0 0))"), False))
    triangle = ink(tmp_path / "b")
    assert triangle < 0.75 * default, (triangle, default)


def test_rotated_svg_fill_is_seamless(plugin, tmp_path):
    """A rotated SVG fill (QGIS rotates the whole texture) becomes a cell of
    rotated tiles whose sides are whole lattice steps: drawn twice as large,
    the four quadrants are the same picture."""
    import numpy as np
    from fidelity.patterns import rotated_lattice_cell
    width, height, u, v, error = rotated_lattice_cell(30, 30, -30)
    assert error < 0.08 and width <= 512 and height <= 512
    svg = tmp_path / "brick.svg"
    svg.write_text(BRICK_SVG)
    fill = QgsSVGFillSymbolLayer(str(svg), 8)
    fill.setAngle(-30)
    exporter = _exporter()
    cells = exporter.pattern_images[exporter._register_svg_pattern(fill)]
    assert cells.img_1x.width < 600 and cells.img_2x.width == 2 * cells.img_1x.width
    from PIL import Image
    tile = Image.new("RGBA", (30, 30), (200, 40, 40, 255))
    tile.paste((240, 220, 60, 255), (0, 0, 30, 10))
    big = np.asarray(exporter._rotated_texture(tile, (2 * width, 2 * height, u, v, 0), 1)).astype(int)
    quads = [big[:height, :width], big[:height, width:], big[height:, :width], big[height:, width:]]
    assert all(np.abs(q - quads[0]).mean() < 2 for q in quads[1:])
