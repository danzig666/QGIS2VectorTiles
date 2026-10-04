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

