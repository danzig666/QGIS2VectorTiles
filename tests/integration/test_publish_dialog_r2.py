"""Publish window, Cloudflare R2 help: the step-by-step guide, field help,
Cloudflare addresses pasted into the fields (account id and bucket taken
from them), fields shown per destination kind, the public map address
preview, and session keys saved into QGIS's encrypted store."""

from qgis.PyQt.QtWidgets import QMessageBox

from q2vt_fixtures import reset_project

ACCOUNT = "0123456789abcdef0123456789abcdef"


def _dialog(plugin, monkeypatch):
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: None)
    reset_project()
    from q2vt_plugin.src.gui.publish_dialog import PublishDialog  # pylint: disable=import-error
    return PublishDialog(iface=None)


def test_pasted_addresses_fill_the_fields(plugin, monkeypatch):
    dialog = _dialog(plugin, monkeypatch)
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("local"))
    dialog.d_account.setText(f"https://{ACCOUNT}.r2.cloudflarestorage.com/maps")
    dialog.d_account.editingFinished.emit()
    assert dialog.d_kind.currentData() == "r2"  # pasting an R2 address picks R2
    assert dialog.d_account.text() == ACCOUNT and dialog.d_bucket.text() == "maps"
    assert dialog.d_endpoint.text() == ""       # derived from the id
    dialog.d_endpoint.setText(f"https://{ACCOUNT}.eu.r2.cloudflarestorage.com")
    dialog.d_endpoint.editingFinished.emit()    # jurisdiction endpoint is kept
    assert dialog.d_endpoint.text() == f"https://{ACCOUNT}.eu.r2.cloudflarestorage.com"
    dialog.d_bucket.setText(f"https://dash.cloudflare.com/{ACCOUNT}/r2/default/buckets/terkep")
    dialog.d_bucket.editingFinished.emit()
    assert dialog.d_bucket.text() == "terkep" and dialog.d_endpoint.text() == ""
    dialog.d_bucket.setText("plain-name")       # a plain bucket name stays as typed
    dialog.d_bucket.editingFinished.emit()
    assert dialog.d_bucket.text() == "plain-name"
    profile = dialog.collect()
    assert profile.destination.account_id == ACCOUNT and profile.destination.bucket == "plain-name"
    dialog.close()


def test_fields_per_kind_address_preview_and_help(plugin, monkeypatch):
    dialog = _dialog(plugin, monkeypatch)
    dialog.show()
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("s3"))
    assert dialog.d_account.isHidden() and not dialog.d_endpoint.isHidden()
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("r2"))
    assert not dialog.d_account.isHidden()
    assert dialog.d_form.labelForField(dialog.d_endpoint).text() == "S3 API endpoint (optional)"
    assert "Account ID" in dialog.d_account.toolTip() and "Custom Domains" in dialog.d_public.toolTip()
    dialog.e_slug.setText("arlo")
    dialog.d_public.setText("https://maps.example.com/")
    assert dialog.d_address.text() == "https://maps.example.com/maps/arlo/"
    dialog.d_prefix.setText("terkepek/arlo")
    assert dialog.d_address.text() == "https://maps.example.com/terkepek/arlo/"
    dialog.d_prefix.setText("bad prefix!")
    assert "check the prefix" in dialog.d_address.text()
    dialog.show_r2_guide()
    html = dialog.r2_guide.text.toPlainText()
    for step in ("Account ID", "Create bucket", "Custom Domains", "Object Read & Write",
                 "Secret Access Key", "Test connection", "CORS policy"):
        assert step in html, step
    dialog.r2_guide.close()
    dialog.close()


def test_save_keys_stores_them_encrypted_and_selects_them(plugin, monkeypatch):
    dialog = _dialog(plugin, monkeypatch)
    stored = {}

    def fake_store(name, key, secret, existing=None):
        stored.update(name=name, key=key, secret=secret)
        return "abc1234"
    import q2vt_plugin.src.publishing.credentials as credentials  # pylint: disable=import-error
    monkeypatch.setattr(credentials, "store_auth_config", fake_store)
    selected = []
    if dialog.d_auth is not None:
        monkeypatch.setattr(dialog.d_auth, "setConfigId", selected.append)
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("r2"))
    dialog.d_bucket.setText("maps")
    dialog.save_keys()                          # nothing pasted yet: nothing stored
    assert not stored
    dialog.d_session_key.setText("AKID")
    dialog.d_session_secret.setText("SECRET")
    dialog.save_keys()
    assert stored == {"name": "R2 maps (QGIS2VectorTiles)", "key": "AKID", "secret": "SECRET"}
    assert dialog.d_session_key.text() == "" and dialog.d_session_secret.text() == ""
    assert dialog.d_auth is None or selected == ["abc1234"]
    assert "saved encrypted" in dialog.status.text()
    dialog.close()
