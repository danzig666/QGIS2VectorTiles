**QWebMap 4.36.4**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fixes
- **Export crash (RecursionError)**: a point pattern fill whose row distance is in map units with a scale range ("Min/max size" in the unit settings), and whose Y displacement is 0, stopped the export with "maximum recursion depth exceeded". QGIS draws such a pattern with the Y displacement's scale range; the export now clears that range too when it splits the pattern by zoom.
- **Markers cut off zoomed in**: a large marker in map units drawn with a fill outline (e.g. a 5 m circle with a 0.8 m ring) was cut off at its sides from about zoom 20, because the outline reaches past the marker's size. The marker image now grows until the whole marker fits.
