**QGIS2VectorTiles 4.5.6 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Publish window
- **Extent from a layer:** the Extent setting has a layer list. Pick a layer and the published area is that layer's extent, recomputed at every export, so it follows the layer when its data changes. Or click **Map canvas** for a fixed area from what QGIS shows now. The area is summarized as its size in km and its longitude/latitude range, instead of cut-off EPSG:3857 coordinates.
- **Interaction tab:** the layer list on the left has room again. A long help sentence that did not wrap made the tab very wide and squeezed the list; long layer names now wrap, with layer icons and tooltips.
- **Settings file…** (next to *Save settings*): export all publication settings to a `.q2vt.json` file, or import them — e.g. into another project or for a colleague. Layers are matched by id, else by name; layers not found are left out and listed. On import you choose whether to keep publishing the same web map (same address) or a new one. No passwords or keys are ever in the file.

| Run | Result |
|---|---|
| `pytest tests/integration/test_publish_dialog_extent.py` | 4 passed (new: extent follows a layer, fixed extent / removed layer, room for the layer list, settings file export → import into another project) |
| Publish window and profile suites | 45 passed |
