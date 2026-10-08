"""hu-hesz edition: the zone regulation table made with the LLM prompt
(docs/hu/HESZ_ELOIRAS_PROMPT.md) works as the prompt says. Its example CSV
has the prompt's columns, loads into QGIS as a delimited text table without
geometry (numbers detected), is found by the preset, and every column gets
a Hungarian title; the decree column links to the published document."""

import csv
import io
import os
import re
import sys

from qgis.core import QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QUrl

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROMPT = os.path.join(ROOT, "docs", "hu", "HESZ_ELOIRAS_PROMPT.md")


def _text():
    with open(PROMPT, encoding="utf-8") as handle:
        return handle.read()


def _example():
    match = re.search(r"<!-- minta-csv -->\s*```csv\n(.*?)```", _text(), re.S)
    assert match, "the example CSV block is missing"
    return match.group(1)


def _prompt_columns():
    """The column line of prompt 1 (=== A KIMENET OSZLOPAI ===)."""
    match = re.search(r"=== A KIMENET OSZLOPAI[^\n]*\n\n([a-z_,]+)\n", _text())
    assert match
    return match.group(1).split(",")


def test_example_has_the_prompt_columns():
    columns = _prompt_columns()
    rows = list(csv.reader(io.StringIO(_example())))
    assert rows[0] == columns and len(columns) == 22
    assert all(len(row) == len(columns) for row in rows[1:]), [len(r) for r in rows]
    codes = [row[0] for row in rows[1:]]
    assert len(codes) == len(set(codes))
    # The column table of the document lists the same columns, in order.
    table = re.findall(r"^\| `([a-z_]+)` \|", _text(), re.M)
    assert table == columns
    assert "minden sorban 22 mező" in _text() and "pontosan 22 mező" in _text()


def test_example_table_is_found_and_titled(plugin, tmp_path):
    from q2vt_plugin.src.publishing import presets  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.models import PublicationProfile  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.parcel_report import _regulations  # pylint: disable=import-error
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_preset_hu_hesz import _plan  # pylint: disable=import-error
    preset = next(m for m in presets.available() if m.PRESET_ID == "hu-hesz")
    path = tmp_path / "hesz_eloirasok.csv"
    path.write_text(_example(), encoding="utf-8")
    uri = (QUrl.fromLocalFile(str(path)).toString()
           + "?type=csv&delimiter=,&quote=%22&escape=%22&detectTypes=yes&geomType=none&encoding=UTF-8")
    table = QgsVectorLayer(uri, "HÉSZ övezeti előírások", "delimitedtext")
    assert table.isValid() and table.featureCount() == 6
    project = QgsProject()
    _plan(project)
    project.addMapLayer(table)
    numeric = {f.name() for f in table.fields() if f.isNumeric()}
    assert {"min_ter", "beep_szaz", "max_mag", "min_zold"} <= numeric
    profile = PublicationProfile()
    notes = preset.apply(project, profile)
    info = profile.parcel_info
    assert info.regulation_layer_id == table.id() and info.regulation_code_field == "szab_ov", notes
    titles = {f.field: f.alias for f in info.regulation_fields}
    assert set(titles) == set(_prompt_columns()) - {"szab_ov"}
    untitled = [field for field, title in titles.items() if title == field]
    assert not untitled  # every column has a Hungarian title
    assert titles["max_epitm_mag"] == "Legnagyobb építménymagasság (m)"
    kinds = {f.field: f.type for f in info.regulation_fields}
    assert kinds["rendelet"] == "string"  # a document title (links in the viewer), not URLs
    regulations = _regulations(project, info)
    assert set(regulations) == {"Lke-1", "Lke-2", "Vt", "Gksz", "Má", "KÖu"}
    assert regulations["Lke-1"]["beep_szaz"] == 30 and regulations["Lke-1"]["max_mag"] == 5.5
    assert regulations["Vt"]["elokert"] == "kialakult"
    assert regulations["Lke-1"]["rendelet"] == "Helyi építési szabályzat"


def test_aliases_win_over_the_known_titles(plugin):
    from q2vt_plugin.src.publishing.presets import hu_hesz  # pylint: disable=import-error
    table = QgsVectorLayer("None?field=szab_ov:string&field=beep_szaz:double&field=sajat:string",
                           "HÉSZ övezeti előírások", "memory")
    table.setFieldAlias(table.fields().indexOf("beep_szaz"), "Beépítettség (%)")
    fields = {f.field: f.alias for f in hu_hesz._regulation_fields(table, "szab_ov")}  # pylint: disable=protected-access
    assert fields == {"beep_szaz": "Beépítettség (%)", "sajat": "sajat"}


def test_the_table_wins_over_a_zone_layer_with_the_same_fields(plugin):
    """A zone polygon layer named "Övezeti jelek" with szab_ov and two other
    fields is no regulation table; the zone popup gets the code even when the
    zone layer is not (yet) ticked for publishing."""
    from q2vt_plugin.src.publishing import presets  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.models import LayerConfig, PublicationProfile  # pylint: disable=import-error
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_preset_hu_hesz import _layer, _plan  # pylint: disable=import-error
    preset = next(m for m in presets.available() if m.PRESET_ID == "hu-hesz")
    project = QgsProject()
    layers = _plan(project)
    square = "POLYGON((650000 230000, 650100 230000, 650100 230100, 650000 230100, 650000 230000))"
    _layer(project, "Övezeti jelek", "Polygon", ["szab_ov:string", "felirat:string", "meret:double"], square)
    table = QgsVectorLayer("None?field=szab_ov:string&field=beep_szaz:double&field=max_mag:double",
                           "HÉSZ övezeti előírások", "memory")
    project.addMapLayer(table)
    profile = PublicationProfile()
    profile.layers = [LayerConfig(layer.id(), included=False) for layer in project.mapLayers().values()]
    preset.apply(project, profile)
    info = profile.parcel_info
    assert info.regulation_layer_id == table.id()
    zoning = profile.layer(info.zoning_layer_id)
    assert zoning.popup_fields and zoning.popup_fields[0].field == "szab_ov"
    assert info.zoning_layer_id == layers["colours"].id()
