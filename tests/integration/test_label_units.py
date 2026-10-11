"""Labels whose text and offsets (or frames) use different units: a map-unit
length grows with the map, a screen one does not. Converted once at the
style's min zoom (0 in a Publish export), the mix came out ~1e5 em off or 0
at every zoom; the converter now follows each zoom, also above the tiles'
max zoom (Publish: tiles to 16, the map to 22)."""

import pytest
from qgis.core import Qgis, QgsPalLayerSettings, QgsTextBackgroundSettings, QgsTextFormat
from qgis.PyQt.QtCore import QSizeF

from fidelity.diagnostics import DiagnosticCollector

MM = 96 / 25.4
PX_PER_UNIT_Z0 = 512 / 40075016.68557849  # EPSG:3857 map unit (no CRS context)


def _mu(length, zoom):
    """A map-unit length in px at a zoom."""
    return length * PX_PER_UNIT_Z0 * 2 ** zoom


@pytest.fixture
def mc(plugin):
    from q2vt_plugin.src.core import maplibre_converter as module  # pylint: disable=import-error
    module.PropertyExtractor.context = module.ConversionContext(DiagnosticCollector())
    module.PropertyExtractor.context.reference_zoom = 0.0  # a Publish export's min zoom
    return module


@pytest.fixture
def at(mc):
    """A style value at a zoom: a step's output, a literal, a curve's value."""
    def evaluate(value, zoom):
        if isinstance(value, list) and value and value[0] == "step":
            out = value[2]
            for stop, output in zip(value[3::2], value[4::2]):
                if zoom >= stop:
                    out = output
            value = out
        if isinstance(value, list) and value and value[0] == "literal":
            value = value[1]
        if mc.ex.is_zoom_curve(value) and value[0] == "interpolate":
            return mc.ex.evaluate_zoom_curve(value, zoom)
        return value
    return evaluate


def _settings(placement, text_size, text_unit):
    settings = QgsPalLayerSettings()
    settings.placement = placement
    text_format = QgsTextFormat()
    text_format.setSize(text_size)
    text_format.setSizeUnit(text_unit)
    settings.setFormat(text_format)
    return settings


def _text_size(mc, settings):
    return mc.TextPropertyExtractor.get_text_size(settings.format(), settings, 1)


@pytest.mark.parametrize("zoom", [17, 19, 21])
def test_a_mm_offset_of_map_unit_text_keeps_its_size_at_every_zoom(mc, at, zoom):
    """6 map-unit text, 3 mm x offset: one em ratio at zoom 0 was 1.5e5 em
    (the label drawn hundreds of thousands of px away)."""
    settings = _settings(Qgis.LabelPlacement.OverPoint, 6, Qgis.RenderUnit.MapUnits)
    settings.xOffset, settings.yOffset, settings.offsetUnits = 3, 0, Qgis.RenderUnit.Millimeters
    size = _text_size(mc, settings)
    offset = mc.TextPropertyExtractor.get_text_offset(settings, size)
    # MapLibre lays the ems out at the tile zoom, multiplied by the drawn size.
    for drawn in (zoom, zoom + 0.5, zoom + 0.99):
        px = at(offset, drawn)[0] * at(size, drawn)
        assert 3 * MM / 2 ** 0.5 - 0.01 <= px <= 3 * MM * 2 ** 0.5 + 0.01
    assert at(offset, zoom + 0.5)[0] * at(size, zoom + 0.5) == pytest.approx(3 * MM, rel=1e-3)
    # Low zooms (text too small to see) stay within MapLibre's 16-bit offsets.
    assert abs(at(offset, 0)[0]) <= mc.TextPropertyExtractor.MAX_TEXT_OFFSET_EM
    # Over point: drawn as text-translate instead, the mm offset at every zoom.
    translate = mc.TextPropertyExtractor.get_text_offset_translate(settings, 0)
    assert translate == pytest.approx([3 * MM, 0], abs=1e-4)


@pytest.mark.parametrize("zoom", [15, 17, 19, 21])
def test_a_map_unit_distance_of_screen_text_grows_with_the_map(mc, at, zoom):
    """10 pt text, around point at 20 map units / above a line at 10 map units /
    over point offset 30, -30 map units: taken at zoom 0 they were ~1e-5 em,
    the label on its point at every zoom."""
    middle = zoom + 0.5
    around = _settings(Qgis.LabelPlacement.AroundPoint, 10, Qgis.RenderUnit.Points)
    around.dist, around.distUnits = 20, Qgis.RenderUnit.MapUnits
    size = _text_size(mc, around)
    radial = mc.TextPropertyExtractor.get_text_radial_offset(around, size)
    assert at(radial, middle) * size == pytest.approx(_mu(20, middle), rel=1e-3)

    line = _settings(Qgis.LabelPlacement.Line, 10, Qgis.RenderUnit.Points)
    line_settings = line.lineSettings()
    line_settings.setPlacementFlags(Qgis.LabelLinePlacementFlags(2))  # above the line
    line.setLineSettings(line_settings)
    line.dist, line.distUnits = 10, Qgis.RenderUnit.MapUnits
    above = mc.TextPropertyExtractor.get_line_text_offset(line, size)
    assert at(above, middle)[0] == 0
    expected = min(_mu(10, middle) / size, mc.TextPropertyExtractor.MAX_TEXT_OFFSET_EM)
    assert -at(above, middle)[1] == pytest.approx(expected, rel=1e-3)

    over = _settings(Qgis.LabelPlacement.OverPoint, 10, Qgis.RenderUnit.Points)
    over.xOffset, over.yOffset, over.offsetUnits = 30, -30, Qgis.RenderUnit.MapUnits
    offset = mc.TextPropertyExtractor.get_text_offset(over, size)
    expected = min(_mu(30, middle) / size, mc.TextPropertyExtractor.MAX_TEXT_OFFSET_EM)
    assert at(offset, middle) == pytest.approx([expected, -expected], rel=1e-3)


def test_offsets_in_the_units_of_the_text_stay_one_number(mc):
    """Offset and text in the same kind of unit scale alike: one em value."""
    settings = _settings(Qgis.LabelPlacement.AroundPoint, 6, Qgis.RenderUnit.MapUnits)
    settings.dist, settings.distUnits = 3, Qgis.RenderUnit.MapUnits
    assert mc.TextPropertyExtractor.get_text_radial_offset(
        settings, _text_size(mc, settings)) == pytest.approx(0.5, abs=1e-4)
    settings = _settings(Qgis.LabelPlacement.OverPoint, 12, Qgis.RenderUnit.Pixels)
    settings.xOffset, settings.yOffset, settings.offsetUnits = 6, 3, Qgis.RenderUnit.Pixels
    assert mc.TextPropertyExtractor.get_text_offset(settings, 12) == pytest.approx([0.5, 0.25])


@pytest.mark.parametrize("zoom", [14, 18, 21])
def test_around_point_polygon_distance_in_map_units_is_not_rounded_away(mc, zoom):
    """The viewer scales q2vt:label-around by 2^(zoom - its zoom); taken at
    zoom 0 a 10 map-unit distance rounded to 0 px."""
    settings = _settings(Qgis.LabelPlacement.AroundPoint, 10, Qgis.RenderUnit.Points)
    settings.dist, settings.distUnits = 10, Qgis.RenderUnit.MapUnits
    around = mc.QgisMapLibreStyleExporter._label_around(settings, "AroundPoint")
    assert around["px"] * 2 ** (zoom - around["zoom"]) == pytest.approx(_mu(10, zoom), rel=1e-3)


def _exporter(mc, maxzoom=16):
    exporter = mc.QgisMapLibreStyleExporter.__new__(mc.QgisMapLibreStyleExporter)
    exporter.pattern_images, exporter.marker_counter, exporter.maxzoom = {}, 0, maxzoom
    exporter.profile = mc.ExportProfile()
    exporter.context = mc.PropertyExtractor.context
    return exporter


def _frame(size_type, size, size_unit, stroke, stroke_unit):
    frame = QgsTextBackgroundSettings()
    frame.setEnabled(True)
    frame.setType(QgsTextBackgroundSettings.ShapeType.ShapeRectangle)
    frame.setSizeType(size_type)
    frame.setSize(QSizeF(*size))
    frame.setSizeUnit(size_unit)
    frame.setStrokeWidth(stroke)
    frame.setStrokeWidthUnit(stroke_unit)
    return frame


@pytest.mark.parametrize("maxzoom", [10, 16, 19])
def test_frames_of_map_unit_text_ramp_at_every_zoom_above_the_tiles(mc, maxzoom):
    """MapLibre lays overscaled tiles out again at each zoom: a frame fitted
    to map-unit text needs its 0.5 -> 1 icon-size ramp at every zoom to 24
    (one curve above maxzoom + 4 was 8x at zoom 20 for tiles to 16)."""
    exporter = _exporter(mc, maxzoom)
    settings = _settings(Qgis.LabelPlacement.OverPoint, 1, Qgis.RenderUnit.MapUnits)
    frame = _frame(QgsTextBackgroundSettings.SizeType.SizeBuffer, (0.25, 0.25),
                   Qgis.RenderUnit.MapUnits, 0.1, Qgis.RenderUnit.MapUnits)
    layer_def = {"id": "lbl", "minzoom": 0, "maxzoom": 24,
                 "layout": {"text-size": _text_size(mc, settings), "text-line-height": 1.2},
                 "paint": {}}
    exporter._apply_icon_from_background(layer_def, frame, "s", settings)
    parts = exporter._line_label_zoom_split(layer_def)
    assert [part["minzoom"] for part in parts] == list(range(0, 24))
    for zoom, part in enumerate(parts):
        assert part["maxzoom"] == zoom + 1
        assert part["layout"]["icon-size"] == ["interpolate", ["exponential", 2], ["zoom"],
                                               zoom, 0.5, zoom + 1, 1.0]
    # A map-unit border drawn for each zoom, also above the tiles' max zoom.
    assert len({part["layout"]["icon-image"] for part in parts}) > 20
    curve = exporter._text_fit_icon_size()
    for zoom in (20, 21.5, 22.9):
        assert 0.5 <= mc.ex.evaluate_zoom_curve(curve, zoom) <= 1.0


def test_a_fixed_map_unit_frame_of_screen_text_scales_with_the_map(mc):
    """10 pt text in a fixed 80 x 30 map-unit frame: drawn at zoom 0 its
    sprite was 1 x 1 px with no icon-size (no frame at any zoom)."""
    exporter = _exporter(mc)
    settings = _settings(Qgis.LabelPlacement.OverPoint, 10, Qgis.RenderUnit.Points)
    frame = _frame(QgsTextBackgroundSettings.SizeType.SizeFixed, (80, 30),
                   Qgis.RenderUnit.MapUnits, 2, Qgis.RenderUnit.MapUnits)
    layer_def = {"id": "lbl", "layout": {"text-size": 13.3333}, "paint": {}}
    exporter._apply_icon_from_background(layer_def, frame, "s", settings)
    image = exporter.pattern_images[layer_def["layout"]["icon-image"]].img_1x
    for zoom in (15, 17.5, 19, 21):
        scale = mc.ex.evaluate_zoom_curve(layer_def["layout"]["icon-size"], zoom)
        # QGIS's box, its border centred on the outline (half of it outside)
        assert image.width * scale == pytest.approx(_mu(80 + 2, zoom), rel=0.02)
        assert image.height * scale == pytest.approx(_mu(30 + 2, zoom), rel=0.02)
    assert "icon-text-fit" not in layer_def["layout"]


@pytest.mark.parametrize("zoom", [15, 17, 19, 21])
def test_a_map_unit_buffer_frame_of_screen_text_grows_with_the_map(mc, at, zoom):
    """10 pt text, a 5 x 3 map-unit buffer with a 1 map-unit border: padding
    and border at zoom 0 were 0 px at every zoom (a bare box hugging the text)."""
    exporter = _exporter(mc)
    settings = _settings(Qgis.LabelPlacement.OverPoint, 10, Qgis.RenderUnit.Points)
    frame = _frame(QgsTextBackgroundSettings.SizeType.SizeBuffer, (5, 3),
                   Qgis.RenderUnit.MapUnits, 1, Qgis.RenderUnit.MapUnits)
    layer_def = {"id": "lbl", "minzoom": 0, "maxzoom": 24,
                 "layout": {"text-size": 13.3333, "text-line-height": 1.2}, "paint": {}}
    exporter._apply_icon_from_background(layer_def, frame, "s", settings)
    layout = layer_def["layout"]
    padding = at(layout["icon-text-fit-padding"], zoom)
    low = at(layout["icon-text-fit-padding"], 0)  # the buffer ~0 px: the font's own part
    buffer_x, buffer_y = _mu(5, zoom + 0.5), _mu(3, zoom + 0.5)
    assert padding[1] == padding[3] == pytest.approx(buffer_x, rel=1e-3, abs=1e-3)
    # top and bottom: the buffer plus QGIS's ascent / descent around MapLibre's line box
    assert padding[0] == pytest.approx(buffer_y + low[0], abs=2e-3)
    assert padding[2] == pytest.approx(buffer_y + low[2], abs=2e-3)
    # The border of the zoom's middle, in the frame image's fixed margin.
    meta = exporter.pattern_images[at(layout["icon-image"], zoom)].metadata
    assert meta["content"][0] == pytest.approx(_mu(1, zoom + 0.5) / 2, abs=0.01)
    assert "icon-size" not in layout


@pytest.mark.parametrize("zoom", [15, 19, 21.5, 22])
def test_a_map_unit_over_point_offset_of_screen_text_is_a_px_translate(mc, at, zoom):
    """10 pt text, offset 15, -10 map units: in ems (text-offset, baked into
    each glyph's 16-bit position) a long label wrapped round to the far side
    of its point from zoom 21.5; as text-translate px it is exact at every
    zoom, whole ones too, with no limit."""
    settings = _settings(Qgis.LabelPlacement.OverPoint, 10, Qgis.RenderUnit.Points)
    settings.xOffset, settings.yOffset, settings.offsetUnits = 15, -10, Qgis.RenderUnit.MapUnits
    translate = mc.TextPropertyExtractor.get_text_offset_translate(settings, 0)
    assert translate[:3] == ["interpolate", ["exponential", 2], ["zoom"]]
    stops = list(zip(translate[3::2], (value[1] for value in translate[4::2])))
    x = mc.ex.exponential_zoom_curve([(z, v[0]) for z, v in stops])
    y = mc.ex.exponential_zoom_curve([(z, v[1]) for z, v in stops])
    assert mc.ex.evaluate_zoom_curve(x, zoom) == pytest.approx(_mu(15, zoom), rel=1e-4)
    assert mc.ex.evaluate_zoom_curve(y, zoom) == pytest.approx(_mu(-10, zoom), rel=1e-4)
    # Turned with a fixed label rotation, as MapLibre turns text-offset.
    turned = mc.TextPropertyExtractor.get_text_offset_translate(settings, 90)
    stops = list(zip(turned[3::2], (value[1] for value in turned[4::2])))
    x = mc.ex.exponential_zoom_curve([(z, v[0]) for z, v in stops])
    y = mc.ex.exponential_zoom_curve([(z, v[1]) for z, v in stops])
    assert mc.ex.evaluate_zoom_curve(x, zoom) == pytest.approx(_mu(10, zoom), rel=1e-4)
    assert mc.ex.evaluate_zoom_curve(y, zoom) == pytest.approx(_mu(15, zoom), rel=1e-4)
    # A data-defined rotation: kept as text-offset ems.
    assert mc.TextPropertyExtractor.get_text_offset_translate(settings, ["get", "r"]) is None


def test_per_zoom_label_offsets_are_cut_to_the_layer_zooms(mc):
    """A step of per-zoom ems keeps only the stops within the layer's zooms."""
    step = ["step", ["zoom"], ["literal", [0, 1]], 5, ["literal", [0, 2]], 14, ["literal", [0, 3]],
            15, ["literal", [0, 4]], 20, ["literal", [0, 5]]]
    trim = mc.QgisMapLibreStyleExporter._layer_zoom_step
    assert trim({"minzoom": 14, "maxzoom": 20}, step) == [
        "step", ["zoom"], ["literal", [0, 3]], 15, ["literal", [0, 4]]]
    assert trim({"minzoom": 15, "maxzoom": 18}, step) == [0, 4]
    assert trim({}, step) == step
    assert trim({"minzoom": 3}, [0, 1]) == [0, 1]


def test_a_map_unit_border_with_data_defined_colours_grows_around_screen_text(mc, at):
    """10 pt text, 3 x 2 map-unit buffer, 0.5 map-unit border, data-defined
    fill colour: the colour variants kept the border of zoom 0 (none)."""
    from qgis.core import QgsProperty
    exporter = _exporter(mc)
    exporter._distinct_values = lambda source_layer, fields: [("#00ff00",), ("#0000ff",)]
    settings = _settings(Qgis.LabelPlacement.OverPoint, 10, Qgis.RenderUnit.Points)
    settings.dataDefinedProperties().setProperty(
        QgsPalLayerSettings.Property.ShapeFillColor, QgsProperty.fromField(f"{mc._FIELD_PREFIX}_fill"))
    frame = _frame(QgsTextBackgroundSettings.SizeType.SizeBuffer, (3, 2),
                   Qgis.RenderUnit.MapUnits, 0.5, Qgis.RenderUnit.MapUnits)
    layer_def = {"id": "lbl", "minzoom": 0, "maxzoom": 24, "source-layer": "p",
                 "layout": {"text-size": 13.3333, "text-line-height": 1.2}, "paint": {}}
    exporter._apply_icon_from_background(layer_def, frame, "s", settings)
    image = layer_def["layout"]["icon-image"]
    assert image[0] == "step"
    for zoom in (17, 19, 21):
        variants = at(image, zoom)
        assert variants[0] == "match"
        for name in variants[3::2] + variants[-1:]:
            meta = exporter.pattern_images[name].metadata
            assert meta["content"][0] == pytest.approx(_mu(0.5, zoom + 0.5) / 2, abs=0.01)
    # The zoom-0 images of the variants are not left in the sprite.
    used = {n for v in [image[2]] + image[4::2] for n in v[3::2] + v[-1:]}
    assert set(exporter.pattern_images) == used
