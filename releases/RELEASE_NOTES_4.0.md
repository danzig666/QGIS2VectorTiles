**QGIS2VectorTiles 4.0 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It is based on upstream v3.6. This fork focuses on making the web map look like the QGIS map.

Install it in QGIS with *Plugins → Manage and Install Plugins → Install from ZIP* and choose `QGIS2VectorTilesFork-4.0.zip`. It installs as **QGIS2VectorTiles (fork)**, in its own `QGIS2VectorTilesFork` folder, with its own Processing provider. It sits next to the official QGIS2VectorTiles plugin and does not replace it.

### Changes since 3.6

**Styling accuracy**
- Every symbol and label style in a real style library was compared symbol by symbol with QGIS renders, and the differences were fixed.
- Marker lines are placed where QGIS draws them:
  - interval, vertex, first/last, inner, centre and segment-centre markers;
  - offset along the line;
  - corner angles averaged the way QGIS averages them;
  - ring filters;
  - offsets of polygon outlines.
- Marker lines spaced in mm are placed at every zoom, and no markers go missing at tile edges or ring starts.
- Point pattern, line pattern (hatch), SVG, raster image and random marker fills keep the QGIS spacing, density and clipping.
- Pattern textures keep the QGIS size between zoom levels. Dense patterns have no seams.
- Large map-unit patterns and hatches are exported as real geometry, cut at the polygon boundary.
- Map-unit dashes are exported as their dashes. Qt pen styles (dash, dot, dash-dot…) and square caps match QGIS.
- Lines thinner than a pixel get the same amount of ink as in QGIS.
- Map-unit markers stay sharp at every zoom, and font markers sit on the QGIS baseline.
- Map-unit font markers are exported as glyph outlines.
- Nested geometry generators, centroid fills and polygon outline offsets work as in QGIS.
- Map-unit line labels and label background frames are drawn at every zoom. Pinned labels and callouts are supported.
- Draw order, `orderBy` and circle markers match QGIS.

**Speed and robustness**
- Large and detailed layers export much faster. Big polygons are cut into pieces for patterns, and random fills use QGIS's native random points.
- Very dense patterns fall back to textures instead of producing millions of features.
- The sprite sheet stays within browser GPU texture limits.
- Float noise in stored offsets (for example 5.55e-17) no longer drops a symbol layer.

**Reporting**
- Every export writes `fidelity_report.json` / `.html`. It lists each symbol part that is approximated or not supported, with a stable `Q2VT_*` code and a suggested fix.
- A rule that fails to export is reported in the fidelity report, not only in the processing log.
- Strict mode stops the export on fidelity errors.

**Publishing**
- There is an optional static web package with relative URLs, which runs from any folder of any web server.

**Tooling (not in the zip)**
- A QGIS-vs-browser comparison gallery for style databases and QML files (`tools/gallery`), with a `--hard` mode: demanding lines and polygons that cross tile edges, rendered at several zooms.
- 256 automated tests: unit, PyQGIS integration and browser comparison tests.
