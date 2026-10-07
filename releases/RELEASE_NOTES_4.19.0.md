**QWebMap 4.19.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Temporary layers are published
Temporary (memory, "scratch") layers — for example the ones the Memory Layer Saver plugin keeps with the project — were published empty, and the web map said "not visible at this zoom" for them. The export read each layer again from its source, and a temporary layer has none: it came back empty. Now their features are taken from the open project. An unchanged temporary layer is reused from the export cache like any other layer.

### The web map stays on its extent
New option in the Publish window, under **Extent**: **Keep the web map on the extent** (on by default). Visitors cannot pan away from the published area, and they can zoom out only a little beyond the view of the whole extent. Shared links are kept inside it too. Switch it off to let the map move freely, as before.

| Run | Result |
|---|---|
| `pytest tests/integration/test_publishing_pipeline.py -k temporary` | 1 passed (new: a temporary layer is published and reused from the cache; without the fix it has no data) |
| `pytest tests/browser/test_web_viewer_features.py tests/browser/test_web_viewer_parcel.py tests/browser/test_web_viewer_basemap_raster.py tests/browser/test_snap.py tests/browser/test_coordinates_crs.py` | 27 passed (new: the map stays on the extent) |
| `pytest tests/browser/test_web_viewer_streetview.py`, 4 runs | 6 passed each time (the test no longer depends on Google's coverage tiles) |
| `pytest tests/browser/test_pmtiles_transport.py tests/browser/test_browser_smoke.py tests/browser/test_static_package.py` | all passed |
| `pytest tests/unit/test_publishing_profile.py tests/unit/test_publishing_web_builder.py tests/integration/test_publish_dialog*.py tests/integration/test_publishing_pipeline.py tests/integration/test_publishing_raster.py tests/integration/test_end_to_end.py` | 93 passed |
