**QGIS2VectorTiles 4.7.5 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Layers switch from the legend when the Layers tab is off
With the Layers tab switched off (Interaction tab → "Layers tab"), visitors had no way to switch off a background, such as an orthophoto or other raster layer.

Now each layer in the **Legend** gets an on/off switch in that case:
- A layer with several entries has the switch next to its heading; a layer with one entry has it on its row.
- A switched-off layer stays in the legend as one dimmed row, so it can be switched on again. This also holds in "only what is visible" mode.
- Layers set as not switchable ("Toggleable" off in the Publish window) get no switch.
- The switches use the same state as the Layers tab: shared links and saved views keep them.
- The bundled OpenStreetMap basemap keeps its own switch on the map ("Basemap" button), whether the Layers tab is on or off.

| Run | Result |
|---|---|
| `pytest tests/browser/test_web_viewer_parcel.py` | 5 passed (new: without the Layers tab, the legend switches a layer off and keeps it listed) |
| browser features, static package, basemap/raster suites + the above | 21 passed |
| Test plan with the Layers tab off | "Épületek" switched off from the legend: the buildings disappear, the row stays (dimmed), switched on again |
