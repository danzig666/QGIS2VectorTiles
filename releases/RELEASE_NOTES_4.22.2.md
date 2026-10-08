**QWebMap 4.22.2**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### The export cache is used again
- **GeoPackage layers** (on Windows, also on network drives): a layer is reused when the GeoPackage's own change stamps say it is unchanged. Reading those stamps could fail on Windows paths, and the layer was then quietly redone on every export, so exporting twice in a row reused nothing. They are now read in a way that works there.
- **Memory layers** (e.g. restored by the Memory Layer Saver plugin): QGIS gives them a new random id each time the project is opened, which made them look changed after every restart. Their content decides now.
- **The log says why**: a layer that cannot be cached is listed as "Redone (not cached: …)" with the reason, instead of being redone silently.

Note: the first export after this update redoes everything once (the plugin's own code changed); from the second export on, unchanged layers are reused.

| Run | Result |
|---|---|
| `pytest tests/integration/test_export_cache.py tests/unit/test_export_cache.py tests/unit/test_dependencies_validation_capabilities.py` | 20 passed (two new tests, both failing on 4.22.1) |
| Publishing pipeline, end-to-end, publishing unit tests, static package, Publish window | 183 passed, 5 skipped |
