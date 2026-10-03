**QGIS2VectorTiles 4.7.2 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### The short address works, and stays in the address bar
Published on Cloudflare R2, the map opened at `…/maps/arlo/index.html`, but `…/maps/arlo` gave an error: object storage has no folder index. And once open, the address bar showed the long release address `…/maps/arlo/releases/r-…/index.html`.

- **The short address:** every publish now also uploads two small objects:
  - `maps/arlo/` holds the stable entry page itself;
  - `maps/arlo` redirects to `maps/arlo/`.

  So `https://<your domain>/maps/arlo` opens the current map. No Cloudflare rule or Worker is needed. The Publish window shows this short link after a publish.
- **The address bar:** once the release has loaded, it shows the address the map was opened at, plus the map position, e.g. `…/maps/arlo/#…`. A reload or a shared link always opens the *current* release. The viewer's "this version" link still points to the exact release.
- **If the host refuses:** some S3-compatible servers cannot store a key ending in `/` (e.g. MinIO). There the extra objects are skipped, the publish still succeeds and the link stays `…/index.html`.

The first publish with 4.7.2 creates the two objects; no other change is needed.

| Run | Result |
|---|---|
| `pytest tests/unit/test_publishing_providers.py` | 16 passed (new: the two objects and their content on R2; a host that refuses them keeps the `index.html` link and still publishes) |
| `pytest tests/browser/test_web_viewer_parcel.py` | 4 passed (new: opened at the stable address, the address bar keeps it; relative URLs still load from the release; the stable and the versioned links are right) |
| browser (features, parcel, basemap, static package, smoke), S3 and pipeline suites | 41 passed, 4 skipped (moto server not configured) |
