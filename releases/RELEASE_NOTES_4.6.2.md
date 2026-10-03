**QGIS2VectorTiles 4.6.2 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Export cache really reuses unchanged layers
- GeoPackage layers were redone on every export after QGIS had them open. The cache compared the GeoPackage file's time and size, which SQLite changes when QGIS merely opens and closes the file. Now only GeoPackage's own edit stamps count (updated on every real edit).
- The export log now says **why** a layer is redone: layer data changed; style, labels or fields changed; export settings changed (e.g. extent, zooms, a project variable); plugin, QGIS or GDAL updated.
- The first export after this update redoes everything once (plugin updated), then unchanged layers are reused. On a 40-layer zoning plan: 242 s → 23 s.

### Web map
- **Legend:** line and outline symbols at their real width. They were drawn without a map scale, so lines sized in map units came out far too thick. A layer with a single symbol is one row (symbol + layer name), with no bold heading repeating the name.
- **Layers tab can be switched off:** Publish window → Interaction → *Layers tab*. When off, visitors see the legend only.
- **No highlight on mouse hover.** A click still selects and marks the feature.
- **A click opens one feature:** the parcel when the map has a parcel report (its report lists the zone and everything else at that place), else the topmost feature. No more "several items here — choose" list.

Re-export (or re-publish) the map to get these.

| Run | Result |
|---|---|
| `pytest tests/unit/test_export_cache.py tests/integration/test_export_cache.py` | 8 passed (new: a GeoPackage opened and touched without edits keeps its fingerprint; reasons in words; the log names the layer whose data / style changed) |
| browser, web builder and profile suites | 112 passed |
| Publish window, publishing pipeline and cache suites | 27 passed |
