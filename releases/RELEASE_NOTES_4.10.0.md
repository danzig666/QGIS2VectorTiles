**QWebMap 4.10.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

QWebMap was tested against the symbols that ship with QGIS (its built-in style library). The ones that did not yet look right on the web are fixed in this release, and the plugin has a new icon.

### Gradient and shapeburst fills
Gradient fills used to be drawn as a flat colour on the web. Shapeburst fills were left out (and reported). Both now look like QGIS:
- **Gradient fills:** linear, radial and conical; two colours or a colour ramp; pad, reflect and repeat.
- **Shapeburst fills:** whole shape or a set distance; rings can be ignored.

How it works: QWebMap computes the polygons of fine colour bands for every feature when it exports, with about 4 colour levels between neighbouring bands and up to 64 bands. Gradients are built from the QGIS reference points in the feature's bounding box, exactly as QGIS (Qt) stretches them with the box. All bands of a symbol are one dataset and one style layer, and neighbouring bands overlap under each other so no hairline gaps show. The 10 gradient symbols of the QGIS library differ from QGIS by about 1 colour level on average in the browser.

Known limits (listed in the fidelity report):
- Viewport-relative gradients are drawn relative to each feature.
- Shapeburst blur is not applied.
- A shapeburst distance in screen units is fixed at the middle of the visible zooms.

### Glow and shadow on lines
An **outer glow** or **drop shadow** effect on a simple line is now drawn on the web as blurred line layers below or above the line (the built-in "neon" symbol). Other effects, such as inner shadow, blur and colorize, are still reported as not applied. Effects on markers were reported as ignored even though they are drawn into the marker images; that false warning is gone.

### Arrow colour
An arrow line takes the colour of the arrow's top visible fill. Before, it could take the colour of a hidden or offset shadow layer.

### New icon
QWebMap has its own icon: a QGIS-green "Q" with a web map inside.

| Run | Result |
|---|---|
| `pytest tests/integration/test_gradient_fills.py` (new) | 11 passed in 7 s: every gradient type and spread, a colour ramp and shapeburst fills are rendered by QGIS before and after the export and compared pixel by pixel (mean difference below 6 levels); glow and shadow layers; arrow colour |
| Unit tests, `test_materialize.py` and `test_plugin_package.py` | all passed |
| Browser gallery of the QGIS built-in symbols (QGIS vs MapLibre) | all 10 gradient symbols match (shape score 0.0, colour difference ~1 level); neon glow matches |
