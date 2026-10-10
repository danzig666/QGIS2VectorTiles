"""Bold/italic labels: the B/I buttons of the text format (forcedBold,
forcedItalic) and a bold font weight leave the font's style name empty;
the web style must still use the bold/italic face, not the regular one."""

import pytest
from qgis.core import QgsTextFormat
from qgis.PyQt.QtGui import QFont

from fidelity.diagnostics import DiagnosticCollector


@pytest.fixture
def extractor(plugin):
    from q2vt_plugin.src.core import maplibre_converter as mc  # pylint: disable=import-error
    mc.PropertyExtractor.context = mc.ConversionContext(DiagnosticCollector())
    return mc.TextPropertyExtractor


def _format(bold=False, italic=False, forced_bold=False, forced_italic=False, style="", family="DejaVu Sans"):
    font = QFont(family)
    font.setBold(bold)
    font.setItalic(italic)
    if style:
        font.setStyleName(style)
    text_format = QgsTextFormat()
    text_format.setFont(font)
    if forced_bold:  # setForcedBold(False) would also clear the font's own weight
        text_format.setForcedBold(True)
    if forced_italic:
        text_format.setForcedItalic(True)
    return text_format


@pytest.mark.parametrize("kwargs, expected", [
    ({}, "DejaVu Sans Book"),
    ({"bold": True}, "DejaVu Sans Bold"),
    ({"forced_bold": True}, "DejaVu Sans Bold"),
    ({"style": "Bold"}, "DejaVu Sans Bold"),
    # DejaVu Sans Mono: the family installed with all four faces in the test image
    ({"family": "DejaVu Sans Mono", "forced_italic": True}, "DejaVu Sans Mono Oblique"),
    ({"family": "DejaVu Sans Mono", "bold": True, "italic": True}, "DejaVu Sans Mono Bold Oblique"),
])
def test_bold_and_italic_labels_use_their_face(extractor, kwargs, expected):
    from qgis.PyQt.QtGui import QFontDatabase
    family = kwargs.get("family", "DejaVu Sans")
    if expected[len(family) + 1:] not in QFontDatabase().styles(family):
        pytest.skip(f"{expected} is not installed")
    assert extractor.get_text_font(_format(**kwargs)) == expected



@pytest.mark.parametrize("kind, spacing, size, expected", [
    (QFont.SpacingType.AbsoluteSpacing, 3.0, 15.0, 0.2),     # letter-spaced town name: 3 pt at 15 pt
    (QFont.SpacingType.AbsoluteSpacing, -0.5, 10.0, -0.05),  # tightened
    (QFont.SpacingType.AbsoluteSpacing, 0.0, 10.0, 0),
    (QFont.SpacingType.PercentageSpacing, 100.0, 10.0, 0),   # 100 %: none
    (QFont.SpacingType.PercentageSpacing, 120.0, 10.0, 0.1),
])
def test_letter_spacing_becomes_ems(extractor, kind, spacing, size, expected):
    """QGIS letter spacing (absolute, in the text size's units) is
    text-letter-spacing in ems; it was always 0 (letter-spaced labels
    narrower on the web)."""
    text_format = _format()
    font = text_format.font()
    font.setLetterSpacing(kind, spacing)
    text_format.setFont(font)
    text_format.setSize(size)
    assert extractor.get_text_letter_spacing(text_format) == pytest.approx(expected)


def test_unset_multiline_alignment_of_an_old_project_is_left(extractor):
    """Projects from older QGIS versions can store multilineAlign="4294967295"
    (-1, unset). PyQGIS refuses to read it (ValueError: -1 is not a valid
    Qgis.LabelMultiLineAlignment), which stopped the export; QGIS draws such
    labels left aligned."""
    from qgis.core import QgsPalLayerSettings, QgsReadWriteContext
    from qgis.PyQt.QtXml import QDomDocument
    settings = QgsPalLayerSettings()
    settings.fieldName = "name"
    doc = QDomDocument()
    element = settings.writeXml(doc, QgsReadWriteContext())
    text_format = element.firstChildElement("text-format")
    text_format.setAttribute("multilineAlign", "4294967295")
    old = QgsPalLayerSettings()
    old.readXml(element, QgsReadWriteContext())
    try:
        old.multilineAlign  # pylint: disable=pointless-statement
    except ValueError:
        pass  # the PyQGIS of this QGIS cannot read it either
    assert extractor.get_text_justify(old) == "left"
    assert extractor.get_text_anchor(old) in ("center", "bottom-right", "bottom", "bottom-left", "right",
                                              "left", "top-right", "top", "top-left")


@pytest.mark.parametrize("weight, italic", [
    (QFont.Weight.DemiBold, False), (QFont.Weight.DemiBold, True),
    (QFont.Weight.Medium, True), (QFont.Weight.Light, False)])
def test_weights_between_the_four_faces_use_the_face_qgis_draws(extractor, weight, italic):
    """A DemiBold, Medium or Light font without a style name: QGIS draws the
    face Qt matches (Semibold, Light); the web used the nearest of Regular,
    Italic, Bold and Bold Italic."""
    from qgis.PyQt.QtGui import QFontInfo
    from q2vt_plugin.src.core.glyphs_generator import GlyphGenerator  # pylint: disable=import-error
    font = QFont("Open Sans")
    font.setWeight(weight)
    font.setItalic(italic)
    info = QFontInfo(font)
    if info.family() != "Open Sans" or info.styleName() in ("Regular", "Italic", "Bold", "Bold Italic"):
        pytest.skip("the Open Sans Semibold / Light faces are not installed")
    text_format = QgsTextFormat()
    text_format.setFont(font)
    assert extractor.get_text_font(text_format) == \
        GlyphGenerator.resolve_fontstack("Open Sans", info.styleName())


def _framed_label(family, size_pt=7.5):
    """A label with a rectangle background sized as a 1.4 x 0.7 mm buffer."""
    from qgis.core import Qgis, QgsPalLayerSettings, QgsTextBackgroundSettings
    from qgis.PyQt.QtCore import QSizeF
    text_format = _format(family=family)
    text_format.setSize(size_pt)
    frame = QgsTextBackgroundSettings()
    frame.setEnabled(True)
    frame.setType(QgsTextBackgroundSettings.ShapeType.ShapeRectangle)
    frame.setSizeType(QgsTextBackgroundSettings.SizeType.SizeBuffer)
    frame.setSize(QSizeF(1.4, 0.7))
    frame.setSizeUnit(Qgis.RenderUnit.Millimeters)
    text_format.setBackground(frame)
    settings = QgsPalLayerSettings()
    settings.setFormat(text_format)
    return settings, frame


def _frame_exporter():
    from q2vt_plugin.src.core import maplibre_converter as mc  # pylint: disable=import-error
    exporter = mc.QgisMapLibreStyleExporter.__new__(mc.QgisMapLibreStyleExporter)
    exporter.pattern_images, exporter.marker_counter, exporter.maxzoom = {}, 0, 16
    exporter.profile = mc.ExportProfile()
    exporter.context = mc.ConversionContext(DiagnosticCollector())
    mc.PropertyExtractor.context = exporter.context
    return exporter


@pytest.mark.parametrize("family", ["DejaVu Sans", "Open Sans"])
def test_frame_padding_wraps_the_ascent_and_descent_like_qgis(plugin, family):
    """QGIS fits a buffer-sized label background around the font's ascent
    and descent; MapLibre fits icon-text-fit around its own line box
    (text-line-height ems, the baseline 7/24 em below its middle). The
    difference goes into the top and bottom padding: with Open Sans the
    frame was 1.6 px too short at 10 px, all of it above the text."""
    from qgis.PyQt.QtGui import QFontDatabase, QFontMetricsF
    if family not in QFontDatabase().families():
        pytest.skip(f"{family} is not installed")
    settings, frame = _framed_label(family)
    layer_def = {"layout": {"text-size": 10, "text-line-height": 1.2}, "paint": {}}
    _frame_exporter()._apply_icon_from_background(layer_def, frame, "s", settings)
    top, right, bottom, left = layer_def["layout"]["icon-text-fit-padding"]
    font = QFont(settings.format().font())
    font.setPixelSize(1000)
    metrics = QFontMetricsF(font)
    ascent, descent = metrics.ascent() / 1000, metrics.descent() / 1000
    buffer_x, buffer_y = 1.4 * 96 / 25.4, 0.7 * 96 / 25.4
    assert right == left == pytest.approx(buffer_x, abs=1e-3)
    assert top == pytest.approx(buffer_y + (ascent - 0.6 - 7 / 24) * 10, abs=1e-3)
    assert bottom == pytest.approx(buffer_y + (descent - 0.6 + 7 / 24) * 10, abs=1e-3)
    # The frame is as high as QGIS's: ascent + descent and both buffers.
    assert 1.2 * 10 + top + bottom == pytest.approx((ascent + descent) * 10 + 2 * buffer_y, abs=1e-3)


def test_frame_padding_of_map_unit_text_keeps_the_buffer(plugin):
    """Map-unit text (a text-size zoom curve) keeps the bare buffer,
    doubled for MapLibre's text fit at tile zoom + 1."""
    settings, frame = _framed_label("DejaVu Sans")
    size = ["interpolate", ["exponential", 2], ["zoom"], 10, 1, 20, 1024]
    layer_def = {"layout": {"text-size": size, "text-line-height": 1.2}, "paint": {}}
    _frame_exporter()._apply_icon_from_background(layer_def, frame, "s", settings)
    buffer_x, buffer_y = 1.4 * 96 / 25.4, 0.7 * 96 / 25.4
    assert layer_def["layout"]["icon-text-fit-padding"] == pytest.approx(
        [2 * buffer_y, 2 * buffer_x, 2 * buffer_y, 2 * buffer_x], abs=1e-3)
