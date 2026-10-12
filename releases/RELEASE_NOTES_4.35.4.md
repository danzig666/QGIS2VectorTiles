**QWebMap 4.35.4**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fix
- **Tiles flooded with a polygon's colour**: a polygon that is valid in the project CRS (here EOV) can cross itself once reprojected to Web Mercator, where two of its edges nearly touch. The tile writer then clipped the crossing ring into whole tiles filled with the polygon's colour, at some zoom levels only. Polygons are now repaired after the reprojection as well as before it.
