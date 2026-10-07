**QWebMap 4.16.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Pattern fills start exactly where QGIS starts them
Pattern fills set to **Align pattern to: Feature** (the QGIS default) used to be anchored to the map, so their lines, dots or images could sit shifted by part of their spacing compared with QGIS. They now start exactly where QGIS starts them, to the pixel:

- **Line, point and SVG patterns** start at the bottom-left corner of each feature's bounding box.
- **Raster image fills** ("Coordinate mode: Object") start at the top-left corner of each part. As in QGIS, a part that reaches far outside the view starts at the view's edge (plus 10 %).

Patterns set to **Viewport** already started at the corner of the map view (4.14.1).

Each exported polygon carries its pattern anchor (two numbers), and the web map's renderer draws the pattern from there. Exports and the map are not noticeably slower; tiles of pattern-filled layers grow slightly.

### Known differences
- On a rotated or tilted map the patterns keep their anchors, but without QGIS's whole-pixel rounding and view clipping.
- Rotated SVG fills whose tiles do not repeat seamlessly at their angle are adjusted slightly (reported in the fidelity report), so far from the feature's corner the pattern drifts from QGIS's.

| Run | Result |
|---|---|
| `pytest tests/browser/test_browser_parity.py -k "feature_aligned or beyond_the_view"` | 9 passed (new: vertical, horizontal and diagonal hatches, point, SVG and raster patterns on three features at fractional pixel offsets, one of two parts, and on a feature reaching far beyond the view; pixel mismatch 0–0.9 %, was 36–72 %) |
| `pytest tests/browser/test_browser_parity.py -k "pattern or hatch or viewport or feature or offsets or dash"` | 28 passed |
| `pytest tests/integration/test_end_to_end.py tests/integration/test_sprites.py tests/integration/test_line_shapes.py tests/integration/test_more_symbology.py tests/unit/test_patterns_and_assets.py` | 67 passed |
| `pytest tests/integration/test_materialize.py -k "pattern or hatch or texture or svg or raster"` | 36 passed |
| Gallery: the 24 pattern styles (QGIS library and your library), colour mismatch at zooms 14.6 / 16.25 / 17.8 | 19 hatch and dot styles 0–4 % (were up to 97 %), Bricks and Organic Blocks 0 % (were 11–41 %), Cross-Stitch 12–16 % (was 47–59 %; the rest is the stitch shading); Dormido Rough about 40 % (its tiles are turned by 30° and adjusted by 6 % to repeat seamlessly, so the pattern drifts) and Fantasia about 50 % (random marker rotation, colour and size) are unchanged |
