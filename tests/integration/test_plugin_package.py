"""Clean install of the release zip (PUB-16): build it with
tools/build_release.py, unpack it into an empty plugin folder and load it in
a fresh QGIS Python process like QGIS does (classFactory, initGui, open the
Publish Web Map window, unload). Checks that the zip carries the viewer, the
vendored PMTiles writer and S3 SDK, and nothing that must stay private."""

import os
import subprocess
import sys
import textwrap
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FOLDER = "QGIS2VectorTilesFork"

SMOKE = textwrap.dedent("""
    import os, sys
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    sys.path.insert(0, sys.argv[1])
    from qgis.core import QgsApplication
    from qgis.gui import QgsMapCanvas
    from qgis.PyQt.QtWidgets import QMainWindow
    app = QgsApplication([], True)
    app.initQgis()
    for candidate in ("/usr/share/qgis/python/plugins", "/usr/lib/qgis/python/plugins"):
        if os.path.isdir(candidate):
            sys.path.append(candidate)
    from processing.core.Processing import Processing
    Processing.initialize()
    calls = []

    class Iface:
        window = QMainWindow()
        def mainWindow(self): return self.window
        def addWebToolBarIcon(self, action): calls.append(("toolbar", action.text()))
        def addPluginToWebMenu(self, menu, action): calls.append(("menu", menu))
        def removeWebToolBarIcon(self, action): calls.append(("untoolbar",))
        def removePluginWebMenu(self, menu, action): calls.append(("unmenu",))
        canvas = QgsMapCanvas()
        def mapCanvas(self): return self.canvas
        def messageBar(self): return None

    import QGIS2VectorTilesFork as plugin_package
    assert plugin_package.__file__.startswith(sys.argv[1]), plugin_package.__file__
    plugin = plugin_package.classFactory(Iface())
    plugin.initGui()
    assert QgsApplication.processingRegistry().providerById(plugin.provider.id())
    plugin.show_publish_dialog()
    assert plugin.dialog is not None and plugin.dialog.isVisible()

    from QGIS2VectorTilesFork.src.publishing.providers.s3 import sdk_versions
    from QGIS2VectorTilesFork.src.publishing.pmtiles_builder import build_pmtiles  # noqa: F401
    from QGIS2VectorTilesFork.src.publishing import controller, deployments  # noqa: F401
    versions = sdk_versions()
    plugin.unload()
    print("CALLS", calls)
    print("SDK", versions)
    app.exitQgis()
    print("OK")
""")


def test_release_zip_installs_and_loads(tmp_path):
    archive = tmp_path / "plugin.zip"
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_release.py"),
                    "--out", str(archive)], check=True, capture_output=True)
    with zipfile.ZipFile(archive) as handle:
        names = handle.namelist()
        assert all(name.startswith(f"{FOLDER}/") for name in names)
        for required in ("__init__.py", "metadata.txt",
                         "resources/web_viewer/index.html", "resources/web_viewer/app.mjs",
                         "resources/web_viewer/vendor/pmtiles.js",
                         "resources/ml_viewer/visible_labels.mjs",
                         "src/gui/publish_dialog.py",
                         "src/publishing/vendor/pmtiles/writer.py",
                         "src/publishing/vendor/s3/boto3/__init__.py",
                         "src/publishing/vendor/s3/botocore/data/s3/2006-03-01/service-2.json.gz"):
            assert f"{FOLDER}/{required}" in names, required
        assert not [name for name in names if "/tests/" in name or name.endswith(
            (".pyc", ".qgs", ".qgz", ".mbtiles", ".pmtiles", ".gpkg"))]
        handle.extractall(tmp_path / "plugins")
    result = subprocess.run([sys.executable, "-c", SMOKE, str(tmp_path / "plugins")],
                            capture_output=True, text=True, timeout=300,
                            cwd=str(tmp_path), env={**os.environ, "PYTHONPATH": ""})
    assert result.returncode == 0 and "OK" in result.stdout, result.stdout + result.stderr
    assert "('toolbar', 'Publish Web Map…')" in result.stdout
    assert "('menu', 'QGIS2VectorTiles (fork)')" in result.stdout
    assert "('untoolbar',)" in result.stdout and "('unmenu',)" in result.stdout
    if "'vendored': True" not in result.stdout:  # a system boto3 takes precedence
        assert "'boto3'" in result.stdout
