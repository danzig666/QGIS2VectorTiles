**QWebMap 4.13.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Plugin upgrade without an error
Upgrading or reloading the plugin showed *Error while unloading plugin QWebMap — RuntimeError: wrapped C/C++ object of type QGIS2VectorTilesPorvider has been deleted*. The plugin registered its Processing provider twice, and QGIS deleted the second copy. The old copy then stayed loaded with the old code. Now the provider is registered once and removed cleanly, and an upgrade replaces a provider left behind by an earlier version.

The error may still appear **once**, while upgrading *to* this version, because QGIS then unloads the old version's code. Restarting QGIS after that upgrade clears it; later upgrades do not show it.

| Run | Result |
|---|---|
| `pytest tests/integration/test_plugin_package.py` | 1 passed (load as QGIS does, unload, upgrade over a leftover provider; fails without the fix) |
| `pytest tests/integration/test_publish_dialog.py` | 4 passed |
