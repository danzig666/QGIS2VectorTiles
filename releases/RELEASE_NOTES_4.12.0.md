**QWebMap 4.12.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Raster layers are reused from the export cache
Rendering raster layers (orthophotos, scanned plans, DEMs) was the slowest part of an export, and it ran every time. A raster layer is now reused from the export cache, like unchanged vector layers, as long as all of these are the same:
- the raster file(s) and their side files (`.aux.xml`, overviews, world files);
- the layer's style in QGIS (colours, resampling, brightness, opacity…);
- the extent and zooms;
- the image settings (format, quality, sharp tiles for high-DPI screens).

Changing any of these renders the layer again. Rasters from web services or databases are always rendered.

### Raster tiles on all CPU cores
Raster tiles are now drawn and compressed on several CPU cores at once (the export's CPU share setting applies). It is 2.4× faster on 4 cores. The tiles are exactly the same as before, byte for byte.

### Parcel report prints zoomed on the parcel
*Print* in a parcel report used the screen's zoom. It now zooms on the parcel: the whole parcel fills the printed map, with a small margin showing the edges of the neighbouring parcels. It uses the closest standard scale that still shows the whole parcel; 1:100 and 1:200 were added to the scales. A print scale you choose in *Tools* still wins.

| Run | Result |
|---|---|
| `pytest tests/integration/test_publishing_raster.py` | 8 passed (new: a second export reuses the raster and makes the same archive, while a style or quality change renders it again; 1 and 4 threads make identical archives) |
| Parcel report and viewer print tests | 9 passed (the printed parcel is wholly on the map and fills much of it) |
| Browser, publishing and unit tests | 372 passed |
