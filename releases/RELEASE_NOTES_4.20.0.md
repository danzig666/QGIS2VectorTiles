**QWebMap 4.20.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Raster resolution, shown and matched
How sharp a raster layer is on the web map depends only on its **Maximum zoom**: each zoom level halves the size of a pixel on the ground (at 48° N: zoom 16 = 1.6 m, 17 = 0.8 m, 18 = 0.4 m). Beyond the maximum zoom the browser only enlarges the last images. This was hard to see, so the raster settings in the Publish window now say it:

- **The image's own resolution** and **what the maximum zoom publishes**, for example *"Image: 0.40 m per pixel. Published: 1.60 m per pixel at zoom 16 – 4× coarser than the image; zoom 18 would show all of it."* A maximum zoom finer than the image is flagged too (a larger export, no more detail).
- New option **Match the image's resolution**: the maximum zoom becomes the lowest one as sharp as the image (one less with *Sharp on high-resolution screens*, whose tiles have twice the pixels). Online services (WMS, XYZ…) have no resolution of their own; set their maximum zoom by hand.

The resolution is measured on the ground, so it is right in any coordinate system (EOV, Web Mercator, degrees).

| Run | Result |
|---|---|
| `pytest tests/integration/test_publish_dialog_layers.py tests/integration/test_publishing_raster.py tests/unit/test_publishing_profile.py` | 45 passed (new: the resolution text, matching with and without high-resolution tiles, saved with the project; image and tile resolutions) |
| A 0.40 m orthophoto (EOV) | before: zoom 16, 1.60 m per pixel ("4× coarser"); matched: zoom 18, 0.40 m (882 tiles); with high-resolution tiles: zoom 17 (252 tiles) |
