**QWebMap 4.42.3**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)


### Fixed
- **Online tiled raster layers were blurry when zoomed in:** an XYZ or WMTS layer had no "image resolution", so its default *Sharpest detail* fell back to the vector tiles' maximum zoom (e.g. 16) and everything beyond was enlarged. It now gets image tiles down to the service's finest tile level (the `zmax` of an XYZ layer, e.g. 18); the Publish window offers it as *Like the service's sharpest tiles (recommended)*. Plain WMS layers (any resolution) keep the map's zooms; local raster images were not affected.
