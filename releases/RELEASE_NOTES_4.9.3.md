**QWebMap 4.9.3**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Hillshade in multiply mode over a DEM, as in QGIS
A common terrain map is a DEM coloured with a ramp, and a grey hillshade above it with the layer blend mode **Multiply**. The web map drew the hillshade opaque, so the elevation colours were lost and the map turned grey. Browsers cannot blend map layers, so QWebMap now converts the hillshade when it exports:
- **Multiply** with a grey layer is the same as black with transparency (1 − brightness).
- **Screen** with a grey layer is the same as white with transparency (brightness).

Both conversions are exact, whatever is drawn below. More cases:
- **A colour layer in multiply mode with nothing below it** on a white map (often the DEM itself) is drawn normally, which gives the same result.
- **Colour layers in multiply or screen mode above other layers** are approximated by their brightness, and the export says so.
- **Other blend modes, and blend modes on vector layers,** have no web equivalent. They are drawn normally and the export log now warns about them instead of dropping them silently.
- **JPEG raster layers** have no transparency, so a multiply/screen JPEG layer is drawn normally with a warning to choose PNG or WebP.

### Basemap glyphs with older Open Sans installed
With the classic Open Sans release installed (where "Open Sans Semibold" is a family of its own), exporting a bundled OpenStreetMap basemap failed with "Glyphs for 'Open Sans Semibold' could not be generated". It now uses the font name the glyph generator actually writes.

| Run | Result |
|---|---|
| `pytest tests/integration/test_publishing_raster.py` | 5 passed (new: multiply/screen conversion is exact over any colour; a multiply hillshade over a DEM becomes black shading, the DEM keeps its colours, a vector blend mode is reported; both new tests fail without the fix) |
| `pytest tests/integration/test_basemap_glyph_fonts.py` (new) | 1 passed (fails without the fix when Open Sans with a separate "Semibold" family is installed) |
| Basemap/raster viewer, pipeline, export, label-always, web builder and basemap suites | 44 passed |
| A real terrain project (DEM + multiply hillshade + CanVec vectors, NAD83 / UTM 17N), compared with QGIS | hypsometric colours and relief match; the topographic version (contours, index contour labels, elevation points) matched before and still does |
