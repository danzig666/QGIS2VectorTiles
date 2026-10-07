**QWebMap 4.17.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Point patterns drawn marker by marker, as in QGIS
A point pattern fill whose marker clipping is not "Clip to shape" ("Marker centroid within shape", "Marker completely within shape", "No clipping"; e.g. the "Cross-Stitch" style) is drawn by QGIS as whole markers: along the edges no marker is cut, so the outline follows the markers, not the polygon. The web map used to fill such polygons with a texture cut at the edge. Now:

- The markers are whole, on QGIS's grid: from the top-left corner of the feature, the row on the top edge included.
- Overlapping markers are stacked as QGIS draws them (column by column from the left, each column from the top), with no seams where a large polygon is split for export.
- They are drawn smoothly at fractional pixel positions, as in QGIS, without bands.

The marker spacing follows the zoom in eighths of a zoom level (within ±4.5 %), so such layers export one dataset per eighth of a zoom.

### "Clip to shape" point patterns use QGIS's own texture
QGIS fills these polygons with a texture two markers wide, cut to whole pixels, with the markers stacked in its own order. The web map now uses exactly that texture, so overlapping markers look the same (in tests the colours match to 1.4 %, were 19 % off).

### Fixes
- Markers sized in pixels were drawn at half size on high-resolution (retina) screens.

### Known differences
- With "Align pattern to: Viewport" QGIS starts the marker grid at the corner of the map window, so which markers touch the edges changes whenever you pan. The web map starts it at the map origin: the markers are whole and look the same, but along the edges they can differ from QGIS's by one row or column. With "Feature" alignment the grid matches QGIS.

| Run | Result |
|---|---|
| `pytest tests/browser/test_browser_parity.py -k "pattern or hatch or viewport or feature or offsets or dash or overlapping"` | 30 passed (new: overlapping markers, whole markers 2.0 % of pixels off, was 4.8 %; "Clip to shape" 1.4 % of colours off, was 19 %) |
| `pytest tests/integration/test_materialize.py -k "pattern or grid or point or hatch or texture or svg or raster"` | 39 passed (new: whole markers in QGIS's order across the pieces of a large polygon) |
| `pytest tests/integration/test_end_to_end.py tests/integration/test_sprites.py tests/integration/test_more_symbology.py tests/unit/test_patterns_and_assets.py` | 62 passed |
| Cross-Stitch on a rectangle at zoom 16.5 | "Clip to shape": identical to QGIS (0 % of pixels off, was 10.8 %); Feature-aligned: whole stitches, 12 % of pixels off (was 14 %, cut stitches), the rest is the ±4.5 % spacing; Viewport-aligned (as in the style): 17 % (was 3 %): the grid starts at the map origin, not at the window corner |
