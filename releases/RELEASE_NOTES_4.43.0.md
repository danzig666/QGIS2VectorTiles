**QWebMap 4.43.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

QGIS styles look more like QGIS on the web. Checked against 853 styles of the QGIS Hub, each drawn by QGIS and by the web viewer: the average difference fell from 5.5 % to 3.4 %, and the styles more than 20 % off from 81 to 42.

### Fixed
- **Hatches made of markers** (arrows, font characters, dashes, several line layers) are drawn the way QGIS fills them, at QGIS's own pixel-rounded spacing; a pencil hatch with random dots keeps QGIS's exact spacing. They were a dense dark mesh.
- **Zero-width hatch lines** are QGIS's one-pixel hairlines (they were invisible).
- **Dense marker lines** (markers closer than their size: moss, sketchy lines) show every marker; the web map kept about one in five.
- **Glowing (blurred) lines** fade out as far as QGIS's blur does.
- **Raster lines with an embedded image** show the image (they were a faint preview).
- **Geometry generators using aggregate functions** (`collect`, `sum`...) draw their geometry; it was empty.
- **`@symbol_color`** in nested symbols and in geometry generators takes the colour of the symbol that draws it.
- **Hash lines of arrows or other markers** are drawn with that marker.
- **Point pattern fills with an angle** turn the grid and keep the markers upright, as QGIS does.
- **Markers with several layers turned by a data-defined angle** keep each layer's angle (a measure line's arrowheads lay sideways).
- **Marker values computed from the line** (`$length`...) use the whole line.
- **Arrow drop shadows**, a fill's effect on its outline, and first/last vertex markers moved along the line, as in QGIS.
- **Small circles** are no longer fainter than in QGIS.
- **Symbols sized in meters at scale** were empty.
- **Rotated, anchored SVG markers** that QGIS reports too large are drawn.
- Layers drawn only through lightening blend modes (*Screen*, *Addition*, *Lighten*, *Dodge*) are left out with a report: on a white map they show nothing in QGIS either.

### Fixed: exports that failed
- Geometry generators that smooth a randomized outline (millions of vertices) ran out of memory.
- A pattern fill whose marker is too large for a sprite stopped the export; it is now reported and left out.
- A polygon's geometry generator drawing lines with a screen-unit offset stopped the export.

### Changed
- The web viewer uses **MapLibre GL JS 6.14.0** (was 6.11.2): among others, `line-offset` keeps the line's width at joins, the map finishes loading when a tile fails, fill outlines show in the 3D view, and a blank-map problem on some iPads is worked around. The viewer now loads two MapLibre files (`maplibre-gl.mjs`, `maplibre-gl-worker.mjs`) instead of three.
