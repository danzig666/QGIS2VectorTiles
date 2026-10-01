**QGIS2VectorTiles 4.2.0 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

Install it in QGIS with *Plugins → Manage and Install Plugins → Install from ZIP* and choose `QGIS2VectorTilesFork-4.2.0.zip`. The new window is under *Web → QGIS2VectorTiles (fork) → Publish Web Map…* (also on the Web toolbar). The Processing algorithm is still in *Processing → Toolbox* → **QGIS2VectorTiles (fork)**.

### Publish Web Map (new)
- **A plugin window** with five tabs: *Map*, *Interaction*, *Output*, *Destination* and *Review*. You choose the layers to publish, including hidden ones that start switched off. You also set the extent and zooms, and the popup, search and filter fields. **Every setting is saved in the project**, but credentials are not. Keys stay in the QGIS authentication database.
- **The map stays vector tiles.** The existing compiler writes the same MVT tiles, now packed into one **PMTiles** archive. The browser reads only the parts it needs with HTTP byte ranges, so you need no tile server, database or Docker. Raster layers, WMS, screenshots and whole-map GeoJSON are refused.
- **Immutable releases.** Each export is a new `releases/<id>/` folder. A stable `index.html` follows `current.json`, and the previous map stays usable until the new one is complete and checked.
- **Preview** serves the folder on your own computer (127.0.0.1) and opens the browser.
- **Publish to Cloudflare R2** or other S3-compatible storage, step by step:
  1. Uploads the new release.
  2. Checks it through your public domain: content types, real byte ranges, and a tile read through the archive.
  3. Only then switches the stable link, with a conditional write.

  *History* rolls back to an earlier release, removes old releases (you confirm the list) and cleans up interrupted uploads. Setup steps are in [`docs/publishing/HOSTING.md`](https://github.com/danzig666/QGIS2VectorTiles/blob/main/docs/publishing/HOSTING.md).
- **Privacy.** Only fields you approve become public. Every tile is decoded and checked before anything is written or uploaded. The first publication and any change of the published fields need an explicit review.

### New web viewer
- Layer tree with groups, hidden-but-published layers and opacity sliders.
- Legend with symbols rendered by QGIS, and a label on/off switch.
- Attribute filters, and popups with only the approved fields.
- **Search** across the whole publication, not just the loaded tiles. It ignores accents and capital letters, so *arlo* finds *Arló*.
- Links to features, and permalinks that keep the view, layers, filters and selection.
- Distance and area measuring, with WGS84 and **EOV** coordinates.
- Phone layout, in Hungarian and English. The viewer makes no requests to other sites (no CDN, fonts or tracking).

### Other changes
- The Processing algorithm has a new *Tile archive format* option: MBTiles (default), PMTiles or both.
- Web releases never contain the raster backgrounds (OSM, Blue Marble) that the older viewer package can add.

### Not yet verified
- Publishing to a **live** Cloudflare R2 bucket has not been tested yet. Upload, verification, activation and rollback were tested against a simulated S3 service and a local S3 server (moto). Try it first with a test prefix.
- The plugin was tested on Linux with QGIS 3.34 and Chromium only. It has not been tested on Windows, in QGIS 3.44/4.x, or in Firefox and Safari.
- External vector basemaps and private (password-protected) maps are not part of this release.
