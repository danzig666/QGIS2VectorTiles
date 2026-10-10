**QWebMap 4.31.2**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fix
- **Free (angled) polygon labels sit in the middle of long strips.** Along a long polygon of even width (a street, a strip of land) the web map put a turned label wherever its search for the roomiest spot ended, often towards one end of the strip. Like QGIS, it now takes the spot nearest the polygon's centroid (of its visible part).

| Run | Result |
|---|---|
| Tests (full suite, each file in its own process, on 4.31.1's code) | 893 passed, 5 skipped |
| Viewer, label and browser comparison tests after this change | all passed |
