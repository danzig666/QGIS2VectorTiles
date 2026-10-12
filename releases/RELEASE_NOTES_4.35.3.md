**QWebMap 4.35.3**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fix
- **Labels of features drawn with an invisible symbol**: a renderer rule whose symbol draws nothing (no fill and no outline — often the ELSE rule of a rule-based style) exported nothing, and its features got no labels on the web map. QGIS renders those features (invisibly) and labels them; so does the web map now.
