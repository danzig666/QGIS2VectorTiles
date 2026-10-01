**QGIS2VectorTiles 4.3.0 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

Install it in QGIS with *Plugins → Manage and Install Plugins → Install from ZIP* and choose `QGIS2VectorTilesFork-4.3.0.zip`. Open *Web → QGIS2VectorTiles (fork) → Publish Web Map…*.

### Raster layers
- QGIS **raster layers** (orthophotos, scanned plans, rendered elevation models, WMS/XYZ layers) can now be published.
- QGIS draws each one exactly as on the canvas, into its own image tile file (`data/raster-….pmtiles`).
- Each layer has its own settings on the *Interaction* tab: image format (PNG, WebP or JPEG), zoom range, quality, and optional sharp tiles for high-resolution screens. The tab also estimates the number of tiles.
- Empty (transparent) tiles are not stored.
- Raster layers sit at their place in the layer order, under all labels.
- **Vector layers are never turned into images**; they stay vector tiles.

### Vector basemap
- New *Basemap* tab with an optional **OpenStreetMap vector basemap** (Protomaps).
- When you export, the plugin takes only your area from one of these sources:
  - the latest Protomaps daily build (the export needs internet);
  - a `.pmtiles` file or URL you provide.
- That area goes into the release as its own vector tile file. The plugin generates the fonts for its labels from fonts installed on your computer.
- Five styles: light, dark, white, grayscale and black. Visitors switch between them, or turn the basemap off, with a button in the bottom-right corner.
- Visitors never load anything from another site. The OpenStreetMap attribution is shown automatically.

### Map themes, layer settings
- **Map themes**: one click publishes exactly the layers visible in a QGIS map theme, or uses them as the start view. The themes you choose become **views** in the web map: one-click buttons above the map, and one of them can be the start view.
- **Several layers at once**: select rows (Ctrl/Shift + click) and use *Selected layers* or the right-click menu to publish, hide at start, or make them *always shown*. A selected group applies to all its layers.
- **Always shown**: layers and groups can be marked so visitors cannot switch them off. They show a lock in the viewer, and links or views cannot hide them either.
- Group checkboxes show whether all, some or none of their layers are published.

### A new viewer
- **Desktop**: the map fills the window, with floating panels for the title, search and layers.
- **Phones**: a top bar and a bottom sheet you can drag taller or shorter.
- Switches instead of checkboxes, an opacity control per layer, card-style popups and legend, and value lists with counts for filters.
- **Light and dark appearance** (follows the system or a button), your **accent colour**, and a styled search with result icons.
- Locate-me button on secure (https) sites.
- Settings for all of this are saved in the project, like everything else.

### Notes
- **Basemap download not tested live**: the Protomaps daily build service could not be reached from the test environment. Downloading only the needed tiles over HTTP was tested against a local server. A local `.pmtiles` file works offline.
- Map themes that use a different layer style are published with the layer's current style.
- Not yet tested: live Cloudflare R2, Windows, QGIS 3.44 or later, Firefox, Safari.
