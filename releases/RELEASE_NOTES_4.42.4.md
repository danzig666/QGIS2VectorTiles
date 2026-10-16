**QWebMap 4.42.4**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)


### Fixed
- **Overview map out of step with the main map:** a layer switched on or off, a theme or another basemap chosen while the overview was still loading tiles (usually right after moving the map) was never applied to it. It now always follows.

### Changed
- **Overview map** shows a wider area: 6 zoom levels further out than the main map (was 4).
- **Basemap switcher:** each web basemap (XYZ/WMS) shows a real image tile of the current place instead of the same placeholder; the tiles are loaded only when the menu is opened, so the page still contacts no basemap server until then. *No basemap* shows the project's own background colour.
