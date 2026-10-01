**QGIS2VectorTiles 4.1.4 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

Install it in QGIS with *Plugins → Manage and Install Plugins → Install from ZIP* and choose `QGIS2VectorTilesFork-4.1.4.zip`. Run it from *Processing → Toolbox* (Ctrl+Alt+T) → **QGIS2VectorTiles (fork)**.

### Fixes since 4.1.3
- **QGIS closed without a message on larger exports (heap corruption, `0xc0000374`).** Each parallel worker called `QgsProject.createExpressionContext()`. QGIS rebuilds a cached project scope there without a lock, and the cache is cleared whenever a layer is added or removed. Several threads rebuilding it at once corrupted memory. Higher maximum zooms make more parallel steps, so the crash was more likely. The global and project expression scopes and the transform context are now read once on the main thread, and each worker gets its own copy. Project variables still work in expressions.
- Messages from worker threads, such as invalid-expression warnings, are now written by the main thread. QGIS's Processing log is not thread-safe.
- The *CPU Usage Limit* setting now limits the number of parallel workers. It was ignored before.
- **The web viewer opens on the exported area.** It used to open at the minimum zoom, so with a minimum zoom of 0 it showed the whole earth. The MapLibre viewer fits the export extent (within the exported zooms). The OpenLayers viewer and the static web package start at the zoom that shows the extent.

### More progress messages
- The Processing log shows each layer as it is read and prepared, the number of exported datasets as they finish, and a "still generating tiles" line every 15 seconds. The progress bar moves through the whole export.
