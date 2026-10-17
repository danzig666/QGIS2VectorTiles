**QWebMap 4.45.2**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

A full check of the MapLibre GL JS 6.14 upgrade (viewer, patches, rendering compared with 6.11.2) found no other problem caused by it; these bugs turned up along the way.

### Fixed
- **Labels with a data-defined size in map units** hidden by "Hide labels smaller than" (4.45.1) were copied once per zoom into the tiles; they are now cut at the exact scales of their sizes (smaller tiles, exact zooms).
- **Large map-unit markers** (drawn as their shape from about 128 px) disappeared between the last tile zoom and the zoom where they become a shape.
- **"Offset along line"** of first / last vertex markers is measured in the project CRS: in an EOV project 100 m moved them about 68 m.
- **Labels with a background frame and a text shadow** are placed by their framed size; they overlapped and stuck out of their polygons.
- **Diagrams** are drawn as soon as the map needs them (no console warning, no delay).
- **Search**: with animated markers on the map the result and its popup came after 10 seconds.
