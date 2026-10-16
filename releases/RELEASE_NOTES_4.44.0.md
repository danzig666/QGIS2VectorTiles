**QWebMap 4.44.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Changed
- **Markers along lines spaced in screen units** (mm, points; *Marker line* with *Interval* placement) keep QGIS's spacing within ±2.2 % at every zoom (was ±4.4 %). QGIS keeps that spacing exact as you zoom; the web map computes the positions for each sixteenth of a zoom level, and the small difference adds up along long lines.
- **A dashed line drawn together with such markers** (letters or ticks placed in the gaps of the dashes, e.g. boundary lines with crosses or outlines with zone codes) is laid out in the same steps as the markers, so the markers stay in the gaps. Before, the dashes restarted at every tile edge and the markers drifted onto them.
- These lines take more space in the tiles (about twice as much for such marker and dash lines); other lines are not affected.

### Fixed
- **Markers made long by an offset part** (a symbol built from several markers placed apart, such as a bicycle of two circles) **or by a wide glyph**, at ordinary spacing, were drawn as a repeating pattern that bent them around corners and restarted at tile edges. They are placed one by one again; only markers less than about 1 mm apart are drawn as a pattern.
