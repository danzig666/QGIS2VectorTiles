**QWebMap 4.42.2**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)


### Changed
- **Measuring:** a right click finishes the measurement, like in QGIS (a right-button drag still rotates the map); **Backspace** removes the last point. Double-click, Enter and Escape work as before.
- **Parcel report print:** every zone's full regulation text is printed, also those not opened in the panel (they are loaded for the print). Sections shared by several zones are still printed once.
