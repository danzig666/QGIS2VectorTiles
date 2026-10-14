**QWebMap 4.38.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Web map
- **Thin hairlines on high-resolution screens**: in 4.38.0 a thin hairline was 0.5 CSS px wide. On a 4K screen (Windows scaling 150–200 %) that is a whole physical pixel, the same as QGIS's hairline, so it looked no thinner there. The viewer now divides the width by the screen's pixel ratio: a hairline is half a physical pixel on every screen (0.8 when zoomed far in). A pixel cannot be thinner than itself, so the line is drawn lighter, and dense parcel boundaries no longer look filled in on 4K screens either. It also adjusts when the window moves to a screen with another pixel ratio. This is a viewer change: maps published with 4.38.0 get it after publishing again.
