**QWebMap 4.48.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Changed
- **Lighter tiles when zoomed out** (Publish window → *Output*, on by default): fills and thin lines smaller than half a pixel at a zoom level are left out of that zoom's tiles, and the tiles below each layer's last zoom are simplified to a quarter pixel. The last zoom of each layer keeps every feature and its exact shape.
- **Fewer style layers**: map-unit markers no longer get a style layer per zoom where nothing differs but their image (a city map: 809 → 473 layers).
- **Low memory web map** (on by default) also:
  - draws the map at a pixel ratio of at most 2;
  - releases out-of-view tiles when the tab is hidden or the map has not moved for a minute;
  - draws only plain fills and lines in the overview map.
- Polygon labels placed on the visible part of their polygon no longer make the browser triangulate those polygons.
- The viewer no longer copies the whole style on every move, layer switch or label update.
