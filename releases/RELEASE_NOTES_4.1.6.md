**QGIS2VectorTiles 4.1.6 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

Install it in QGIS with *Plugins → Manage and Install Plugins → Install from ZIP* and choose `QGIS2VectorTilesFork-4.1.6.zip`. Run it from *Processing → Toolbox* (Ctrl+Alt+T) → **QGIS2VectorTiles (fork)**.

### Changes since 4.1.5
- **MapLibre GL JS updated from 5.11.0 to 6.11.2** in the web viewer and the static web package.
  - MapLibre 6 is an ES module. The viewer now loads `maplibre-gl.mjs`, which loads `maplibre-gl-shared.mjs` and its worker `maplibre-gl-worker.mjs` from the same folder.
  - The plugin's local tile server already serves `.mjs` files as JavaScript. For the static web package, your web server must send `.mjs` files as `text/javascript`. Current web servers do this by default.
- Rendering compared with QGIS is unchanged. Across the 214 styles of the style gallery, the score is the same as with 5.11 (158 identical to QGIS), and the browser images differ only by anti-aliasing.
