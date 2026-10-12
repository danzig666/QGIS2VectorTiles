**QWebMap 4.35.2**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fix
- **A legend entry without its layer** — a layer QGIS could not create, such as a virtual layer over a missing source — stopped the export with `'NoneType' object has no attribute 'id'`. Such entries are now skipped.
