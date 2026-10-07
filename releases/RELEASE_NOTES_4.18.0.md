**QWebMap 4.18.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### WebP is the default format for raster layers
Raster layers you add to a publication are now published as **WebP** images: much smaller than PNG and still transparent. Layers you already set up keep the format they were saved with; you can change it per layer in the Publish window (**Image format**). If your QGIS cannot write WebP images, the layer is published as PNG and the export tells you so (before, the export stopped with an error).

### Publish the visible layers
The Publish window's Map tab has a new **Publish the visible layers** button. It publishes exactly the layers visible in the QGIS Layers panel (they are also visible when the web map opens) and stops publishing the hidden ones. A layer in a switched-off group counts as hidden. The map theme button **Publish its layers** still only adds layers.

| Run | Result |
|---|---|
| `pytest tests/integration/test_publish_dialog_layers.py tests/integration/test_publishing_raster.py tests/unit/test_publishing_profile.py` | 43 passed (new: publish the visible layers, WebP default; PNG fallback without a WebP writer) |
| `pytest tests/integration/test_publishing_pipeline.py tests/integration/test_publishing_basemap_themes.py tests/integration/test_export_cache.py` | 9 passed, 1 failed: an export-cache test (`test_reused_dataset_under_a_new_zoom_range_keeps_its_features`) reuses 18 of 24 datasets; it fails the same way in 4.16.0 and 4.17.0 and is being looked at separately |
