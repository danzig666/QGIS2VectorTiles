**QGIS2VectorTiles 4.1.2 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

Install it in QGIS with *Plugins → Manage and Install Plugins → Install from ZIP* and choose `QGIS2VectorTilesFork-4.1.2.zip`. Run it from *Processing → Toolbox* (Ctrl+Alt+T) → **QGIS2VectorTiles (fork)**.

### Fixes since 4.1.1
- **QGIS could close without a message during an export.** The export ran in a background thread and added the result layer to the project from there, which QGIS does not allow. It now runs on QGIS's main thread. The heavy work still runs in parallel worker threads, and QGIS keeps redrawing and responds to *Cancel* while it waits.
- The export no longer empties the whole Processing temp folder. It removes only its own old working folders, so temporary layers from other Processing tools that are open in the project are kept.
- Every export writes `export_log.txt` to its output folder. It lists each step and each processing step of the worker threads, written to disk as they happen. If QGIS crashes, the file ends with a Python traceback of every thread. Please send this file with a crash report.
