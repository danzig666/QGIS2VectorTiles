"""4.25 fixes found by reviewing 4.22-4.24: the Publish window keeps a
preset's or an imported file's per-layer settings and the regulation
fields' types, survives a removed layer and picks no house-number field by
itself; web basemaps never overwrite the user's own QGIS connection; a bad
terrain layer is a warning; house numbers find their street in the north;
WMS map paths, zone codes, wrong value types and replaced documents."""

import os
import sys

import pytest
from qgis.core import QgsField, QgsRasterLayer, QgsSettings, QgsVectorLayer
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtWidgets import QMessageBox

from q2vt_fixtures import reset_project

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture
def dialog(plugin, tmp_path, monkeypatch):
    sys.path.insert(0, HERE)
    from test_publishing_pipeline import _parcels  # pylint: disable=import-error
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: None)
    project = reset_project()
    parcels = _parcels(str(tmp_path / "parcels.gpkg"))
    project.addMapLayer(parcels)
    project.setFileName(str(tmp_path / "terv.qgz"))
    from q2vt_plugin.src.gui.publish_dialog import PublishDialog  # pylint: disable=import-error
    window = PublishDialog(iface=None)
    item = next(window._tree_items())  # pylint: disable=protected-access
    item.setCheckState(1, 2)
    window._fill_interaction_layers()  # pylint: disable=protected-access
    yield window, project, parcels
    window.close()


def test_new_settings_are_not_overwritten_by_the_shown_layer(dialog):
    """A preset or an imported file replaces the settings while the
    Interaction tab still shows the old values of a layer."""
    window, _project, parcels = dialog
    assert window.current_layer_id == parcels.id() and not window.i_label_always.isChecked()
    profile = window.collect()
    config = profile.layer(parcels.id())
    config.label_always, config.snap, config.search_fields = True, True, ["hrsz"]
    window._populate(profile)  # pylint: disable=protected-access
    again = window.collect().layer(parcels.id())
    assert again.label_always and again.snap and again.search_fields == ["hrsz"]


def test_regulation_field_types_survive_the_window(dialog):
    from q2vt_plugin.src.publishing.models import PopupField  # pylint: disable=import-error
    window, project, _parcels = dialog
    table = QgsVectorLayer("None?field=szab_ov:string&field=max_mag:double&field=rendelet:string",
                           "HÉSZ övezeti előírások", "memory")
    project.addMapLayer(table)
    profile = window.collect()
    info = profile.parcel_info
    info.regulation_layer_id, info.regulation_code_field = table.id(), "szab_ov"
    info.regulation_fields = [PopupField("max_mag", "Magasság", "number"), PopupField("rendelet", "Rendelet", "url")]
    window._populate(profile)  # pylint: disable=protected-access
    kinds = [(f.field, f.type) for f in window.collect().parcel_info.regulation_fields]
    assert kinds == [("max_mag", "number"), ("rendelet", "url")]


def test_a_removed_layer_does_not_break_the_interaction_tab(dialog):
    window, project, parcels = dialog
    other = QgsVectorLayer("Point?crs=EPSG:3857&field=hsz:string", "Házszámok", "memory")
    project.addMapLayer(other)
    project.removeMapLayer(parcels.id())  # the window keeps its tree item
    window._fill_interaction_layers()  # pylint: disable=protected-access
    window.collect()


def test_house_number_field_is_not_guessed_from_the_first_field(dialog):
    window, project, _parcels = dialog
    numbered = QgsVectorLayer("Point?crs=EPSG:3857&field=fid_x:integer&field=hsz:string", "Címek", "memory")
    plain = QgsVectorLayer("Point?crs=EPSG:3857&field=fid_x:integer&field=adat:string", "Pontok", "memory")
    project.addMapLayers([numbered, plain])
    window.i_address_layer.setLayer(numbered)
    assert window.i_address_number.currentField() == "hsz"
    window.i_address_layer.setLayer(plain)
    assert window.i_address_number.currentField() == ""
    assert window.collect().interaction.address_layer_id == ""  # no number field: no house numbers


def test_web_basemaps_keep_the_users_own_qgis_connection(plugin):
    from q2vt_plugin.src.gui.xyz_connections import save_qgis_xyz_connections  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.models import XyzBasemap  # pylint: disable=import-error
    settings, key = QgsSettings(), "connections/xyz/items/Saját ortó"
    settings.setValue(f"{key}/url", "https://mine.example.hu/{z}/{x}/{y}.png")
    try:
        save_qgis_xyz_connections([XyzBasemap("Saját ortó", "https://other.example.hu/{z}/{x}/{y}.png")])
        assert settings.value(f"{key}/url") == "https://mine.example.hu/{z}/{x}/{y}.png"
    finally:
        settings.remove(key)


def test_unreadable_terrain_is_a_warning(plugin, tmp_path):
    from q2vt_plugin.src.publishing import controller  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.models import PublicationProfile  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.progress import Progress  # pylint: disable=import-error
    project = reset_project()
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "browser"))
    from test_web_viewer_extras import _dem  # pylint: disable=import-error
    path = _dem(tmp_path / "dem.tif")
    dem = QgsRasterLayer(path, "Domborzat")
    project.addMapLayer(dem)
    os.remove(path)  # gone after the project was opened
    profile = PublicationProfile()
    profile.terrain.layer_id = dem.id()
    warnings = []
    out = controller._render_terrain(project, profile, (2119000, 6019000, 2123000, 6023000),  # pylint: disable=protected-access
                                     str(tmp_path), Progress(), warnings)
    assert out is None and warnings and "without terrain" in warnings[0]


def test_house_numbers_find_their_street_in_the_north(plugin):
    import math
    from q2vt_plugin.src.publishing.basemap import StreetIndex, address_records  # pylint: disable=import-error
    lat = 60.17
    east = 130 / (111320 * math.cos(math.radians(lat)))
    index = StreetIndex({"Kauppakatu": [[(24.94 + east, lat - 0.001), (24.94 + east, lat + 0.001)]],
                         "Kaukana": [[(24.93, lat + 145 / 110540), (24.95, lat + 145 / 110540)]]})
    assert index.nearest((24.94, lat)) == "Kauppakatu"
    records, missing = address_records([(24.94, lat, float("nan"), None), (24.94, lat, 7.0, None)], index)
    assert [r["label"] for r in records] == ["Kauppakatu 7"] and missing == 0


def test_wms_map_path_and_zone_codes(plugin):
    from q2vt_plugin.src.publishing.parcel_report import zone_key  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.validation import scan_text_for_leaks  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.xyz import wms_template  # pylint: disable=import-error
    url = wms_template("https://h.example.hu/cgi-bin/mapserv?map=/home/gis/orto.map", "ORTO")
    assert "map=%2Fhome%2Fgis%2Forto.map" in url and not scan_text_for_leaks(url)
    assert [zone_key(v) for v in (12.0, 12, " Lke-1 ", None, 2.5)] == ["12", "12", "Lke-1", "", "2.5"]


def test_wrong_value_types_and_replaced_documents(plugin, tmp_path):
    from q2vt_plugin.src.publishing.errors import PublishingError  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.models import DocumentConfig, PublicationProfile  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.profile import disclosure_fingerprint, dumps, load_profile  # pylint: disable=import-error
    import json  # pylint: disable=import-outside-toplevel
    data = json.loads(dumps(PublicationProfile(title="T", slug="t")))
    data["terrain"] = {"exaggeration": "high"}
    with pytest.raises(PublishingError):
        load_profile(data)
    document = tmp_path / "rendelet.pdf"
    document.write_bytes(b"%PDF-1.4 first")
    profile = PublicationProfile(title="T", slug="t")
    profile.info.documents = [DocumentConfig("HÉSZ", str(document))]
    before = disclosure_fingerprint(profile)
    document.write_bytes(b"%PDF-1.4 another file under the same name")
    assert disclosure_fingerprint(profile) != before


def test_missing_document_and_address_field_stop_before_the_export(plugin, tmp_path):
    from q2vt_plugin.src.publishing.models import DocumentConfig, LayerConfig, PublicationProfile  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.qgis_model import check_profile_against_project  # pylint: disable=import-error
    project = reset_project()
    points = QgsVectorLayer("Point?crs=EPSG:3857", "Címek", "memory")
    points.dataProvider().addAttributes([QgsField("hsz", QVariant.String)])
    points.updateFields()
    project.addMapLayer(points)
    profile = PublicationProfile(title="T", slug="t")
    profile.layers = [LayerConfig(points.id())]
    profile.info.documents = [DocumentConfig("HÉSZ", str(tmp_path / "nincs.pdf"))]
    profile.interaction.address_layer_id = points.id()
    profile.interaction.address_number_field, profile.interaction.address_street_field = "hsz", "utca"
    problems = " ".join(check_profile_against_project(profile, project))
    assert "nincs.pdf" in problems and 'field "utca"' in problems


def test_regulation_texts_table_round_trip(dialog):
    """4.26: the full regulation texts table (Parcel report tab): its fields are
    guessed when it is chosen and kept through the window."""
    window, project, _parcels = dialog
    texts = QgsVectorLayer("None?field=fid_x:integer&field=szab_ov:string&field=eloiras_html:string",
                           "HÉSZ övezeti előírások szövege", "memory")
    project.addMapLayer(texts)
    window.p_text.setLayer(texts)
    assert window.p_text_code.currentField() == "szab_ov" and window.p_text_field.currentField() == "eloiras_html"
    profile = window.collect()
    info = profile.parcel_info
    assert (info.text_layer_id, info.text_code_field, info.text_field) == (texts.id(), "szab_ov", "eloiras_html")
    window.p_text.setLayer(None)
    window._populate(profile)  # pylint: disable=protected-access
    again = window.collect().parcel_info
    assert (again.text_layer_id, again.text_code_field, again.text_field) == (texts.id(), "szab_ov", "eloiras_html")
