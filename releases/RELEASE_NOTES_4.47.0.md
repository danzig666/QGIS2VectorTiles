**QWebMap 4.47.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Changed
- **Less browser memory.** A detailed city map (50 000 parcels) took over 1.5 GB in the browser.
  - **A fill and its outline share one copy of the polygons** in the tiles (the outline was exported again as lines): such layers take about half the tile data and memory. A transparent fill under an outline is now free again — keep it where visitors click inside the polygons (popups, parcel report).
  - **Low memory web map** (Publish window → *Output*, on by default): the browser keeps the tiles of about one zoom level besides those on screen (it kept five); places you return to are loaded again, usually from the browser's cache.
