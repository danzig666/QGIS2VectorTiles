"""Publish window: the extent from a layer (combo; follows the layer's
extent), a fixed extent, a readable summary instead of raw coordinates, the
setting saved in the profile; and room for the layer list on the
Interaction tab."""

import os
import sys

from qgis.core import QgsFeature, QgsGeometry
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QMessageBox

from q2vt_fixtures import reset_project

CHECKED = Qt.CheckState.Checked


def _dialog(plugin, monkeypatch, tmp_path):
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: None)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_publishing_pipeline import _parcels  # pylint: disable=import-error,import-outside-toplevel
    os.makedirs(str(tmp_path), exist_ok=True)
    parcels = _parcels(str(tmp_path / "parcels.gpkg"))
    project = reset_project()
    project.addMapLayer(parcels)
    from q2vt_plugin.src.gui.publish_dialog import PublishDialog  # pylint: disable=import-error
    return PublishDialog(iface=None), parcels


def test_extent_from_a_layer_follows_it(plugin, monkeypatch, tmp_path):
    dialog, parcels = _dialog(plugin, monkeypatch, tmp_path)
    assert dialog.e_extent_layer.currentLayer() is None
    assert "canvas" in dialog.extent_label.text()
    dialog.e_extent_layer.setLayer(parcels)
    view = dialog.collect().view
    assert view.extent_layer == parcels.id()
    box = parcels.extent()  # EPSG:3857 already
    assert view.extent == [box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()]
    text = dialog.extent_label.text()
    assert text.startswith("Layer extent") and parcels.name() in dialog.extent_label.toolTip() and " km" in text and "\nN " in text and "EPSG" not in text
    # The layer grows: the published extent follows it.
    parcels.startEditing()
    feature = QgsFeature(parcels.fields())
    feature.setGeometry(QgsGeometry.fromWkt(
        "POLYGON((2125000 6025000, 2125100 6025000, 2125100 6025100, 2125000 6025000))"))
    parcels.addFeature(feature)
    assert parcels.commitChanges()
    parcels.updateExtents()
    assert dialog._extent().xMaximum() >= 2125100 - 1e-6  # pylint: disable=protected-access
    # Saved and read back.
    from q2vt_plugin.src.publishing.profile import dumps, load_profile  # pylint: disable=import-error
    again = load_profile(dumps(dialog.collect()))
    assert again.view.extent_layer == parcels.id()
    dialog.close()


def test_fixed_extent_and_a_removed_layer(plugin, monkeypatch, tmp_path):
    dialog, parcels = _dialog(plugin, monkeypatch, tmp_path)
    dialog.profile.view.extent = [2119000, 6019000, 2123000, 6023000]
    dialog.e_extent_layer.setLayer(parcels)
    dialog.e_extent_layer.setLayer(None)            # back to the fixed extent
    assert dialog.profile.view.extent_layer == ""
    assert dialog.extent_label.text().startswith("Fixed extent")
    dialog.e_extent_layer.setLayer(parcels)
    profile = dialog.collect()
    from qgis.core import QgsProject  # pylint: disable=import-outside-toplevel
    QgsProject.instance().removeMapLayer(parcels.id())
    dialog._populate(profile)                       # pylint: disable=protected-access
    assert dialog.profile.view.extent_layer == "" and dialog.profile.view.extent  # last extent kept
    dialog.close()


def test_interaction_layer_list_has_room(plugin, monkeypatch, tmp_path):
    dialog, parcels = _dialog(plugin, monkeypatch, tmp_path)
    dialog.show()
    dialog.tabs.setCurrentIndex(1)
    assert dialog.i_layers.width() >= 220
    assert dialog.i_split.sizes()[0] >= 220
    dialog.close()


def test_settings_file_export_and_import_into_another_project(plugin, monkeypatch, tmp_path):
    dialog, parcels = _dialog(plugin, monkeypatch, tmp_path)
    dialog.e_title.setText("Arló terv")
    dialog.e_slug.setText("arlo-terv")
    dialog.e_extent_layer.setLayer(parcels)
    item = next(dialog._tree_items())  # pylint: disable=protected-access
    item.setCheckState(1, CHECKED)
    path = str(tmp_path / "arlo.q2vt.json")
    assert dialog.export_settings_file(path)
    original = dialog.collect()
    text = open(path, encoding="utf-8").read()
    assert parcels.name() in text and "secret" not in text.lower()
    old_id = parcels.id()
    dialog.close()

    # Another project: the same layer under a new id.
    other, copy = _dialog(plugin, monkeypatch, tmp_path / "other")
    assert copy.id() != old_id
    assert other.import_settings_file(path, same_map=True)
    profile = other.collect()
    assert profile.title == "Arló terv" and profile.slug == "arlo-terv"
    assert profile.view.extent_layer == copy.id()
    assert profile.layer(copy.id()) is not None and profile.layer(copy.id()).included
    assert profile.publication_id == original.publication_id
    assert other.import_settings_file(path, same_map=False)
    assert other.collect().publication_id != original.publication_id
    # Not a settings file: refused, nothing changed.
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    assert not other.import_settings_file(str(bad), same_map=True)
    assert other.collect().title == "Arló terv"
    other.close()


def test_window_remembers_its_size_and_position(plugin, monkeypatch, tmp_path):
    from qgis.core import QgsSettings  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtGui import QGuiApplication  # pylint: disable=import-outside-toplevel
    dialog, _parcels = _dialog(plugin, monkeypatch, tmp_path)
    QgsSettings().remove(dialog.GEOMETRY_KEY)
    screen = QGuiApplication.primaryScreen().availableGeometry()
    dialog.resize(700, 520)
    dialog.move(screen.left() + 40, screen.top() + 30)
    dialog.close()  # the Close button
    assert QgsSettings().value(dialog.GEOMETRY_KEY)
    again, _ = _dialog(plugin, monkeypatch, tmp_path / "again")
    assert (again.width(), again.height()) == (700, 520)
    assert abs(again.pos().x() - (screen.left() + 40)) <= 1 and abs(again.pos().y() - (screen.top() + 30)) <= 1
    again.resize(760, 600)
    again.reject()  # Escape
    third, _ = _dialog(plugin, monkeypatch, tmp_path / "third")
    assert (third.width(), third.height()) == (760, 600)
    QgsSettings().remove(dialog.GEOMETRY_KEY)


def test_settings_file_carries_the_object_storage_keys(plugin, monkeypatch, tmp_path):
    """At the owner's request the settings file also holds the R2/S3 keys
    (secret in plain text); importing them makes them usable at once."""
    import json  # pylint: disable=import-outside-toplevel
    dialog, _parcels = _dialog(plugin, monkeypatch, tmp_path)
    dialog.e_title.setText("Arló terv")
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("r2"))
    dialog.d_account.setText("0123456789abcdef0123456789abcdef")
    dialog.d_bucket.setText("maps")
    dialog.d_public.setText("https://maps.example.hu")
    dialog.d_session_key.setText("AKIAEXAMPLEKEY")
    dialog.d_session_secret.setText("s3cr3t/EXAMPLE+key")
    path = str(tmp_path / "arlo.q2vt.json")
    assert dialog.export_settings_file(path)
    data = json.load(open(path, encoding="utf-8"))
    assert data["credentials"] == {"accessKeyId": "AKIAEXAMPLEKEY", "secretAccessKey": "s3cr3t/EXAMPLE+key"}
    assert "credentials" not in data["profile"]  # the profile itself never holds them
    assert "secret included" in dialog.status.text()
    dialog.close()

    other, _ = _dialog(plugin, monkeypatch, tmp_path / "other")
    assert other.import_settings_file(path, same_map=True)
    keys = other._credentials()  # pylint: disable=protected-access
    assert (keys.access_key_id, keys.secret_access_key) == ("AKIAEXAMPLEKEY", "s3cr3t/EXAMPLE+key")
    assert other.collect().destination.kind == "r2"
    other.close()


def test_web_basemaps_saved_in_qgis_and_in_the_settings_file(plugin, monkeypatch, tmp_path):
    """XYZ addresses: kept in the profile, written as QGIS XYZ connections
    (Browser -> XYZ Tiles), carried by the settings file and offered back."""
    import json  # pylint: disable=import-outside-toplevel
    from qgis.core import QgsSettings  # pylint: disable=import-outside-toplevel
    from q2vt_plugin.src.gui.xyz_connections import qgis_xyz_connections  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.models import XyzBasemap  # pylint: disable=import-error
    QgsSettings().remove("connections/xyz/items/Teszt műhold")
    dialog, _parcels = _dialog(plugin, monkeypatch, tmp_path)
    dialog.e_title.setText("Arló terv")
    dialog._add_xyz_row(XyzBasemap("Teszt műhold", "https://{s}.tiles.example.hu/{z}/{x}/{y}.jpg",  # pylint: disable=protected-access
                                   "© Példa Kft.", 0, 20))
    dialog.b_initial.setCurrentIndex(dialog.b_initial.findData("xyz-1"))
    basemap = dialog.collect().basemap
    assert [(x.title, x.max_zoom) for x in basemap.xyz] == [("Teszt műhold", 20)] and basemap.initial == "xyz-1"
    assert "Teszt műhold" not in {x.title for x in qgis_xyz_connections()}  # not on every read
    dialog.save_settings(quiet=True)
    saved = {x.title: x for x in qgis_xyz_connections()}
    assert saved["Teszt műhold"].url == "https://{s}.tiles.example.hu/{z}/{x}/{y}.jpg"
    assert saved["Teszt műhold"].attribution == "© Példa Kft." and saved["Teszt műhold"].max_zoom == 20
    path = str(tmp_path / "arlo.q2vt.json")
    assert dialog.export_settings_file(path)
    assert json.load(open(path, encoding="utf-8"))["profile"]["basemap"]["xyz"][0]["title"] == "Teszt műhold"
    dialog.close()

    QgsSettings().remove("connections/xyz/items/Teszt műhold")  # another computer
    other, _ = _dialog(plugin, monkeypatch, tmp_path / "other")
    assert other.import_settings_file(path, same_map=True)
    assert [x.url for x in other.collect().basemap.xyz] == ["https://{s}.tiles.example.hu/{z}/{x}/{y}.jpg"]
    assert "Teszt műhold" in {x.title for x in qgis_xyz_connections()}  # saved in QGIS on import
    other.close()
    QgsSettings().remove("connections/xyz/items/Teszt műhold")



def test_web_basemap_table_lists_the_qgis_xyz_connections(plugin, monkeypatch, tmp_path):
    """Every XYZ connection saved in QGIS is listed (not ticked); ticking one
    offers it in the web map; an http:// one cannot be ticked."""
    from qgis.core import QgsSettings  # pylint: disable=import-outside-toplevel
    settings = QgsSettings()
    settings.setValue("connections/xyz/items/QGIS ortó/url", "https://ortho.example.hu/{z}/{x}/{y}.jpg")
    settings.setValue("connections/xyz/items/QGIS ortó/zmax", 20)
    settings.setValue("connections/xyz/items/Régi http/url", "http://old.example.hu/{z}/{x}/{y}.png")
    try:
        dialog, _parcels = _dialog(plugin, monkeypatch, tmp_path)
        rows = {dialog.b_xyz.item(r, 1).text(): r for r in range(dialog.b_xyz.rowCount())}
        assert "QGIS ortó" in rows and "Régi http" in rows
        ortho, old = rows["QGIS ortó"], rows["Régi http"]
        assert dialog.b_xyz.item(ortho, 0).checkState() == Qt.CheckState.Unchecked
        assert not dialog.b_xyz.item(ortho, 1).flags() & Qt.ItemFlag.ItemIsEditable  # QGIS's name stays
        assert not dialog.b_xyz.item(old, 0).flags() & Qt.ItemFlag.ItemIsEnabled      # http: not offered
        assert dialog.collect().basemap.xyz == []
        dialog.b_xyz.item(ortho, 0).setCheckState(CHECKED)
        dialog.b_xyz.item(ortho, 3).setText("© Lechner")
        xyz = dialog.collect().basemap.xyz
        assert [(x.title, x.url, x.max_zoom, x.attribution) for x in xyz] == [
            ("QGIS ortó", "https://ortho.example.hu/{z}/{x}/{y}.jpg", 20, "© Lechner")]
        assert dialog.b_initial.findData("xyz-1") >= 0
        dialog.save_settings(quiet=True)  # the QGIS connection is updated when the settings are saved
        assert settings.value("connections/xyz/items/QGIS ortó/q2vt-attribution") == "© Lechner"
        dialog.close()
    finally:
        settings.remove("connections/xyz/items/QGIS ortó")
        settings.remove("connections/xyz/items/Régi http")
