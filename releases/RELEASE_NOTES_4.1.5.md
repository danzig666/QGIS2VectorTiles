**QGIS2VectorTiles 4.1.5 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

Install it in QGIS with *Plugins → Manage and Install Plugins → Install from ZIP* and choose `QGIS2VectorTilesFork-4.1.5.zip`. Run it from *Processing → Toolbox* (Ctrl+Alt+T) → **QGIS2VectorTiles (fork)**.

### Fixes since 4.1.4
- **QGIS still closed on larger exports (heap corruption, `0xc0000374`, QGIS 3.44 / Windows).** The export ran several QGIS Processing algorithms at the same time in Python threads, and QGIS is not safe to use that way. The export now runs every algorithm on QGIS's main thread, one after another, and QGIS keeps redrawing between steps. On a real 40-layer project at zoom 0–17 this takes 3.4 minutes instead of 3.0.
- New option **Parallel export**, off by default, for the old parallel behaviour. It is faster on some projects but can crash QGIS. *CPU Usage Limit* only applies to parallel export.
