**QGIS2VectorTiles 4.5.4 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Contour labels: all of them
4.5.3 put once-per-line labels (e.g. contour heights) on the visible part of the line, but MapLibre still hid most of them: its collision boxes use the text size of the next whole zoom level, up to twice the drawn size for labels sized in map units.
- Line labels are now placed clear of the other labels by their drawn size, as QGIS does — including the point labels MapLibre draws itself (building numbers, names) — and are always drawn where placed.
- No clear spot along the visible part of the line: no label (as QGIS), instead of a label MapLibre would hide.

Re-export (or re-publish) the map to get the new viewer.

| Run | Result |
|---|---|
| `pytest tests/browser/test_visible_labels.py` | 20 passed |
| label-related suites (visible labels, web viewer features, transport, parity, web builder, end to end) | 89 passed |
