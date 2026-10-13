**QWebMap 4.36.6**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fixes
- **False "The map style is invalid"**: 4.36.5 showed this error for a map with a layer that has no features in the export extent. MapLibre reports such a layer's missing source layer as an error, but it accepts the style and draws the map. Now only an error before MapLibre has accepted the style stops the map; everything after it is a warning, as before. Maps published with 4.36.5 work again once published with 4.36.6.
