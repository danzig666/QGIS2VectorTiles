"""Basemap label glyphs with the installed fonts: a font whose bold-ish face
is a family of its own (older Open Sans: "Open Sans Semibold") must still
give every basemap fontstack its glyphs (the export failed with
"Glyphs for 'Open Sans Semibold' could not be generated")."""

import os

import pytest


def test_every_basemap_fontstack_gets_glyphs(plugin, tmp_path):
    from qgis.PyQt.QtGui import QFontDatabase
    from q2vt_plugin.src.publishing import basemap  # pylint: disable=import-error
    try:
        families = list(QFontDatabase().families())
    except TypeError:
        families = list(QFontDatabase.families())
    family = next((f for f in basemap.FAMILIES if f in families), None)
    if family is None:
        pytest.skip("no basemap font installed")
    used = basemap.generate_glyphs(set("Hamilton 123"), str(tmp_path))
    assert set(used) == set(basemap.FONTSTACKS.values())
    for stack in used:
        assert os.listdir(tmp_path / stack), stack
