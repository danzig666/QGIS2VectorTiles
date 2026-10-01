**QGIS2VectorTiles 4.4.3 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fix
- **Visible-polygon labels stay glued to the map while you drag or zoom.** 4.4.2 made them appear on time but kept re-centring labels near the screen edge during a drag. Now placed labels do not move while the map moves (new polygons still get their label at once); they settle onto the visible part of their polygon once the map stops. Measured over three drags: 0 label jumps or blinks during dragging (4.4.2: 17–34 jumps). Re-export (or re-publish) a map to get the new viewer.
