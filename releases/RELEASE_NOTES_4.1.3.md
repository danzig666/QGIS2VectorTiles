**QGIS2VectorTiles 4.1.3 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

Install it in QGIS with *Plugins → Manage and Install Plugins → Install from ZIP* and choose `QGIS2VectorTilesFork-4.1.3.zip`. Run it from *Processing → Toolbox* (Ctrl+Alt+T) → **QGIS2VectorTiles (fork)**.

### Fixes since 4.1.2
- **Export failed on QGIS 3.44 / Windows with `'QgsFillSymbol' object has no attribute 'sizeUnit'`.** When a fill's outline was split into its own line, the plugin kept a Python reference to the deleted fill symbol. Python then returned that stale object for the next symbol QGIS created at the same memory address, such as a point pattern's marker. Replaced symbols and symbol layers are now released before QGIS deletes them. The same mistake could also make QGIS close without a message.
- **Line labels no longer disappear from lines that cross themselves or have several parts.** Labels that are not drawn on every part went through a polygon-only "keep the biggest part" step, which dropped these lines. They are now labelled once, on the longest part, as in QGIS.
- Geometries inside the export extent are no longer cut by the extent clip. A self-crossing line stays one line, so its label isn't placed on a fragment, and polygon rings keep their start vertex.
- Marker lines offset into a polygon by more than the polygon's size (for example *Crayon* on small polygons) no longer fail the whole layer. QGIS draws no markers there, and neither does the export.
