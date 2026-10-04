**QWebMap 4.9.0**

**QGIS2VectorTiles (fork) is now called QWebMap.** It started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter and has grown into a complete web map publisher, so it now has its own name. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Updating from 4.8.1 or older
QWebMap installs as a **new plugin** (folder `QWebMap`, zip `QWebMap-4.9.0.zip`):
1. Install `QWebMap-4.9.0.zip` (*Plugins → Manage and Install Plugins → Install from ZIP*).
2. Uninstall **QGIS2VectorTiles (fork)** in the same window. Until you do, QWebMap shows a reminder, because both copies would add a menu and the same Processing tool.

Everything carries over: the publishing settings saved in your projects, the window size and position, the saved storage keys (stored keys keep their old name in the QGIS authentication database; new ones are named "… (QWebMap)"), and the Processing tool's id, so models and scripts still work.

### What changed
- **Menus:** *Web → QWebMap → Publish Web Map…*; the window is "Publish Web Map — QWebMap".
- **Processing:** the provider is *QWebMap* and the tool is *Export vector tile package*.
- **Log:** messages appear under the *QWebMap* tab of the Log Messages panel.
- **Published maps:** new releases name QWebMap as their generator. Maps you already published keep working unchanged.

| Run | Result |
|---|---|
| `pytest tests/integration/test_plugin_package.py` | passed (the zip installs as `QWebMap/`, the menu is "QWebMap", and the reminder appears while the old copy is installed) |
| Publish window, export, variant and end-to-end suites + `tests/unit` | all passed |
