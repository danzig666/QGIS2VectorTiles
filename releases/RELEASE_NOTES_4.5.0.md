**QGIS2VectorTiles 4.5.0 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Faster re-exports
- **Unchanged layers are reused** from earlier exports in the same output folder: their datasets, vector tiles and the parcel report. Editing one layer redoes only that layer. On a 40-layer zoning plan, a re-export went from about 170 s to 15 s; a colour and a label change took 22 s. The tiles are the same as from a full export. Database and web layers are always exported.
- The first export is also faster: layers are tiled in parallel.
- *Output → Reuse unchanged layers* (on by default) and *Clear cache…*. The cache stays on your computer and is pruned automatically.

### Faster uploads
- A new release copies the files that did not change from the current release **inside the bucket** instead of uploading them again.

### Cloudflare R2 made clear
- **Destination → Step-by-step: set up Cloudflare R2…** explains where each value comes from in the Cloudflare dashboard, with links.
- Every field has a tooltip. Pasting the S3 API address or a dashboard page address fills the account id and the bucket.
- The window shows the resulting map address. **Save keys in QGIS…** stores the API token's keys encrypted with one click.
