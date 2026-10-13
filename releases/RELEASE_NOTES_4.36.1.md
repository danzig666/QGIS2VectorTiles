**QWebMap 4.36.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Zoomed-out load
- **Labels weigh more**: labels are the slowest part of the web map to draw — every label of a tile is laid out and tested for collisions, even the ones that end up hidden. A tile with more than 1500 labels of one layer now counts as heavy on its own, so the labels' suggested zoom moves in even when they would fit; the speed slider scales this limit (very fast: 300, very slow: 6000). Such rows are marked "Many labels: slow to draw".
- **Progress**: estimating a big project shows a progress window (layer by layer), which can be cancelled.
