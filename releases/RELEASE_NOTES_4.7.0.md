**QGIS2VectorTiles 4.7.0 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Street search (OpenStreetMap)
Publish window → Interaction tab → **Search street names (OpenStreetMap)**. The web map's search box then also finds the named streets inside the **extent layer** (Map tab). Without an extent layer it uses the export extent.

- **One search box:** parcel numbers, any other searchable field and streets are all found together. A street result is marked *Street (OpenStreetMap)* with a road icon. Choosing it moves the map to the street and puts a marker halfway along it. Streets have no popup.
- **Where the names come from:** the bundled basemap, if the map has one. Without a basemap the export reads them from the Protomaps build of OpenStreetMap, which needs internet while exporting.
- **What gets published:** for each street, its name, one point and its bounds. No street geometry is published.
- **Inside the extent layer only:** the extent layer's polygons are merged and each street is cut to them, so streets of the neighbouring settlement are not offered.
- **Same name twice:** pieces of the same name more than 300 m apart become separate results, e.g. a "Fő utca" in two villages of the extent layer.
- **If it fails:** when the names cannot be read (no internet, wrong source), the map is still published without them, with a warning.

### Field search: already there for any field of any layer
Interaction tab → pick a layer → tick the **Search** column next to the fields to search, for example `hrsz` on the parcels. The same box searches every layer's ticked fields. Checked on the test plan: "862" finds the parcel 862, the building on it, then 1862/1 and so on; Enter opens the parcel's report.

### The Publish window remembers its size and position
The window opens again with the size and position it was closed with, whether by the Close button, Escape or the title bar. The values are kept in the QGIS settings, so they survive a QGIS restart. If that screen is no longer connected (a laptop undocked), the window opens with the default size on the main screen.

| Run | Result |
|---|---|
| `pytest tests/integration/test_publishing_street_search.py` | 3 passed (new) |
| `pytest tests/unit/test_publishing_basemap.py` | 16 passed (new: tile geometry decoding; streets cut to an area; streets grouped by distance) |
| `pytest tests/browser/test_web_viewer_basemap_raster.py` | 6 passed (new: one box finds a street and a parcel; a street result moves the map and marks it, with no popup) |
| `pytest tests/integration/test_publish_dialog_extent.py` | 5 passed (new: size and position restored after Close and after Escape) |
| profile, web builder, indexes, validation, Publish window, viewer features, parcel and static package suites | 104 passed |
