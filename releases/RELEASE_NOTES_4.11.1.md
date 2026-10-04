**QWebMap 4.11.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Free (angled) polygon labels turn like in QGIS
- **Labels left horizontal:** polygon labels with *Free (angled)* placement and *Centroid: whole polygon* were exported as fixed points and drawn horizontally on the web. They are now placed by the web viewer like other polygon labels.
- **The QGIS angle rule:** the viewer now follows the rule QGIS's labeling engine uses for Free placement. It takes the oriented bounding box of the polygon:
  - **horizontal** when a label twice the size, centred on that box, still fits inside the polygon;
  - otherwise **along the box side nearest horizontal**, when both sides are longer than 1.5 label widths;
  - otherwise **along the long side**.

  Before, the viewer turned a label only when it did not fit horizontally at its spot, and then along the local direction of the polygon edges. In a tilted plot the label now follows the plot like in QGIS.
- **Label sizes:** the viewer used a generic text width (0.6 em per character) to decide whether a label fits. Each label layer now carries its font's measured average character width, for example 0.45 em for italic Liberation Sans, so narrow fonts are no longer treated as too wide.

On a real parcel layer (Free placement, *whole polygon*, labels in map units) the web map now turns labels at the same angles as QGIS. The viewer still leaves out labels whose polygon is mostly off the screen; QGIS sometimes draws those at the screen edge.

| Run | Result |
|---|---|
| `pytest tests/browser/test_visible_labels.py` | 32 passed (new: the Free angle for 3 box shapes × 19 angles matches what QGIS 3.34 drew) |
| `pytest tests/integration/test_end_to_end.py` | 21 passed (new case: Free + whole polygon is placed by the viewer; fails without the fix) |
| All browser tests | 90 passed (a stale check of the stable map address, unchanged since 4.7.2, was updated) |
| Unit, label font and publishing pipeline tests | passed |
