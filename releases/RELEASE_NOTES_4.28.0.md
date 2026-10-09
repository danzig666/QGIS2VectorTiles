**QWebMap 4.28.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Much faster export
The same 88-layer zoning plan as in 4.27, from scratch (no cache), on a 4-core computer:

| Export | 4.27 | 4.28 |
|---|---|---|
| 88 layers, zoom 0–16 | 6.9 min | 54 s (1.8 min without the helper processes) |
| the same with *fast marker lines* (new option, see below) | – | 27 s |
| 11 layers (the plan's published set) | 8.7 s | 7.5 s |

The tiles are the same: every tile was decoded and compared with 4.26 and 4.27, feature by feature (labels as sets of features, everything else also in drawing order), and the fidelity report lists the same items.

What changed:
- **Marker positions computed directly.** Marker symbols repeated along a line (for example letters along a boundary) were placed by a QGIS expression that walked the whole line again for every marker. They are now computed with the same arithmetic in one pass — the very same numbers, checked on thousands of random lines.
- **Several QGIS processes at once.** For a big export (from about 150 datasets), the plugin starts helper processes (headless copies of your QGIS, as many as the *CPU limit* allows) that read the file layers and prepare the datasets in parallel. Database, web and virtual layers, and rules that look up other layers of the project, are still done by QGIS itself; if a helper cannot do something, QGIS does it.
- **Tiles in parallel and in the background.** Each layer's tiles are cut by their own `ogr2ogr`, several at a time (a big layer in bands of zoom levels), while the records, legend, rasters and basemap are prepared.
- Smaller savings: the thousands of prepared datasets are read with SQLite instead of opening each as a QGIS layer, the symbol settings are read faster, and identical pattern images are made once.

### Fast marker lines (optional, off by default)
*Output → Fast marker lines*: marker lines whose spacing is set in screen units (points, millimetres, pixels) keep the same spacing on screen at every zoom, so their markers are normally computed for every zoom level. With this option the browser places them along the lines instead: a much faster export, but the markers are not exactly where QGIS draws them (the fidelity report says so). Leave it off for the exact map.

### Network output folder
When the output folder is on a network drive or share, the many work files and the export cache stay on this computer (in the system temp folder); only the finished map is written to the network folder, with a copy of the export log and the fidelity report.

### Export log
The export log names the slowest layers (seconds for their datasets and for their tiles): where to look first when an export is slow.

For troubleshooting: `Q2VT_WORKERS=0` (environment variable) turns the helper processes off, `Q2VT_WORKERS=n` sets their number; `Q2VT_FOREGROUND_TILES=1` makes the tiles before the other steps again; `Q2VT_FILE_CHAINS=1` brings back the file-based steps of 4.26.

| Run | Result |
|---|---|
| Full test suite (`pytest`: unit, PyQGIS, browser) | 688 passed, 5 skipped, in 13.8 min |
| New tests: `test_marker_points.py`, `test_export_workers.py`, `test_export_speed.py` | 19 passed (direct markers equal the QGIS expression on random lines; workers give the tiles and diagnostics of one process, also when a worker stops; SQLite dataset reading; network folder; zoom bands equal one run; fast marker lines) |
| 88-layer plan: every tile decoded and compared with 4.26/4.27 | identical (labels as sets of features); diagnostics identical |
| 11-layer published set, compared with 4.26 | identical |
