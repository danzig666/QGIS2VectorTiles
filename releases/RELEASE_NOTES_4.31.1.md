**QWebMap 4.31.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fix
- **Label frames turn with their label.** A label background set to turn with the label (*Sync with label*, QGIS's default, or *Offset of label*) stayed level on the web: for example zone codes placed *Free (angled)* along narrow polygons had turned text in a level frame. The frame now turns with the text, as in QGIS, and keeps its corners (MapLibre left the corners of a turned frame unturned, so they stuck out).

| Run | Result |
|---|---|
| Tests (full suite, each file in its own process) | 893 passed, 5 skipped; after the last change the browser, label and viewer tests again: all passed |
