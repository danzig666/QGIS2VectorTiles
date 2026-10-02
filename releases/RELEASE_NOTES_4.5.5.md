**QGIS2VectorTiles 4.5.5 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Shared boundaries stay on top of each other
- Before tiling, every layer was simplified with a fixed 1 m tolerance, each layer on its own. A boundary shared by two layers — e.g. a zone boundary (övezethatár) running along a parcel edge — lost different vertices in each layer and the two drifted apart by up to about a metre: a few pixels at zoom 18, more when zoomed in further.
- Geometry is now simplified only within a quarter of the tiles' own coordinate step at the max zoom (about 2 cm at zoom 17), so shared boundaries stay exactly on top of each other. Markers placed along lines (e.g. the dots of a dotted zone boundary) now sit on the line too.
- Cost on a 40-layer project: export 159 s instead of 154 s, published files 1.4 % larger.

Re-export (or re-publish) the map to get the exact boundaries.

| Run | Result |
|---|---|
| `pytest tests/integration/test_end_to_end.py -k detail_below` | 1 passed (new: a 0.4 m bend survives at the max zoom; fails with the old 1 m tolerance) |
| exporter / pipeline / cache suites (end to end, units and properties, publishing pipeline, materialize, export cache) | 141 passed |
