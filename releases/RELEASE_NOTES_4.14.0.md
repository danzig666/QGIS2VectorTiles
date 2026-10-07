**QWebMap 4.14.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Every built-in QGIS symbol type on the web
These were missing or approximate before and now look like QGIS:

- **Lineburst** lines (the gradient across the line, round caps and joins included).
- **Interpolated lines** (colour and width changing along each line).
- **Raster image lines** (the image repeated at QGIS's size, start and orientation).
- **Vector field markers** (a line from each point by its vector).
- **Merged features** and **inverted polygons** renderers.
- **Heatmaps**, drawn by the browser with QGIS's radius and colours.
- **Point cluster** renderer: points grouped the way QGIS groups them at each zoom, with the cluster symbol and its count.
- **Point displacement** renderer: overlapping points spread on a ring, concentric rings or a grid around the centre symbol, with the circle or grid lines.

### Fixes
- **Pattern fills keep their size and stay sharp.** Point, SVG, raster and line-hatch fills in screen units were drawn up to 1.4× too large or too small between zoom levels, and slightly blurred. They now have exactly the QGIS size at every zoom and are pixel-sharp. Line hatches use the same whole-pixel spacing as QGIS.
- **Gradient fills are smooth.** Rainbow and other colourful gradients showed visible stripes; the colour now changes in steps of about one colour level, as in QGIS.
- **Arrows** (e.g. the "pointing arrow" style): the body tapers from the start width to the end width as in QGIS, the heads have the QGIS size, and every fill layer of the arrow is drawn, including the black drop shadow.
- **Marker lines** with an interval in screen units (e.g. "cat trail"): the spacing was up to 19 % off between zoom levels; it now stays within 4 % of QGIS.

Layers using screen-size marker intervals, point clusters or point displacement now export a dataset per eighth of a zoom level, so their export takes longer.

### Known differences
- A line image or dash pattern starts again where a vector tile cuts the line (a limit of browser vector tiles).
- QGIS fits a gradient to the visible part of a feature that runs out of the view; the web map keeps the gradient of the whole feature.
- Pattern fills are fixed to the map; QGIS starts them at each feature's top-left corner, so the pattern can be shifted by part of its spacing (same size and look).

| Run | Result |
|---|---|
| `pytest tests/integration/test_more_symbology.py` | 10 passed (new: cluster, displacement ring and grid, interpolated line, vector field, merged and inverted polygons compared with QGIS; heatmap; lineburst and raster line images) |
| `pytest tests/integration/test_gradient_fills.py` | 11 passed (now requires about one colour level of accuracy, was six) |
| Pattern, arrow, marker-line and sprite tests (`test_materialize.py -k "pattern or arrow or interval or marker_line or screen"`, `test_sprites.py`, `test_patterns_and_assets.py`) | passed |
| `pytest tests/browser/test_browser_parity.py -k "pattern or hatch or dash or marker or interval"` | passed (screen-unit pattern spacing now within 3 % between zooms, was 30 %) |
| `pytest tests/integration/test_end_to_end.py` | 21 passed |
