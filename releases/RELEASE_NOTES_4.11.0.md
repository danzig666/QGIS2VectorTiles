**QWebMap 4.11.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

This release was tested with symbols from shared QGIS style libraries: Barb Wire, Bricks, Cross-Stitch, Dormido Rough, Fantasia, Organic Blocks, a rainbow colour ramp and Italian-region legend patch shapes.

### Map themes add up
In the Publish Web Map window, *Publish its layers* for a map theme now **only adds** the theme's layers and never unpublishes others. Pick a theme, press the button, pick the next theme, press again: the layers of all of them are published.

### Symbols using `@symbol_color`
Expressions with `@symbol_color` (for example a marker in a marker line that follows the line's colour) made the whole layer fail to export ("Cannot convert '' to int"), so the layer was missing from the web map. QGIS sets this variable only while it draws a symbol. QWebMap now gives it the symbol's colour, as QGIS does.

### Random values per marker
A data-defined angle, size or colour with `rand()` / `randf()` on the markers of a point pattern or random marker fill gives every marker its own value in QGIS (the "Fantasia" symbol: rotated diamonds in random colours and sizes). The export drew every marker the same, with one value per polygon. The pattern texture now holds many markers, each drawn with its own random values.

### SVG fills: no seams, rotation
- **Seams:** SVG fills (bricks, stone blocks…) had thin white lines along every tile edge. The SVG now fills its whole tile, as QGIS draws it.
- **Rotation:** a rotated SVG fill broke the texture at every tile edge. QGIS rotates the whole texture, and so does QWebMap now. The texture still repeats without seams, because the tiles are turned or scaled by a few per cent where needed. The export says when this happens.

### Legend patch shapes
The web legend draws each symbol in the legend patch shape set on the layer or legend item, for example a region outline instead of the default rectangle.

### Still different (listed in the fidelity report)
- **Whole markers at polygon edges:** a point pattern sized in millimetres that keeps only whole markers (as in "Cross-Stitch") is cut at the polygon edge on the web. Patterns spaced in map units keep whole markers.
- **Pattern scale between zoom levels:** textures and millimetre marker spacing are exact at the middle of each zoom level and within ±41 % in between. This is a MapLibre limit: patterns are drawn at whole zoom levels.

| Run | Result |
|---|---|
| `pytest tests/integration/test_style_library_symbols.py` (new) | 5 passed: `@symbol_color` in a sub-symbol, random per-marker values, seamless SVG cells, seamless rotated SVG fills, legend patch shapes (4 fail without the fixes; the fifth is new) |
| `pytest tests/integration/test_publish_dialog_layers.py` | 3 passed (two themes published one after the other: all layers stay published) |
| Unit, sprite, materialization, gradient and package tests | 355 passed |
| The style library above, QGIS vs the browser | Barb Wire, Bricks, Organic Blocks, Dormido Rough, Fantasia and the rainbow gradients match |
