**QWebMap 4.23.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### A progress bar that follows the export
Tile generation is usually the longest part of an export (15 minutes on a large project), but the bar jumped to 85% when it started and stayed there, and the later steps restarted it from zero.
- Every step now has its own share of the bar, and the bar only moves forward. Tile generation gets most of it.
- The bar moves **during** tile generation. The tile tool reports no progress, so it is estimated from each layer's data size and zoom range, and corrected by how fast the layers already finished went. The log says "about N% done".
- The largest layers start first, so no big one is left running alone at the end.

### Export cache log after an update
After a plugin update the log says only "plugin, QGIS or GDAL updated". It no longer lists settings of the older version as changes (such as project variables it no longer keeps).

| Run | Result |
|---|---|
| Progress, export cache, publishing pipeline and Publish window tests | 29 passed (tile generation now starts below 40% of the bar and spans over 40 points; it was 85%) |
