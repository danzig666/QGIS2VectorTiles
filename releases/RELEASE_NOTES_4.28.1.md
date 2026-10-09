**QWebMap 4.28.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### What a city's export log showed
An export of a city's cadastre (7 layers: every parcel and building of the city, house numbers) took 11 minutes: 4.5 for the datasets, 6.6 for the tiles, with the progress bar standing at 45% for four minutes. Measured again on a city-size copy of the same layers (85,000 parcels, 47,000 buildings, 39,000 house numbers; zoom 0–16, 4 cores):

| | 4.22 | 4.28.0 | 4.28.1 |
|---|---|---|---|
| Datasets | 4.2 min | 3.5 min | 1.6 min |
| Tiles | 8.4 min | 3.0 min (in the background) | 2.8 min (in the background) |
| Whole publication | 14.5 min | 8.3 min | 6.1 min |

- **Switched-off settings no longer slow the export.** A symbol can keep a data-defined setting that is switched off; QGIS keeps its expression but does not use it. When that expression read the map scale (`@map_scale`), the layer was exported once for every zoom level, every feature each time: the city's parcels 17 times. Now only settings that are switched on count. The map looks the same (the parcels at the highest zoom are identical; below it their fill is simplified by 1/16 of a pixel, as any other fill).
- **The tile progress bar follows ogr2ogr's own progress** instead of an estimate, so it no longer stands still while big layers are cut. "N of M layers left" counts layers (a big layer is cut in several pieces at once).
- **The export log** says when a layer's tiles were made in parallel pieces ("tiles 3336 s in 26 parallel pieces"): their seconds add up to more than the minutes the tiles took.
- **A tile job that stops with an error runs once more** before the export fails (seen once: ogr2ogr stopped on a dataset that it tiled without a problem in every other run).

Tip: a layer drawn at every scale (no *scale-dependent visibility*), such as every parcel of a city, is also in the zoomed-out tiles, where one tile holds all of it (several megabytes). A scale range in QGIS (for example parcels from 1:25,000) makes the export faster and the web map lighter.

| Run | Result |
|---|---|
| Related test suites (export cache, speed, workers, rule flattening, materialization, publishing, scaling, tile progress, marker points) | 200 passed, in 6.6 min (20 test files) |
| New tests | switched-off `@map_scale` setting: one dataset (switched on: one per zoom); tile progress from ogr2ogr; a failed tile job runs once more; log with parallel pieces |
| 88-layer plan, datasets compared with 4.28.0 | identical, except two layers with a switched-off scale setting: 34 per-zoom datasets are 2 now; 5 runs identical to each other |
| City-size copy, tiles compared with 4.28.0 | the same 4640 tiles; every other layer identical; the parcel and building fills identical at the highest zoom, simplified by 1/16 px below |
