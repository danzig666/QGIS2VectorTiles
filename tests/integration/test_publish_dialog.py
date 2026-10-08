"""The Publish Web Map window (PUB-13): defaults from the project, every
setting saved in the project file and read back, local export without
changing the project, loopback preview, local and (fake) R2 publication
with the review gate, credentials only by reference."""

import json
import os
import time
import urllib.request

import pytest
from qgis.core import QgsApplication, QgsProject
from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtWidgets import QMessageBox

from q2vt_fixtures import reset_project
from publishing.models import ReleaseState

CHECKED = Qt.CheckState.Checked


@pytest.fixture
def messages(monkeypatch):
    shown = []
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, _n=name, **k: shown.append((_n, a[2] if len(a) > 2 else "")))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    return shown


@pytest.fixture
def project(plugin, tmp_path):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_publishing_pipeline import _parcels  # pylint: disable=import-error
    parcels = _parcels(str(tmp_path / "parcels.gpkg"))
    project = reset_project()
    group = project.layerTreeRoot().addGroup("Szabályozás")
    project.addMapLayer(parcels, False)
    group.addLayer(parcels)
    project.layerTreeRoot().findLayer(parcels.id()).setItemVisibilityChecked(False)
    project.setFileName(str(tmp_path / "terv.qgz"))
    return project, parcels


def _dialog():
    from q2vt_plugin.src.gui.publish_dialog import PublishDialog  # pylint: disable=import-error
    return PublishDialog(iface=None)


def _configure(dialog, parcels, tmp_path):
    dialog.e_title.setText("Arló szabályozási terv")
    dialog.e_slug.setText("arlo-terv")
    dialog.e_min_zoom.setValue(11)
    dialog.e_max_zoom.setValue(13)
    dialog.profile.view.extent = [2119000, 6019000, 2123000, 6023000]
    item = next(dialog._tree_items())  # pylint: disable=protected-access
    item.setCheckState(1, CHECKED)
    item.setCheckState(2, CHECKED)
    dialog.o_dir.setText(str(tmp_path / "web maps"))
    dialog._fill_interaction_layers()  # pylint: disable=protected-access
    table = dialog.i_fields
    names = [table.item(row, 0).text() for row in range(table.rowCount())]
    row = names.index("hrsz")
    for column in (1, 4, 5):  # popup, search, key
        table.item(row, column).setCheckState(CHECKED)
    table.setItem(row, 2, type(table.item(row, 0))("Helyrajzi szám"))
    table.cellWidget(names.index("zone"), 6).setCurrentIndex(1)  # values filter
    assert not dialog.i_google_key.isEnabled()  # the key field follows the Street View switch
    dialog.i_flags["street_view"].setChecked(True)
    dialog.i_google_key.setText(" AIzaTESTKEY ")


def _wait_task(dialog, timeout=120):
    end = time.time() + timeout
    while dialog.task is not None and time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.05)
    assert dialog.task is None, "publishing task did not finish"


def test_defaults_follow_the_project(project, messages):
    project, parcels = project
    dialog = _dialog()
    config = dialog.profile.layer(parcels.id())
    assert config is not None and config.included is False  # hidden layers are not selected by default
    assert dialog.profile.output.archive == "pmtiles" and dialog.profile.destination.kind == "local"
    dialog.close()


def test_settings_are_saved_in_the_project_file(project, messages, tmp_path):
    project, parcels = project
    dialog = _dialog()
    _configure(dialog, parcels, tmp_path)
    assert dialog.save_settings()
    assert project.isDirty()
    assert project.write()
    path = project.fileName()
    layer_id = parcels.id()
    project.clear()
    assert QgsProject.instance().read(path)
    from q2vt_plugin.src.gui import publication_profiles as store  # pylint: disable=import-error
    profile, saved_from = store.active_profile(QgsProject.instance())
    assert profile.title == "Arló szabályozási terv" and saved_from == path
    config = profile.layer(layer_id)
    assert config.included and config.initially_visible and config.key_fields == ["hrsz"]
    assert [p.alias for p in config.popup_fields] == ["Helyrajzi szám"]
    assert [f.field for f in config.filter_fields] == ["zone"]
    assert profile.interaction.street_view and profile.interaction.google_api_key == "AIzaTESTKEY"
    raw, _ = QgsProject.instance().readEntry(store.SCOPE, store.KEY_PROFILES, "")
    assert "secret" not in raw.lower() and "password" not in raw.lower()
    # A second window shows the saved settings.
    again = _dialog()
    assert again.e_title.text() == "Arló szabályozási terv" and again.e_slug.text() == "arlo-terv"
    assert again.i_google_key.text() == "AIzaTESTKEY" and again.i_google_key.isEnabled()
    dialog.close()
    again.close()


def test_export_preview_and_local_publish(project, messages, tmp_path):
    project, parcels = project
    tree = [(n.layer().id(), n.isVisible()) for n in project.layerTreeRoot().findLayers()]
    dialog = _dialog()
    _configure(dialog, parcels, tmp_path)
    assert dialog.export_locally(), messages
    assert [(n.layer().id(), n.isVisible()) for n in project.layerTreeRoot().findLayers()] == tree
    assert os.path.exists(dialog.local_result.entry_path)
    dialog.preview()
    url = dialog.preview_server.url("arlo-terv/index.html")
    with urllib.request.urlopen(url) as response:
        assert response.status == 200 and b"bootstrap" in response.read()
    dialog.preview_server.stop()
    dialog.publish()  # local destination: export + local link
    assert dialog.public_url.startswith("file:")
    dialog.close()


def test_r2_publish_needs_approval_and_runs_in_a_task(project, messages, tmp_path, monkeypatch):
    project, parcels = project
    from publishing_fake_s3 import FakeS3Client
    from publishing.preview_server import PreviewServer
    from publishing.providers.base import Credentials
    from publishing.providers.r2 import R2Provider
    dialog = _dialog()
    _configure(dialog, parcels, tmp_path)
    root = tmp_path / "bucket"
    client = FakeS3Client(str(root))
    public = PreviewServer(str(root / "maps")).start()
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("r2"))
    dialog.d_account.setText("abc123")
    dialog.d_bucket.setText("maps")
    dialog.d_public.setText(public.url("").rstrip("/"))
    dialog.d_session_key.setText("AKIATEST")
    dialog.d_session_secret.setText("session-secret-value")
    profile = dialog.collect()
    monkeypatch.setattr(dialog, "_provider", lambda: R2Provider(
        profile.destination, Credentials("AKIATEST", "session-secret-value"), "maps/arlo-terv",
        client=client, sleep=lambda s: None))
    try:
        dialog.approve.setChecked(False)
        dialog.publish()
        assert dialog.task is None and dialog.local_result is None  # stopped at the review
        dialog.refresh_review()
        assert "Popup fields: hrsz" in dialog.review.toPlainText()
        dialog.approve.setChecked(True)
        dialog.publish()
        _wait_task(dialog)
        assert dialog.public_url.endswith("/maps/arlo-terv/index.html"), messages
        assert client.keys() and all(k.startswith("maps/arlo-terv/") for k in client.keys())
        from q2vt_plugin.src.gui import publication_profiles as store  # pylint: disable=import-error
        saved, _ = store.active_profile(project)
        assert saved.approval.fingerprint  # approval stored with the settings
        raw, _ = project.readEntry(store.SCOPE, store.KEY_PROFILES, "")
        assert "session-secret-value" not in raw and "AKIATEST" not in raw
        pointer = json.loads(open(root / "maps" / "maps" / "arlo-terv" / "current.json").read())
        assert pointer["releaseId"] == dialog.local_result.release.release_id
    finally:
        public.stop()
        dialog.close()


def test_copy_embed_code(project, messages, tmp_path):
    from qgis.PyQt.QtGui import QGuiApplication
    project, parcels = project
    dialog = _dialog()
    _configure(dialog, parcels, tmp_path)
    QGuiApplication.clipboard().setText("before")
    assert dialog.copy_embed_code() == ""  # a local copy cannot be shown in another page
    assert QGuiApplication.clipboard().text() == "before" and "Destination" in dialog.status.text()
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("r2"))
    dialog.d_public.setText("https://maps.example.hu/")
    code = dialog.copy_embed_code()
    assert code.startswith('<iframe src="https://maps.example.hu/') and "/arlo-terv/index.html?embed\"" in code
    assert 'title="Arló szabályozási terv"' in code and QGuiApplication.clipboard().text() == code
    dialog.public_url = "https://maps.example.hu/terv/index.html#v=1/47.5/19"  # after a publication
    assert 'src="https://maps.example.hu/terv/index.html?embed#v=1/47.5/19"' in dialog.copy_embed_code()
    dialog.close()


def test_info_documents_terrain_addresses_round_trip(project, messages, tmp_path):
    """The 4.24 settings: map info and documents (Info tab), terrain (Basemap
    tab), house numbers, overview and drawing (Interaction tab) and a layer's
    3D height field are kept through the window and listed in the review."""
    from qgis.core import QgsField, QgsRasterLayer, QgsVectorLayer  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtCore import QVariant  # pylint: disable=import-outside-toplevel
    from publishing.models import DocumentConfig  # pylint: disable=import-error
    sys_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "browser")
    import sys  # pylint: disable=import-outside-toplevel
    sys.path.insert(0, sys_path)
    from test_web_viewer_extras import _dem  # pylint: disable=import-error
    project, parcels = project
    dem = QgsRasterLayer(_dem(tmp_path / "dem.tif"), "Domborzat")
    addresses = QgsVectorLayer("Point?crs=EPSG:3857", "Házszámok", "memory")
    addresses.dataProvider().addAttributes([QgsField("hsz", QVariant.String)])
    addresses.updateFields()
    document = tmp_path / "rendelet.pdf"
    document.write_bytes(b"%PDF-1.4\n%%EOF\n")
    dialog = _dialog()
    _configure(dialog, parcels, tmp_path)
    project.addMapLayers([dem, addresses])
    profile = dialog.collect()
    profile.info.issuer, profile.info.legal_date = "Arló", "2025. 10. 01."
    profile.info.documents = [DocumentConfig("HÉSZ", str(document))]
    profile.terrain.layer_id, profile.terrain.exaggeration = dem.id(), 2.0
    profile.interaction.address_layer_id, profile.interaction.address_number_field = addresses.id(), "hsz"
    profile.interaction.overview_map = profile.interaction.three_d = True
    dialog._populate(profile)  # pylint: disable=protected-access
    dialog._fill_interaction_layers()  # the Interaction tab opened  # pylint: disable=protected-access
    dialog.i_layers.setCurrentRow(0)
    dialog.i_height.setCurrentIndex(dialog.i_height.findData("terulet"))  # chosen in the window
    again = dialog.collect()
    assert again.info.issuer == "Arló" and again.info.legal_date == "2025. 10. 01."
    assert [(d.title, d.path) for d in again.info.documents] == [("HÉSZ", str(document))]
    assert again.terrain.layer_id == dem.id() and again.terrain.exaggeration == 2.0 and again.terrain.hillshade
    assert again.interaction.address_layer_id == addresses.id() and again.interaction.address_number_field == "hsz"
    assert again.interaction.address_street_field == "" and again.interaction.overview_map
    assert again.interaction.three_d and not again.interaction.drawing  # drawing: off by default
    assert again.layer(parcels.id()).height_field == "terulet"
    assert dialog.i_height.currentData() == "terulet"
    dialog.refresh_review()
    review = dialog.review.toPlainText()
    assert "HÉSZ (rendelet.pdf)" in review and "Domborzat" in review and "Házszámok" in review
    assert "overview map, 3D view" in review
    assert "terulet" in review
    dialog.close()
