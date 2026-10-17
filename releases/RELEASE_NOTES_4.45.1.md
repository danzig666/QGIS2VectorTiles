**QWebMap 4.45.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fixed
- **A false warning in every web map** ("Polygon labels on the visible part are not available in this browser build"): the viewer still expected MapLibre 6.11.2 after the upgrade to 6.14.0. The labels themselves worked.
- **"Hide labels smaller than"** (Publish window, Output) now also hides labels whose map-unit size is data-defined (e.g. `if("fekves" = 2, 6, 4)`), each by its own size. They were shown at every size.
- **"Label every feature"** shrinks a label that does not fit no smaller than the "Hide labels smaller than" size (it shrank to half, unreadably small); it sticks out of its polygon instead.
- **Export cache**: adding a layer to the export redid every other layer ("export settings changed (view)"). Only the map view variables the styles actually use count now. The first export after updating redoes everything once.
