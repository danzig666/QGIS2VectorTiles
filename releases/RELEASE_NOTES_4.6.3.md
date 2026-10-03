**QGIS2VectorTiles 4.6.3 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fix: export failed after updating the plugin
After installing 4.6.2 over an earlier version without restarting QGIS, the export could fail with `module '…export_cache' has no attribute 'part_hash'`. QGIS only reloads the plugin modules it saw when the plugin loaded; a module first imported later (the export cache) stayed in its old version next to new ones. The plugin now loads all its modules afresh whenever QGIS loads it.

If you still have 4.6.2 open in QGIS, restart QGIS once (or just install 4.6.3).

### Legend column in the layer list
Publish window → Map tab: a **Legend** column decides whether a layer appears in the web map's legend. It works per row, or for several rows or a group at once through **Selected layers → Show in / Hide from the legend**. It is the same setting as *Show in the legend* on the Interaction tab, and the two stay in sync.

| Run | Result |
|---|---|
| `pytest tests/integration/test_plugin_package.py` | 1 passed (the clean-install test now also loads the plugin again, as QGIS does after an update, and checks that a late-imported module is fresh) |
| `pytest tests/integration/test_publish_scale_limits.py tests/integration/test_publish_dialog*.py` | 19 passed (new: the legend column for a group at once, synced with the Interaction tab) |
