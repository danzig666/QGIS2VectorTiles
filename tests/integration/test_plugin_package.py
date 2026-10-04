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
FOLDER = "QWebMap"

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
        def messageBar(self): return Bar()

    class Bar:
        def pushMessage(self, title, text, *args): calls.append(("message", title, text[:40]))

    import qgis.utils
    qgis.utils.available_plugins.append("QGIS2VectorTilesFork")  # the pre-rename copy
    import QWebMap as plugin_package
    assert plugin_package.__file__.startswith(sys.argv[1]), plugin_package.__file__
    registry = QgsApplication.processingRegistry()
    plugin = plugin_package.classFactory(Iface())
    plugin.initProcessing()  # QGIS does this first (hasProcessingProvider=yes), then initGui
    plugin.initGui()
    assert registry.providerById("QGIS2VectorTilesFork") is plugin.provider
    plugin.show_publish_dialog()
    assert plugin.dialog is not None and plugin.dialog.isVisible()

    from QWebMap.src.publishing.providers.s3 import sdk_versions
    from QWebMap.src.publishing.pmtiles_builder import build_pmtiles  # noqa: F401
    from QWebMap.src.publishing import controller, deployments  # noqa: F401
    versions = sdk_versions()
    plugin.unload()
    assert registry.providerById("QGIS2VectorTilesFork") is None
    # Updated without restarting QGIS: QGIS imports the package again; a
    # module first imported late (the export cache) must not stay old.
    import QWebMap.src.core.export_cache as old_cache
    old_cache.STALE = True
    del sys.modules["QWebMap"]
    import QWebMap  # noqa: F811
    import QWebMap.src.core.export_cache as new_cache
    assert not hasattr(new_cache, "STALE"), "stale module after a plugin reload"
    # An upgrade over a version that left its provider registered (its unload
    # failed): the new provider replaces it and unloads cleanly.
    stale = QWebMap.QGIS2VectorTilesPorvider()
    registry.addProvider(stale)
    again = QWebMap.classFactory(Iface())
    again.initProcessing()
    again.initGui()
    assert registry.providerById("QGIS2VectorTilesFork") is again.provider
    again.unload()
    assert registry.providerById("QGIS2VectorTilesFork") is None
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
                         "src/publishing/raster_tiles.py", "src/publishing/basemap.py",
                         "src/publishing/parcel_report.py", "resources/web_viewer/parcel_report.mjs",
                         "resources/basemaps/protomaps/hu-light.json",
                         "resources/web_viewer/basemap.mjs", "resources/web_viewer/icons.mjs",
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
    assert "('menu', 'QWebMap')" in result.stdout
    # the pre-rename copy is reported
    assert "('message', 'QWebMap', 'QGIS2VectorTiles (fork) is the old name " in result.stdout
    assert "('untoolbar',)" in result.stdout and "('unmenu',)" in result.stdout
    if "'vendored': True" not in result.stdout:  # a system boto3 takes precedence
        assert "'boto3'" in result.stdout
