**QWebMap 4.48.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fixed
- A polygon layer's outline no longer draws a line along the edge of the export extent where the extent cuts the polygons (since 4.47.0).
- Thin hairlines in the overview map are no longer half as wide on high-resolution screens.
- Map-unit markers with a data-defined appearance keep their image for each zoom.
- Cached tiles are made again when an outline becomes wider (it decides whether small features may be left out).
