**QGIS2VectorTiles 4.5.2 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Label placement fixes
- **Horizontal** (and Free) polygon labels now go where the polygon has the most room — the middle of a zone — as QGIS places them; they used the centroid, which can lie near an edge of long or bent polygons.
- After panning, labels move back to the middle of what is visible instead of staying at an old spot near an edge.
- A label is not shown when only a sliver of its polygon is in view or when it would not fit on the screen.
- Labels that compete for the same spot (e.g. a zone code and the parcel number of the same parcel) keep clear of each other; the higher QGIS priority / z-index gets the middle.

Re-export (or re-publish) the map to get the new labels.
