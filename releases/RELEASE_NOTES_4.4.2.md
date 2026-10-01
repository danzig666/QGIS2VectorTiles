**QGIS2VectorTiles 4.4.2 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition. The Hungarian zoning-plan edition (HÉSZ, `szab_ov`) is released from the `hu-hesz` branch as `QGIS2VectorTilesFork-4.4.2-hu.zip`; see `docs/BRANCHES.md`.

### Fix
- **Visible-polygon labels no longer lag behind the other labels.** Labels placed on the visible part of their polygon (QGIS "Centroid: visible polygon") are now computed as the polygon tiles arrive and while the map moves, instead of after everything has loaded. Labels of polygons just off screen are placed in advance, so panning reveals them already drawn. Only changed labels are sent to the map. In the test measurements, the median delay after a pan fell from 540 ms to 0 ms. Re-export (or re-publish) a map to get the new viewer.
