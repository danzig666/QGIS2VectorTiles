**QGIS2VectorTiles 4.6.6 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fix: labels or fills missing after a scale range change
Giving the parcel labels a scale range (1:4000 – 1:100) and exporting again into the same folder produced a web map **without the parcel numbers**. Four of the five per-zoom copies of the parcels' transparent fill were missing too. The export reported "Export cache: 154 of 154 datasets … reused" and a warning that some style source layers have no tiles.

**Cause:** the export cache reused the label data, which was unchanged, under the rule's new name; the name includes the zoom range. But the cached GeoPackage still held its table under the old name, so the tile step read nothing. The tiles now read the table that is actually in the file.

**Who is affected:** any layer whose rules got a new zoom range while their data stayed the same. That happens when you change a label or symbol scale range, the *Scales* column, or the export's max zoom. Such a layer could silently go missing from the web map. If an earlier export lost layers this way, export again with 4.6.6.

The first export after the update regenerates everything once (the plugin code changed), then the cache works as before.

### The label style from the report, checked
For the parcel style (`foldreszletek.qml`): the label shows only between **1:4000 and 1:100**, which is zoom 15.59 – 20.91 in the web map, exactly that range. The other settings carry over too:
- the expression text, `(hrsz) street name` for public areas, otherwise `hrsz`;
- font size in map units by `fekves` (6 or 4 m), growing with zoom;
- text opacity 59 %;
- Free (angled) placement.

QGIS's *only draw labels larger than 3 px* is not converted. Within 1:4000 – 1:100 it has no effect: the smallest label (4 m at 1:4000) is about 3.8 px.

| Run | Result |
|---|---|
| `pytest tests/integration/test_export_cache.py` | 3 passed (new: after a label scale range change every dataset is reused and the tiles equal a fresh export with labels present; this test fails without the fix) |
| `pytest tests/unit/test_export_cache.py` + PMTiles suite | 23 passed, 1 skipped |
| Test plan, `foldreszletek.qml` applied, export reusing the cache | parcel numbers in the tiles at zooms 15.59 – 20.91 (2019 / 970 / 726 / 281 / 138 labels at zooms 15 – 18.5), no "no tiles" warning; before the fix, 0 labels |
