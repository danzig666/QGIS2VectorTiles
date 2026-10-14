**QWebMap 4.38.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Web map
- **Thin hairlines** (Publish window, *Output* tab, on by default): QGIS draws a line or outline of width 0 (a hairline, e.g. parcel boundaries) one screen pixel wide. In the browser that is two device pixels on a high-resolution screen, and where many such lines run close together (a village's parcels zoomed out) the map looks filled in. The web map now draws solid hairlines 0.5 CSS px wide: exactly one device pixel on a high-resolution screen, a lighter line on others. They widen to 0.8 px between zoom 14 and 20, so they stay visible at every zoom. Dashed hairlines keep 1 px (their dashes are measured in line widths). Turn the option off to get QGIS's look. Republish for it to take effect.

### Fixes
- **Dimension arrows were missing**: an arrow line whose body tapers to 0 width (*Arrow width* 0, e.g. a double-headed dimension line with a road width label) was not exported at all, so only its label showed. Its outline touches itself between the heads, and that shape was lost when the arrow was cleaned up for the tiles. The heads are drawn now, and the outline is drawn as QGIS strokes it, so the thin line between the heads shows too.
- A line label whose text is barely shorter than its line (e.g. "12m" on a 12 m dimension line) is now placed along the line as in QGIS: the fit test used the label box with its spacing margin instead of the bare text.
