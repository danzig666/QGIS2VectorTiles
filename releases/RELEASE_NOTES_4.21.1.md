**QWebMap 4.21.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Faster re-exports after a scale-range change
Since 4.15, changing when a layer or its labels are shown (a scale range, or the web map's own visible scales) made the next export redo all of that layer's data, although the data itself stays the same — only its name changes. The export cache reuses it again. Gradient fills are still redone, because their colour bands depend on the first zoom.

| Run | Result |
|---|---|
| `pytest tests/integration/test_export_cache.py tests/integration/test_gradient_fills.py` | 15 passed (after a label's scale range changed, 24 of 24 datasets are reused, was 18) |
