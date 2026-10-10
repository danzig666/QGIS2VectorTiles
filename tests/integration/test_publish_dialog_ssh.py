"""Publish window, SSH / SFTP destination: its fields (and only its fields)
for that kind, a pasted user@server:/folder (and a typed host:port or IPv6
address that must stay as typed), settings saved in the project without the
password, the review target, saved logins and public URLs never shared with
R2 / S3, closing with unfinished settings, saving a password without a user
name, the exported settings' wording, Test connection's messages without the
busy cursor, and Test connection plus a publication through the window into
a local OpenSSH server's folder (skipped without sshd / the OpenSSH
client)."""

import json
import os
import time

import pytest
from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsVectorLayer
from qgis.PyQt.QtCore import QCoreApplication, Qt, QVariant
from qgis.PyQt.QtWidgets import QApplication, QMessageBox

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
    dialog.d_public.setText("https://www.example.com/web maps/térkép")  # links percent-encoded
    dialog.refresh_review()
    assert "Target: https://www.example.com/web%20maps/t%C3%A9rk%C3%A9p/index.html" in dialog.review.toPlainText()
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


class _AuthSelection:
    """Stands in for the QGIS authentication selector (no auth database)."""

    def __init__(self, dialog, monkeypatch):
        self.selected = ""
        if dialog.d_auth is None:
            pytest.skip("no QgsAuthConfigSelect")
        monkeypatch.setattr(dialog.d_auth, "setConfigId", self.select)
        monkeypatch.setattr(dialog.d_auth, "configId", lambda: self.selected)

    def select(self, value):
        self.selected = value


def test_ssh_never_uses_the_saved_keys_or_address_of_r2(project, messages, monkeypatch):
    """Switching R2 → SSH must not hand the R2 keys to ssh (as user and
    password), nor keep the R2 domain as the folder's public URL; switching
    back brings the R2 values back."""
    dialog = _dialog()
    auth = _AuthSelection(dialog, monkeypatch)
    import q2vt_plugin.src.publishing.credentials as credentials  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.providers.base import Credentials  # pylint: disable=import-error
    loaded = []

    def fake_load(ref, ssh=False):
        loaded.append((ref, ssh))
        return Credentials("R2ACCESSKEYID", "R2-SECRET-ACCESS-KEY") if ref == "r2keys1" else \
            Credentials("", "ssh-saved-password")
    monkeypatch.setattr(credentials, "from_auth_config", fake_load)
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("r2"))
    dialog.d_account.setText("0123456789abcdef0123456789abcdef")
    dialog.d_bucket.setText("maps")
    dialog.d_prefix.setText("Not A Prefix!")  # invalid, and hidden for SSH
    dialog.d_public.setText("https://maps.example.com")
    dialog.d_auth.setConfigId("r2keys1")
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("ssh"))
    assert dialog.d_auth.configId() == "" and dialog.d_public.text() == ""
    assert "SSH user" in dialog.d_auth.toolTip() and "R2" not in dialog.d_auth.toolTip()
    dialog.d_ssh_host.setText("www.example.com")
    dialog.d_ssh_dir.setText("/srv/map")
    dialog.collect()
    provider = dialog._provider()  # pylint: disable=protected-access
    assert (provider.user, provider.password) == ("", "") and not loaded  # keys / ssh-agent only
    assert dialog.save_settings(), messages  # the hidden R2 prefix does not block SSH
    dialog.d_auth.setConfigId("sshpw1")  # a password saved for SSH
    dialog.collect()
    assert dialog._provider().password == "ssh-saved-password"  # pylint: disable=protected-access
    assert loaded == [("sshpw1", True)]
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("r2"))
    assert dialog.d_auth.configId() == "r2keys1" and dialog.d_public.text() == "https://maps.example.com"
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("s3"))  # object storage: the same keys
    assert dialog.d_auth.configId() == "r2keys1"
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("ssh"))
    assert dialog.d_auth.configId() == "sshpw1" and dialog.d_public.text() == ""
    assert auth.selected == "sshpw1"
    dialog.close()


def test_typed_server_address_stays_as_typed(project, messages):
    dialog = _dialog()
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("ssh"))
    dialog.d_ssh_dir.setText("/var/www/html/map")
    for typed, host, port in (("www.example.com:2222", "www.example.com", 2222),  # host:port → the port
                              ("2001:db8::10", "2001:db8::10", 2222), ("192.0.2.7", "192.0.2.7", 2222),
                              ("https://www.example.com/map", "https://www.example.com/map", 2222)):
        dialog.d_ssh_host.setText(typed)
        dialog.d_ssh_host.editingFinished.emit()
        assert (dialog.d_ssh_host.text(), dialog.d_ssh_port.value()) == (host, port), typed
        assert dialog.d_ssh_dir.text() == "/var/www/html/map", typed  # never rewritten by a host
    dialog.d_ssh_host.setText("deploy@www.example.com:/srv/other")  # an explicit folder: taken, and said
    dialog.d_ssh_host.editingFinished.emit()
    assert dialog.d_ssh_dir.text() == "/srv/other" and "folder /srv/other" in dialog.status.text()
    dialog.close()


def test_password_without_user_export_wording_and_test_connection_cursor(project, messages, monkeypatch,
                                                                          tmp_path):
    dialog = _dialog()
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("ssh"))
    dialog.d_ssh_host.setText("www.example.com")
    dialog.d_ssh_dir.setText("/srv/map")
    import q2vt_plugin.src.publishing.credentials as credentials  # pylint: disable=import-error
    stored = []
    monkeypatch.setattr(credentials, "store_auth_config",
                        lambda name, user, password: stored.append((name, user, password)) or "sshpw2")
    dialog.d_ssh_password.setText("pw for the default user")
    dialog.save_ssh_password()  # no user name: ssh's default user, as in the User field
    assert stored == [("SSH www.example.com (QWebMap)", "", "pw for the default user")], messages
    assert dialog.d_ssh_password.text() == ""
    assert dialog.export_settings_file(str(tmp_path / "town.q2vt.json"))
    assert dialog.status.text().endswith("(without the SSH password).")
    from q2vt_plugin.src.publishing.errors import PublishingError  # pylint: disable=import-error
    cursors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: cursors.append(("box", QApplication.overrideCursor())))

    class Refused:
        def inspect(self):
            cursors.append(("login", QApplication.overrideCursor()))
            raise PublishingError("Q2VT_PUB_CREDENTIALS", "The server refused the login.")

    def provider():  # QGIS may ask for its master password here: never under the busy cursor
        cursors.append(("provider", QApplication.overrideCursor()))
        return Refused()
    monkeypatch.setattr(dialog, "_provider", provider)
    dialog.test_connection()
    assert [name for name, _ in cursors] == ["provider", "login", "box"]
    assert cursors[0][1] is None and cursors[1][1] is not None and cursors[2][1] is None, cursors
    assert QApplication.overrideCursor() is None
    dialog.close()


def test_closing_with_unfinished_settings_keeps_the_saved_ones(project, messages):
    project, _ = project
    from q2vt_plugin.src.gui import publication_profiles as store  # pylint: disable=import-error
    dialog = _dialog()
    dialog.e_title.setText("Town map")
    assert dialog.save_settings(), messages
    saved = store.active_profile(project)[0]
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("ssh"))  # no server, no folder yet
    assert "embed code needs the map's web address" in (dialog.copy_embed_code() or dialog.status.text())
    dialog.close()
    again = store.active_profile(project)[0]  # still loadable: the last valid settings
    assert (again.title, again.publication_id, again.destination.kind) == \
        ("Town map", saved.publication_id, saved.destination.kind)
    reopened = _dialog()
    assert reopened.e_title.text() == "Town map"
    reopened.close()


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
        assert dialog.status.text().startswith("Published into "), (dialog.status.text(), messages)
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
