**QWebMap 4.9.2**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### "Around point" polygon labels beside their point, as in QGIS
Polygon labels with the QGIS placement **Around point** (Around centroid) were drawn centred on the polygon in the web map. A marker at the centroid, such as an SVG icon on a school, ended up under the label.
- **Now:** the label sits beside its point at the QGIS **label distance**.
- **Positions:** like QGIS, the label tries the positions around the point in turn: above first, then the corners and sides. It takes the first one that fits on the screen and keeps clear of other labels.
- **Distance units:** a distance in map units grows and shrinks with the zoom; millimetres, points and pixels stay a fixed size.
- **Your projects:** a polygon layer labelled *Around point* (even with distance 0) now has its labels beside the centroid instead of on it, which is what QGIS draws.

### Coordinates in the project's own CRS
The Tools tab showed EOV (EPSG:23700) coordinates for every map, even outside Hungary. It now shows the coordinates of the **project's own CRS** next to WGS 84:
- **EOV projects:** EOV, with the same transformation as before.
- **Other projected CRSs:** UTM, national grids and so on, converted in the browser with proj4js (MIT, bundled). The library downloads only for maps that need it. When the CRS uses a datum shift, the value is labelled approximate.
- **WGS 84 projects:** WGS 84 only.

| Run | Result |
|---|---|
| `pytest tests/browser/test_visible_labels.py` | 29 passed (new: beside the point at the distance and clear of a centroid marker; next position when one does not fit or is taken; map-unit distance follows the zoom) |
| `pytest tests/browser/test_coordinates_crs.py` (new) | 3 passed (EOV project: EOV, proj4js not loaded; UTM 34N project: matches QGIS's transformation within 5 cm; WGS 84 project: one row) |
| Viewer features, snapping, parcel report, web builder, validation, pipeline, plugin package and end-to-end suites | 80 passed |
| Demo project, school labels *Around point* 3.2 mm, compared with QGIS | label beside the icon at the QGIS distance |
