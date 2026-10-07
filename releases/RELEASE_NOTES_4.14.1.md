**QWebMap 4.14.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Pattern fills aligned to the viewport
In QGIS, line pattern, point pattern and SVG fills have the setting **Align pattern to: Feature / Viewport** (raster image fills: **Coordinate mode: Object / Viewport**). With *Viewport*, the pattern starts at the corner of the map view and stays put while you pan. The web map now does the same, so such patterns line up with QGIS exactly, offset included.

With *Feature* (the QGIS default), QGIS starts the pattern at each feature's corner; the web map keeps those patterns fixed to the map, so they can still be shifted by part of their spacing (same size and look).

| Run | Result |
|---|---|
| `pytest tests/browser/test_browser_parity.py -k viewport` | 2 passed (new: viewport-aligned hatch and point pattern match QGIS pixel for pixel; both fail when feature-aligned) |
| `pytest tests/browser/test_browser_parity.py -k "pattern or hatch"` | 15 passed |
| Pattern, hatch and texture tests (`test_materialize.py`, `test_sprites.py`, `test_patterns_and_assets.py`) | 56 passed |
