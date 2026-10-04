**QWebMap 4.9.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Bold and italic labels on the web
Labels made bold or italic with the **B** / **I** buttons of the QGIS text format (or with a bold font weight) were drawn in the regular face on the web. They now use the bold, italic or bold-italic face of the same font, like in QGIS. Glyphs for that face are generated automatically. A label whose style is chosen from the font's style list (e.g. "Bold") was already right.

### New README and demo
- **Headline feature:** the README now leads with what sets QWebMap apart: your QGIS symbology, converted accurately to vector tiles. It shows a QGIS vs web comparison and a list of what carries over.
- **Demo animation:** a new demo shows the same map in QGIS and on the web, the Publish Web Map window, and the web viewer (layers, popups, search, measuring with snapping, dark mode, phone layout). It uses the open QGIS training data (Swellendam).
- **Plugin description:** the description in the plugin manager says the same.

| Run | Result |
|---|---|
| `pytest tests/integration/test_label_font_style.py` (new) | 6 passed (B button, bold weight, style name "Bold", italic and bold-italic resolve to their faces; 4 of them fail without the fix) |
| Label, glyph, font and text suites (`tests/integration -k "label or glyph or font or text"`) | 37 passed |
| Demo project exported and compared with QGIS at the same view | bold place labels match, fidelity report 0 errors, 0 warnings |
