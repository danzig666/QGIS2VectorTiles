"""Generic extension points used by the variant branches (docs/BRANCHES.md):
presets are discovered from publishing/presets (a variant only adds a
module), the Publish window offers them only when there are some and
applies them, and the build tool makes a variant zip from variant.json."""

import json
import os
import subprocess
import sys
import zipfile

from qgis.PyQt.QtWidgets import QMessageBox

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_preset_modules_are_discovered_and_applied(plugin, monkeypatch, tmp_path):
    from q2vt_plugin.src.publishing import presets  # pylint: disable=import-error
    folder = os.path.dirname(presets.__file__)
    path = os.path.join(folder, "zz_test_preset.py")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("PRESET_ID = 'zz-test'\nTITLE = 'ZZ test preset'\n"
                     "def apply(project, profile):\n    profile.accent_color = '#123456'\n"
                     "    return ['accent set']\n")
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    try:
        titles = [m.TITLE for m in presets.available()]
        assert "ZZ test preset" in titles
        from q2vt_plugin.src.gui.publish_dialog import PublishDialog  # pylint: disable=import-error
        dialog = PublishDialog(iface=None)
        assert dialog.btn_preset.isVisibleTo(dialog) and dialog.btn_preset.menu().actions()
        module = next(m for m in dialog.presets if m.PRESET_ID == "zz-test")
        dialog.apply_preset(module)
        assert dialog.collect().accent_color == "#123456"
        dialog.close()
    finally:
        os.remove(path)
        sys.modules.pop("q2vt_plugin.src.publishing.presets.zz_test_preset", None)
    assert "ZZ test preset" not in [m.TITLE for m in presets.available()]


def test_variant_zip(tmp_path):
    variant = tmp_path / "variant.json"
    variant.write_text(json.dumps({"suffix": "xx", "name": "QWebMap XX"}))
    out = tmp_path / "plugin.zip"
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_release.py"),
                    "--variant", str(variant), "--out", str(out)], check=True, capture_output=True)
    with zipfile.ZipFile(out) as archive:
        meta = archive.read("QWebMap/metadata.txt").decode("utf-8")
    original = open(os.path.join(ROOT, "metadata.txt"), encoding="utf-8").read()
    assert "name=QWebMap XX" in meta
    strip = lambda text: [l for l in text.split("\n") if not l.startswith("name=")]  # noqa: E731
    assert strip(meta) == strip(original)  # same version and everything else
