**QWebMap 4.15.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Arrows exactly as QGIS draws them
Arrow symbol layers (e.g. the "pointing arrow" style) are now the very shapes QGIS fills. The plugin builds them with QGIS's own arrow construction: straight and curved arrows, single, reversed and double heads, half arrows, and the way QGIS pairs the vertices of repeated and curved arrows. In tests the shapes match QGIS pixel for pixel. They are filled with the arrow's own fill symbol, outlines included. A drop-shadow layer now falls on the earlier arrows as in QGIS, which draws each arrow completely before the next one. Arrow lines keep all their vertices: before, nearly straight vertices were simplified away and arrows merged.

### Inner shadow and inner glow on lines
Lines with an inner shadow or inner glow effect (e.g. "effect emboss" and "effect neon") are now shaded like QGIS. The line is drawn as strips about one pixel wide, coloured from QGIS's own rendering of the effect for the line's direction on screen. Line ends and sharp turns are shaded too.

### Smoother glows and shadows on lines
A line's outer glow or drop shadow showed hair-like spikes and dark blocks where the line has many short segments. They are now drawn from a copy of the line simplified to the effect's size at each zoom, and they look smooth, as in QGIS.

### Line offsets at sharp corners
Lines offset in millimetres or pixels (e.g. "topo steps") no longer loop or spike at sharp corners and short segments. At the zooms where the browser's own offset would break, the offset line is the one QGIS draws (its offset curve), exported per eighth of a zoom. Above those zooms the browser offsets the line itself.

### Fixes
- A simple fill's offset in millimetres or pixels now shifts the fill on screen; before, it was ignored.

### Known differences
- QGIS clips a line to the view before building its arrows, so arrows near the edge of the QGIS view change as you pan. The web map always builds them from the whole line.
- Inner effects are computed for straight lines. Where lines overlap or nearly touch, QGIS shades them as one shape and the web map shades each line.
- Screen-size marker intervals (e.g. the ticks of "topo steps") keep their spacing within ±4.5 %, so along long lines their positions drift from QGIS's.

Layers with inner effects, screen-size arrows or screen-size offsets at sharp corners export one dataset per zoom, or per eighth of a zoom, so their export takes longer.

| Run | Result |
|---|---|
| `pytest tests/integration/test_line_shapes.py` | 6 passed (new: 25 random arrows of every kind match QGIS; per-arrow shadow order; arrows keep nearly collinear vertices; inner-shadow strip colours and their export; offset curves per zoom band match QGIS) |
| `pytest tests/integration/test_materialize.py` | 91 passed (map-unit arrows now within 1 % of QGIS's pixels, was 6 %) |
| `pytest tests/integration/test_gradient_fills.py` | 11 passed (glow and shadow from per-zoom simplified copies) |
| `pytest tests/integration/test_more_symbology.py tests/integration/test_end_to_end.py tests/integration/test_units_and_properties.py tests/integration/test_flattener.py` | 61 passed |
| `pytest tests/browser/test_browser_parity.py -k "offsets or dash or thin"` | 14 passed |
| Gallery (`tools/gallery`, hard shapes, colour mismatch at zooms 14.6 / 16.25 / 17.8) | effect emboss 24 / 11 / 5 % (was 59 / 68 / 76 %), effect neon 1.6 / 0.1 / 1.0 % (spikes gone), pointing arrow 3.0 / 1.3 % (was 38 / 35 %; at 17.8 QGIS clips the line to its view, 2.2 % with clipping off), topo steps 15 / 16 / 15 % (was 24 / 20 / 16 %; offset lines identical, tick phase remains) |
