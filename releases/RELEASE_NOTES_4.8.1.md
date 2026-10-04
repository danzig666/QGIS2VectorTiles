**QGIS2VectorTiles 4.8.1 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Web basemaps straight from QGIS's XYZ connections
Publish window → Basemap tab → **Web basemaps**. The table now lists **every XYZ connection saved in QGIS** (Browser → XYZ Tiles):
- **Use** ticks the ones the web map offers. The ticked ones come first, in their order in the basemap menu.
- **Name** is the QGIS connection's name. It is fixed for existing connections, so editing never creates a duplicate.
- **Attribution and zooms** can be edited, and the changes are saved back to the QGIS connection.
- **https only:** a connection with a plain `http://` address is listed but cannot be ticked, because web pages can only load https tiles. Its tooltip says so.
- **Add new…** creates a new connection, saved in QGIS too.
- **Reload from QGIS** lists connections added meanwhile in the Browser panel.

The start basemap ("Basemap shown at start") is now at the top of the tab and covers the bundled styles and the web basemaps. The tab scrolls, so the table has room. Web basemaps in a project or an imported settings file are ticked, and an import also adds them to QGIS, as before.

| Run | Result |
|---|---|
| `pytest tests/integration/test_publish_dialog_extent.py` | 8 passed (new: QGIS connections listed unticked, names fixed, http ones not offered; ticking one offers it, an edited attribution is saved back to QGIS) |
| Publish window suites + `tests/unit/test_publishing_xyz.py` | all passed |
