**QWebMap 4.30.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fixes
- **Export stopped on labels from older projects** with *ValueError: -1 is not a valid Qgis.LabelMultiLineAlignment*. Labels made in older QGIS versions can keep an unset multi-line alignment, which PyQGIS cannot read. Such labels are now left aligned, as QGIS draws them.
- **Shapeburst fills** with a distance in millimetres were a single colour on the web: the distance was converted at a zoom far below the exported ones. They now shade from the edge inwards like in QGIS.
- **Labels of layers with symbols placed per zoom** (for example arrows along a river, or one-way arrows on roads) were shown at the last zoom only. They now show at every zoom where QGIS shows them.
- **Letter spacing of labels** is now converted (letter-spaced names were narrower on the web).

| Run | Result |
|---|---|
| A real 64-layer zoning plan (styles of the project that stopped) | every label converted; full export and viewer without errors |
| Tests (full suite, each file in its own process) | 707 passed, 5 skipped |
