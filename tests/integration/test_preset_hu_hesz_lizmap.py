"""hu-hesz edition: a plan published before with Lizmap Web Client keeps its
zone regulations as HTML files in the project's media folder, chosen by
expression fields of a layer ('media/html/' + zone kind + '.html' …) and
shown one after the other by the layer's Lizmap popup template. The preset
evaluates those expressions on the zoning layer's features, joins the files
in the template's order per zone code and makes the regulation text table
of the parcel report from them."""

import json
import os
import sys

from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsProject, QgsVectorLayer
from qgis.PyQt.QtCore import QVariant

HERE = os.path.dirname(os.path.abspath(__file__))
SQUARE = "POLYGON((650000 230000, 650100 230000, 650100 230100, 650000 230100, 650000 230000))"
EXPRESSIONS = {
    "szab_rov": "'media/html/'+if(csak_szab is null, szerk_ov, csak_szab)+'.html'",
    "sajatos_htm": "'media/html/'+replace(sajatos,'/','sajatos-')+'.html'",
    "altalanos": "'media/html/altalanos.html'",
    "altalanos_lako": "CASE WHEN szerk_ov IN ('Lk', 'Lke') THEN 'media/html/lako-altalanos.html' END",
    "szabov_htm": "'media/html/'+szab_kod+'.html'",  # not in the popup template: not taken
}
FILES = {"Lk": "<p><strong>Lk</strong> kisvárosias</p>", "Vt": "<p>Vt településközpont</p>",
         "Ev": "<p>Ev védelmi erdő</p>", "sajatos-M": "<p>/M sajátos</p>",
         "altalanos": "<p>Általános előírások</p>", "lako-altalanos": "<p>Lakóterületek általános</p>"}
# (szab_ov, szerk_ov, csak_szab, sajatos): empty text in the zoning layer
# where the Lizmap layer has NULL.
ZONES = (("Lk-400/K/40", "Lk", "", ""), ("Vt-2", "Vt", None, "/M"), ("Z", "Z", "Ev", ""),
         ("Gá-1", "Gá", "", ""), ("X-1", "../../titok", "", ""))


def _layer(name, fields, rows):
    layer = QgsVectorLayer("Polygon?crs=EPSG:23700&" + "&".join(f"field={f}:string" for f in fields), name, "memory")
    features = []
    for row in rows:
        feature = QgsFeature(layer.fields())
        feature.setAttributes(list(row))
        feature.setGeometry(QgsGeometry.fromWkt(SQUARE))
        features.append(feature)
    layer.dataProvider().addFeatures(features)
    return layer


def _lizmap_plan(tmp_path, template=True):
    from q2vt_fixtures import reset_project
    project = reset_project()
    media = tmp_path / "media" / "html"
    media.mkdir(parents=True)
    for name, text in FILES.items():
        (media / f"{name}.html").write_text(text, encoding="utf-8")
    (tmp_path / "titok.html").write_text("<p>nem publikus</p>", encoding="utf-8")
    project.addMapLayer(_layer("Földrészletek", ["hrsz"], [("1/1",)]))
    project.addMapLayer(_layer("Szabályozás színek", ["szab_ov", "szerk_ov", "csak_szab", "sajatos"], ZONES))
    web = _layer("szab_webdata", ["szab_kod", "szerk_ov", "csak_szab", "sajatos"], [("Lk-1", "Lk", None, None)])
    for name, expression in EXPRESSIONS.items():
        web.addExpressionField(expression, QgsField(name, QVariant.String))
    project.addMapLayer(web)
    path = tmp_path / "terv.qgs"
    assert project.write(str(path))
    if template:
        popup = "<b>{$szab_kod}</b><hr>{$szab_rov}<br>{$sajatos_htm}<hr>{$altalanos}<br>{$altalanos_lako}"
        config = {"layers": {"szab_webdata": {"name": "szab_webdata", "popupSource": "lizmap",
                                              "popupTemplate": popup}}}
        (tmp_path / "terv.qgs.cfg").write_text(json.dumps(config), encoding="utf-8")
    return project, web


def _apply(project):
    from q2vt_plugin.src.publishing import presets  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.models import PublicationProfile  # pylint: disable=import-error
    preset = next(m for m in presets.available() if m.PRESET_ID == "hu-hesz")
    profile = PublicationProfile()
    return profile, preset.apply(project, profile)


def _rows(project, profile):
    layer = project.mapLayer(profile.parcel_info.text_layer_id)
    return {f["szab_ov"]: f["eloiras_html"] for f in layer.getFeatures()}


def test_lizmap_texts_become_the_zone_text_table(plugin, tmp_path):
    from q2vt_plugin.src.publishing.parcel_report import _regulation_texts  # pylint: disable=import-error
    project, _web = _lizmap_plan(tmp_path)
    profile, notes = _apply(project)
    info = profile.parcel_info
    assert (info.text_code_field, info.text_field) == ("szab_ov", "eloiras_html")
    rows = _rows(project, profile)
    # In the popup template's order; the zone kind's text (or csak_szab's),
    # the special mark's, then the general ones (residential only for Lk).
    lk, vt, z = rows["Lk-400/K/40"], rows["Vt-2"], rows["Z"]
    assert [lk.index(FILES[n]) for n in ("Lk", "altalanos", "lako-altalanos")] == sorted(
        lk.index(FILES[n]) for n in ("Lk", "altalanos", "lako-altalanos"))
    assert vt.index(FILES["Vt"]) < vt.index(FILES["sajatos-M"]) < vt.index(FILES["altalanos"])
    assert FILES["lako-altalanos"] not in vt
    assert z.startswith(FILES["Ev"]) and FILES["altalanos"] in z
    # A zone without its file keeps the general texts (and is reported);
    # nothing is read outside the media folder.
    assert rows["Gá-1"] == FILES["altalanos"]
    assert "nem publikus" not in "".join(rows.values())
    assert any("Hiányzó Lizmap-szövegfájlok: media/html/Gá.html" in note for note in notes), notes
    assert any("Lizmap-előírások átvéve" in note and "5 övezeti jel" in note for note in notes), notes
    assert not any("szabov_htm" in note for note in notes)  # not in the template: not used
    assert os.path.isfile(tmp_path / "hesz_eloirasok_szovege_lizmap.gpkg")
    published = _regulation_texts(project, info)
    assert "<strong>Lk</strong>" in published["Lk-400/K/40"]


def test_expressions_saved_only_in_the_project_file_and_made_again(plugin, tmp_path):
    """A Lizmap layer saved with the paths filled in has data fields of the
    same names: QGIS drops the expression fields, the project file keeps
    them. Running the preset again replaces the table (edited files are
    taken over)."""
    project, web = _lizmap_plan(tmp_path, template=False)
    for name in EXPRESSIONS:
        web.removeExpressionField(web.fields().indexOf(name))
    assert not any(web.fields().fieldOrigin(i) == web.fields().OriginExpression
                   for i in range(web.fields().count()))
    profile, _notes = _apply(project)
    assert FILES["Lk"] in _rows(project, profile)["Lk-400/K/40"]
    (tmp_path / "media" / "html" / "Lk.html").write_text("<p>módosított Lk</p>", encoding="utf-8")
    profile, _notes = _apply(project)
    assert "módosított Lk" in _rows(project, profile)["Lk-400/K/40"]
    assert len(project.mapLayersByName("HÉSZ övezeti előírások szövege (Lizmap)")) == 1


def test_a_texts_table_of_its_own_comes_first(plugin, tmp_path):
    sys.path.insert(0, HERE)
    from test_preset_hu_hesz_texts import _block, _convert  # pylint: disable=import-error
    project, _web = _lizmap_plan(tmp_path)
    _convert(tmp_path, _block("minta-html", "html"))
    project = QgsProject.instance()
    profile, notes = _apply(project)
    assert project.mapLayer(profile.parcel_info.text_layer_id).name() == "HÉSZ övezeti előírások szövege"
    assert not any("Lizmap" in note for note in notes)


def test_zone_values_of_lizmap_plans(plugin, tmp_path):
    """Zone values named beepmod, beepszaz, magassag, m2, zold, kozmu (plans
    made for Lizmap) are taken when there are no p_… fields."""
    from q2vt_fixtures import reset_project
    project = reset_project()
    project.addMapLayer(_layer("Földrészletek", ["hrsz"], [("1/1",)]))
    project.addMapLayer(_layer("Szabályozás színek", ["szab_ov", "beepmod", "beepszaz", "magassag", "m2", "zold",
                                                      "kozmu", "p_zold"], [("Lk-1", "O", "30", "7,5", "550", "40",
                                                                            "T", "45")]))
    profile, _notes = _apply(project)
    fields = {f.alias: f.field for f in profile.parcel_info.zoning_fields}
    assert fields == {"Beépítési mód": "beepmod", "Legnagyobb beépítettség (%)": "beepszaz",
                      "Legnagyobb épületmagasság (m)": "magassag", "Legkisebb telekterület (m²)": "m2",
                      "Legkisebb zöldfelület (%)": "p_zold", "Közművesítettség": "kozmu"}
