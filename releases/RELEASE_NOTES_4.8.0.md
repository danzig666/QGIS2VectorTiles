**QGIS2VectorTiles 4.8.0 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Web basemaps (XYZ tiles)
Publish window → Basemap tab → **Web basemaps (XYZ)**. Each row is one basemap: a title, an XYZ tile address, the attribution, and min / max zoom.
- **Address format:** the QGIS / Leaflet templates: `{z}`, `{x}`, `{y}`. `{-y}` means TMS row order; `{s}` means a/b/c servers. Only `https://` addresses are accepted. For example: `https://tile.openstreetmap.org/{z}/{x}/{y}.png`.
- **Add**, **Remove**, or **From QGIS XYZ connections…**: the last one offers the XYZ tile connections already saved in QGIS (Browser → XYZ Tiles).
- **In the web map:** the basemaps appear in the map's **basemap menu** next to the bundled OpenStreetMap styles. They are drawn under the plan's layers, with their attribution in the corner. One can be the basemap shown at start ("Shown at start" → "Web: …"). They also work without a bundled basemap.
- **Loading:** the visitor's browser loads the tiles from that server while browsing, and nothing is copied into the release. The page allows exactly these servers in its security policy. It sends its own address (origin only) as referrer, which tile services such as OpenStreetMap's require. Maps without web basemaps still send none and load nothing from other sites.
- **Where the entries are kept:**
  - in the project's settings;
  - in the exported settings file, and an import brings them back;
  - as QGIS XYZ connections, under the same title. Saving or importing adds or updates them there, so they show in QGIS's Browser panel and can be picked in other projects. The attribution is stored next to them.

Use only tile addresses you are allowed to use, with the attribution the service requires.

| Run | Result |
|---|---|
| `pytest tests/unit/test_publishing_xyz.py` | 6 passed (new: profile round trip, start basemap, address checks, `{s}` / `{-y}` templates, allowed servers) |
| `pytest tests/integration/test_publish_dialog_extent.py` | 7 passed (new: the table, QGIS XYZ connections written and offered back, carried by the settings file and saved into QGIS on import) |
| `pytest tests/browser/test_web_viewer_basemap_raster.py` | 7 passed (new: in the basemap menu, raster layer under the plan, TMS scheme, attribution, page policy; still nothing from other sites unless chosen) |
| `pytest tests/integration/test_publishing_label_always.py` | 2 passed (new: a web basemap without a bundled basemap, shown at start, manifest schema) |
| Publish window, profile, pipeline, street search and viewer suites | 77 passed |
