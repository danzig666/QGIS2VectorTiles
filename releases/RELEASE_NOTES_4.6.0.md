**QGIS2VectorTiles 4.6.0 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Free (angled) label placement
Polygon labels with QGIS's **Free (angled)** placement now behave as in QGIS. A label that fits inside its polygon horizontally stays horizontal. One that doesn't is turned along the polygon around it, so a street name follows its street and narrow parcels get turned numbers. Before, all of them were horizontal. The web map works the angle out from the part of the polygon on screen, so bends and partly visible streets are handled. A rotation you set in QGIS still wins.

### Visible scales per layer (Publish window → Map tab)
- A new **Scales** column in the layer list. Double-click a cell, or select several rows (or a group) → **Selected layers → Visible scales…**, to hide layers in the web map when zoomed out (or in) beyond a scale, e.g. buildings and contours hidden beyond 1:10 000.
- This only affects the web map, on top of the layer's own QGIS scale range. The project is not changed. A layer's own QGIS range is shown as "(QGIS)", with the range in the tooltip.
- Where a layer is hidden it is **not tiled either**. The web map stays fast when zoomed out, and the export and upload get smaller.

### Views bar
The views bar ("Views" / "Nézetek") is hidden when the map offers only one view, since there's nothing to choose.

Re-export (or re-publish) the map to get these.

| Run | Result |
|---|---|
| `pytest tests/browser/test_visible_labels.py` | 22 passed (new: Free placement turns labels along narrow polygons / keeps them horizontal where they fit; helpers) |
| `pytest tests/integration/test_publish_scale_limits.py` | 3 passed (new: range combination; column set for many layers through a group and saved; export: style zooms and no tiles beyond the limit, project unchanged) |
| Publish window and profile suites | 48 passed |
| browser, web builder, end to end, flattener, publishing pipeline, export cache suites | 116 passed |
