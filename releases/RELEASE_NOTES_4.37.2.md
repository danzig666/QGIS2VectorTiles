**QWebMap 4.37.2**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Publish window
- **Viewer options use the window's width**: the Viewer checkboxes on the Interaction tab were always in 3 columns, so they took 6–7 rows even on a wide window. They now take as many columns as fit (each column as wide as its longest option), with the Google Maps API key field after the last one: on a wide window that is 4 rows, leaving more room for the layer list and the field table below. A narrower window gets fewer columns, so the window stays freely resizable.
