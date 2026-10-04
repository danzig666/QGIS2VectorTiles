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
