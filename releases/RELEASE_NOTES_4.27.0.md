**QWebMap 4.27.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Faster export
Measured on a real 88-layer zoning plan: the slow part was not cutting the vector tiles (17 s) but preparing the data for them. The tiles step is where the plugin works out every symbol rule at every zoom level, for example the positions of marker-line symbols.

That preparation was 3573 datasets made from 27,701 small QGIS Processing steps. Each step wrote a temporary file and read it back, and also opened its input once more just to check its parameters.

- **Steps in memory:** inside a rule, the steps now pass their results to each other in memory, and only the finished dataset is written to disk. A step costs about 1 ms instead of 15–75 ms.
- **Shared steps:** a step already done in this export is not done again. For example, a rule's filter or a polygon outline is prepared once and reused for every zoom level and every symbol layer, instead of once for each.
- **Safe for large layers:** steps over very large layers (more than 50,000 features) still use files, so memory use stays bounded. If a rule fails in memory, the plugin automatically redoes it the old way, with files.

| Project | Before | Now |
|---|---|---|
| 88 layers, zoom 0–16, no cache | 21.5 min | 6.9 min |
| 11 layers (the same plan's published set) | 15.5 s | 8.7 s |

The tiles are the same as before: every tile was decoded and compared feature by feature.
- Labels are compared as sets of features, because their order inside a tile already varies from one export to the next.
- Every other layer matches in drawing order as well.

On this plan the rest of the time is mostly the real work of placing screen-unit marker symbols (letters along the landscape-protection boundary) at 17 zoom levels.

For troubleshooting, setting the environment variable `Q2VT_FILE_CHAINS=1` brings back the former file-based steps.

| Run | Result |
|---|---|
| Full test suite (`pytest`: unit, PyQGIS, browser) | 669 passed, 5 skipped, in 14 min (17.7 min before: the tests' own exports are faster too) |
| New `tests/integration/test_memory_chains.py` | 2 passed (memory and file steps give the same tiles and steps are shared; a failing memory step is redone with files) |
| 88-layer plan: every tile decoded and compared with 4.26.0 | identical (label layers as sets of features) |
