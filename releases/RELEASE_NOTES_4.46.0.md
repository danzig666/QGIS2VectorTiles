**QWebMap 4.46.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Changed
- **The published area (Publish window → *Map* tab → *Extent*) is chosen explicitly**:
  - *Current map view (at each export)* — the default: what the QGIS map shows when you export. Before such an export the window says so, with the area's size and the number of tiles, outlines it on the map, and lets you cancel; *Don't ask again for this project* turns the question off.
  - *Extent of a layer* — follows the layer when its data changes.
  - *Fixed area (drawn or frozen)* — drawn on the map (*Draw…*) or the current view frozen (*Freeze view*, was *Map canvas*).
- **Show on map** outlines the area that will be published on the QGIS map, in every mode.

### Fixed
- The window took the map view when it first opened and kept it as a fixed extent: exports weeks later published that old view instead of the current one. Projects saved before this version without an extent layer now take the current map view (draw or freeze the area again if you had set one).
