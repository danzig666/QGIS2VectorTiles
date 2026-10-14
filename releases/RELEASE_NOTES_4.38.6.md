**QWebMap 4.38.6**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)


### Fixed
- **Parcel report legend graphics:** the zone and restriction graphics in the parcel report are drawn like the legend's. Lines sized in map units use the map scale of the most detailed tiles (they came out far too thick), and outlines have a margin instead of being cut off at the image edge.
