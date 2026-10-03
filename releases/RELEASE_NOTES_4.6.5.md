**QGIS2VectorTiles 4.6.5 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Printing at a map scale
The web map now prints at a map scale, and the sheet states it under the title (Hungarian: **M 1:500**, English: **Scale 1:500**).
- **Choosing the scale:** Tools tab → Print → **Scale**.
  - *As on screen (rounded)* is the default. It keeps the current view and rounds its scale to the nearest standard scale.
  - Or pick a fixed scale: 1:250, 1:500, 1:1000, 1:1500, 1:2000, 1:2500, 1:4000, 1:5000, 1:10 000 … 1:500 000.
  - The choice is remembered in the browser. It also applies to Ctrl+P and to the parcel report's Print.
- **What is printed:** the map keeps its centre and is drawn at exactly that scale on the 186 mm wide map area. At 1:500 that is 93 m of ground.
- **Printer setting:** the scale is exact when the browser prints at 100 % (its default). "Fit to page" or a custom percentage changes it.
- **Zoom limit:** if the map cannot zoom in far enough for the chosen scale, the sheet states the scale it actually printed at.

Symbols and labels keep their screen size in pixels, so at a large scale (1:250, 1:500) they look relatively larger on paper than in QGIS.

| Run | Result |
|---|---|
| `pytest tests/browser/test_web_viewer_parcel.py` | 3 passed (the print test now checks that the screen's scale is rounded to a standard one, and that 1:1000 is printed and measured at 1:1000 within 1 %) |
| `pytest tests/browser/test_web_viewer_features.py tests/browser/test_static_package.py tests/unit/test_publishing_web_builder.py` + the above | 24 passed |
| Test plan printed at 1:500 (Chromium) | 703 px = 186 mm of map covers 92.9 m → 1:500 (within 0.2 %); "M 1:500" on the sheet; 1 page |
