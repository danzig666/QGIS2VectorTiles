"""hu-hesz edition: the full regulation texts made with the LLM prompt
(docs/hu/HESZ_SZOVEG_PROMPT.md) work as the document says. Its converter
script turns the example HTML blocks into a GeoPackage table (shared blocks
expanded, problems listed), the preset finds that table, and the export
publishes each zone's whole, cleaned text."""

import os
import re
import sys

from qgis.core import QgsProject

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROMPT = os.path.join(ROOT, "docs", "hu", "HESZ_SZOVEG_PROMPT.md")


def _block(marker, language):
    text = open(PROMPT, encoding="utf-8").read()
    match = re.search(rf"<!-- {marker} -->\s*```{language}\n(.*?)```", text, re.S)
    assert match, marker
    return match.group(1)


def _convert(tmp_path, html, codes=""):
    source = tmp_path / "hesz_szoveg.html"
    source.write_text(html, encoding="utf-8")
    target = tmp_path / "hesz_szoveg.gpkg"
    script = _block("atalakito", "python")
    script = re.sub(r'^FORRAS = .*$', f"FORRAS = {str(source)!r}", script, flags=re.M)
    script = re.sub(r'^CEL = .*$', f"CEL = {str(target)!r}", script, flags=re.M)
    script = script.replace('JELEK = """\n"""', f'JELEK = """\n{codes}\n"""')
    scope = {}
    exec(compile(script, "atalakito", "exec"), scope)  # pylint: disable=exec-used
    return scope


def test_the_converter_builds_the_table_from_the_example(plugin, tmp_path, capsys):
    from q2vt_fixtures import reset_project
    reset_project()
    scope = _convert(tmp_path, _block("minta-html", "html"), "Lke-1\nKÖu\nVt")
    out = capsys.readouterr().out
    assert "Kész: 2 övezet, 3 közös blokk" in out
    assert "HIBA: nincs blokk ehhez az övezethez: Vt" in out and out.count("HIBA:") == 1
    layer = QgsProject.instance().mapLayersByName("HÉSZ övezeti előírások szövege")[0]
    rows = {f["szab_ov"]: f["eloiras_html"] for f in layer.getFeatures()}
    assert set(rows) == {"Lke-1", "KÖu"}
    lke = rows["Lke-1"]
    # Shared blocks expanded in place, in the zone block's order; no markers left.
    order = [lke.index(part) for part in ("15. § (3)", "14. § (1)", "6. § Telekalakítás", "Ha a telket műemléki",
                                          "2. melléklet")]
    assert order == sorted(order) and "<!--" not in lke
    assert "Nyúlványos telek" in rows["KÖu"] and "műemléki" not in rows["KÖu"]


def test_converter_reports_unknown_and_unused_blocks(plugin, tmp_path, capsys):
    from q2vt_fixtures import reset_project
    reset_project()
    html = ("<!-- KÖZÖS: hasznalatlan -->\n<p>x</p>\n<!-- VÉGE -->\n"
            "<!-- ÖVEZET: Lke-1 -->\n<p>y</p>\n<!-- BEILLESZT: nincs-ilyen -->\n<!-- VÉGE -->\n")
    _convert(tmp_path, html)
    out = capsys.readouterr().out
    assert "HIBA: ismeretlen BEILLESZT: nincs-ilyen" in out
    assert "HIBA: fel nem használt KÖZÖS blokk: hasznalatlan" in out


def test_preset_finds_the_texts_and_the_export_publishes_them(plugin, tmp_path):
    from q2vt_plugin.src.publishing import presets  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.models import PublicationProfile  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.parcel_report import _regulation_texts  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_preset_hu_hesz import _plan  # pylint: disable=import-error
    project = reset_project()
    _convert(tmp_path, _block("minta-html", "html"))
    _plan(project)
    preset = next(m for m in presets.available() if m.PRESET_ID == "hu-hesz")
    profile = PublicationProfile()
    notes = preset.apply(project, profile)
    info = profile.parcel_info
    texts = project.mapLayersByName("HÉSZ övezeti előírások szövege")[0]
    assert (info.text_layer_id, info.text_code_field, info.text_field) == (texts.id(), "szab_ov", "eloiras_html")
    assert info.regulation_layer_id == ""  # the texts table is not the values table
    assert any("teljes szövege" in note for note in notes), notes
    published = _regulation_texts(project, info)
    assert set(published) == {"Lke-1", "KÖu"} and "<table>" in published["Lke-1"]
