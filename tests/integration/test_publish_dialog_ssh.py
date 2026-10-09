"""Publish window, SSH / SFTP destination: its fields (and only its fields)
for that kind, a pasted user@server:/folder, settings saved in the project
without the password, the review target, and Test connection plus a
publication through the window into a local OpenSSH server's folder
(skipped without sshd / the OpenSSH client)."""

import json
import os
import time

import pytest
from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsVectorLayer
from qgis.PyQt.QtCore import QCoreApplication, Qt, QVariant
from qgis.PyQt.QtWidgets import QMessageBox

import publishing_sshd
from q2vt_fixtures import reset_project, to_geopackage

CHECKED = Qt.CheckState.Checked


@pytest.fixture
def messages(monkeypatch):
    shown = []
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name,
                            lambda *a, _n=name, **k: shown.append((_n, a[2] if len(a) > 2 else "")))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    return shown


@pytest.fixture
def project(plugin, tmp_path):
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "Blocks", "memory")
    layer.dataProvider().addAttributes([QgsField("name", QVariant.String)])
    layer.updateFields()
    for index in range(3):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([f"Block {index + 1}"])
        x = 1000000 + index * 400
        feature.setGeometry(QgsGeometry.fromWkt(
            f"POLYGON(({x} 6000000, {x + 300} 6000000, {x + 300} 6000300, {x} 6000300, {x} 6000000))"))
        layer.dataProvider().addFeatures([feature])
    saved = to_geopackage(layer, str(tmp_path / "blocks.gpkg"))
    project = reset_project(saved)
    project.setFileName(str(tmp_path / "town.qgz"))
    return project, saved


def _dialog():
    from q2vt_plugin.src.gui.publish_dialog import PublishDialog  # pylint: disable=import-error
    return PublishDialog(iface=None)


def _label(dialog, widget):
    return dialog.d_form.labelForField(widget).text()


def test_ssh_fields_paste_saved_settings_and_review(project, messages):
    project, _ = project
    from q2vt_plugin.src.gui import publication_profiles as store  # pylint: disable=import-error
    dialog = _dialog()
    dialog.show()
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("ssh"))
    for widget in (dialog.d_ssh_host, dialog.d_ssh_port, dialog.d_ssh_user, dialog.d_ssh_dir,
                   dialog.d_ssh_key_row, dialog.d_ssh_password_row, dialog.d_public):
        assert not widget.isHidden()
    for widget in (dialog.d_account, dialog.d_endpoint, dialog.d_bucket, dialog.d_prefix, dialog.d_session_row,
                   dialog.d_retention, dialog.d_conditional, dialog.d_history, dialog.d_cors, dialog.d_guide):
        assert widget.isHidden()  # no versions, no rollback, no bucket settings
    assert _label(dialog, dialog.d_public) == "Public URL of the folder (optional)"
    assert "SSH user" in dialog.d_auth_label.text() and "no rollback" in dialog.d_note.text()
    dialog.d_ssh_host.setText("deploy@www.example.com:/srv/web maps/town")
    dialog.d_ssh_host.editingFinished.emit()
    assert (dialog.d_ssh_host.text(), dialog.d_ssh_user.text(), dialog.d_ssh_dir.text()) == \
        ("www.example.com", "deploy", "/srv/web maps/town")
    dialog.d_ssh_port.setValue(2222)
    dialog.d_ssh_password.setText("pw-only-for-this-session")
    assert "optional" in dialog.d_address.text()
    dialog.d_public.setText("https://www.example.com/town/")
    assert dialog.d_address.text() == "https://www.example.com/town/"
    assert dialog.save_settings(), messages
    raw, _ = project.readEntry(store.SCOPE, store.KEY_PROFILES, "")
    assert "pw-only-for-this-session" not in raw
    saved = store.active_profile(project)[0].destination
    assert (saved.kind, saved.host, saved.port, saved.user, saved.remote_dir) == \
        ("ssh", "www.example.com", 2222, "deploy", "/srv/web maps/town")
    dialog.refresh_review()
    assert "Target: https://www.example.com/town/index.html" in dialog.review.toPlainText()
    dialog.d_public.clear()
    dialog.refresh_review()
    assert "Target: deploy@www.example.com (port 2222), folder /srv/web maps/town" in dialog.review.toPlainText()
    assert dialog._exportable_credentials(dialog.collect()) is None  # pylint: disable=protected-access
    again = _dialog()  # a second window shows the saved settings, never the password
    assert again.d_kind.currentData() == "ssh" and again.d_ssh_dir.text() == "/srv/web maps/town"
    assert again.d_ssh_port.value() == 2222 and again.d_ssh_password.text() == ""
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("r2"))
    assert dialog.d_ssh_host.isHidden() and not dialog.d_bucket.isHidden() and not dialog.d_history.isHidden()
    assert _label(dialog, dialog.d_public) == "Public base URL (custom domain)"
    again.close()
    dialog.close()


def _wait_task(dialog, timeout=120):
    end = time.time() + timeout
    while dialog.task is not None and time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.05)
    assert dialog.task is None, "publishing task did not finish"


@pytest.mark.skipif(not publishing_sshd.available(), reason="needs /usr/sbin/sshd and the OpenSSH client")
def test_test_connection_and_publish_through_the_window(project, messages, tmp_path, monkeypatch):
    project, layer = project
    try:
        server = publishing_sshd.SshServer(str(tmp_path / "sshd")).start()
    except RuntimeError as error:
        pytest.skip(str(error))
    remote = tmp_path / "server" / "web root" / "town map"
    try:
        dialog = _dialog()
        dialog.e_title.setText("Town map")
        dialog.e_slug.setText("town-map")
        dialog.e_min_zoom.setValue(12)
        dialog.e_max_zoom.setValue(13)
        dialog.profile.view.extent = [999000, 5999000, 1002000, 6001000]
        item = next(dialog._tree_items())  # pylint: disable=protected-access
        item.setCheckState(1, CHECKED)
        item.setCheckState(2, CHECKED)
        dialog.o_dir.setText(str(tmp_path / "web maps"))
        dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("ssh"))
        dialog.d_ssh_host.setText("127.0.0.1")
        dialog.d_ssh_port.setValue(server.port)
        dialog.d_ssh_user.setText(server.user)
        dialog.d_ssh_dir.setText(str(remote))
        dialog.d_ssh_key.setText(server.locked_key)  # its passphrase as the session password
        dialog.d_ssh_password.setText(publishing_sshd.PASSPHRASE)
        # The test server's host key goes into a private known_hosts, not the user's.
        credentials = dialog._credentials  # pylint: disable=protected-access
        monkeypatch.setattr(dialog, "_provider",
                            lambda: server.provider(dialog.profile.destination, credentials()))
        dialog.test_connection()
        assert messages and messages[-1][0] == "information", messages
        assert messages[-1][1].count("✔") == 3 and remote.is_dir(), messages[-1][1]
        dialog.approve.setChecked(True)
        dialog.publish()
        _wait_task(dialog)
        assert "Published" in dialog.status.text(), (dialog.status.text(), messages)
        release_dir = dialog.local_result.release.release_dir
        with open(os.path.join(release_dir, "release.json"), encoding="utf-8") as handle:
            listed = {item["path"] for item in json.load(handle)["files"]}
        on_server = {os.path.relpath(os.path.join(base, name), remote).replace(os.sep, "/")
                     for base, _, names in os.walk(remote) for name in names}
        assert on_server == listed | {"release.json", ".q2vt-files.json"}  # the site itself, no releases/
        with open(os.path.join(release_dir, "index.html"), "rb") as local:
            assert local.read() == (remote / "index.html").read_bytes()
        assert not dialog.btn_open.isEnabled()  # no public URL given: nothing to open
        log = dialog.logbox.toPlainText()
        assert publishing_sshd.PASSPHRASE not in log and "new or changed files" in log
        dialog.close()
    finally:
        server.stop()
