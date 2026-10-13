**QWebMap 4.36.5**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fixes
- **Map stuck on "Loading…"**: 4.36.4 fixed the export crash of point pattern fills with a scale-limited map-unit row distance, but it could then publish a style layer for the pattern that starts past zoom 24. MapLibre rejects a whole style with such a layer, so the web map never loaded. The pattern is no longer split past the last zoom the map shows. Please publish again.
- **Checked before publishing**: every export now checks the finished style and leaves out any layer the browser would reject (a zoom outside 0–24, a minimum zoom not below the maximum, a value that is not a number), with a warning in the export report, so one bad layer can no longer stop the whole map from loading.
- **Viewer error instead of endless loading**: if a map style still fails to load, the viewer shows "The map style is invalid" with the technical detail, instead of "Loading…" for ever.
