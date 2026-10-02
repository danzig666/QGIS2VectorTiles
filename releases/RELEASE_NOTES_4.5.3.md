**QGIS2VectorTiles 4.5.3 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Line labels inside the map view
- Line labels drawn once per line (e.g. contour heights with **Parallel** / **Curved** placement and no repeat distance) now appear on the **visible part** of the line, in the middle of the longest stretch on screen and rotated along it, as QGIS places them. Before, they sat at the middle of the whole line, often far off screen, so long contours showed no label at all.
- A label is shown only where it fits along the visible stretch and on the screen; it slides along the line to keep clear of other labels.

Re-export (or re-publish) the map to get the new labels.

| Run | Result |
|---|---|
| `pytest tests/browser/test_visible_labels.py` | 20 passed (5 new for line labels) |
| `pytest tests/integration/test_end_to_end.py -k once_per_line` | 1 passed (new) |
| label-related suites (web viewer features, transport, parity, web builder, export cache) | 77 passed |
| exporter / pipeline suites (end to end, units and properties, publishing pipeline, materialize) | 134 passed |
