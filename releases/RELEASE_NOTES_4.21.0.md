**QWebMap 4.21.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Raster detail: one choice, in metres
The raster settings had three controls for one question — how sharp the image is on the web map: *Maximum zoom*, *Match the image's resolution* and *Sharp on high-resolution screens*. They are now one choice, **Sharpest detail**, given as the size of one image pixel on the ground:

- **Like the image** (recommended, the default for new raster layers): as sharp as the image itself, e.g. *0.40 m per pixel*.
- **Like the map**: the publication's maximum tile zoom.
- **A pixel size**: e.g. *0.80 m*, *1.6 m*, *3.2 m* — coarser means a smaller export. One step finer than the image is offered too, marked "no more detail".

Below it, the estimate compares your choice with the image (e.g. *"4× coarser than the image"*) and gives the number of tiles. *Minimum zoom* is now called *Shown from zoom*.

Layers set up before keep their sharpest detail: a layer that used *Sharp on high-resolution screens* gets the same detail one zoom further (the option made each image twice as wide, which is the same as one more zoom).

| Run | Result |
|---|---|
| `pytest tests/integration/test_publish_dialog_layers.py tests/integration/test_publishing_raster.py tests/unit/test_publishing_profile.py tests/integration/test_publish_dialog.py tests/integration/test_publishing_pipeline.py` | 58 passed (new: the choices in metres, the default, the coarser / finer texts, saving, older settings carried over) |
