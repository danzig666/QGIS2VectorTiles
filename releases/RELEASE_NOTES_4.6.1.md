**QGIS2VectorTiles 4.6.1 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### No more blinking labels while panning
- Labels that QGIS may overlap "if required" were drawn by MapLibre plus a copy layer that showed the label where MapLibre hid it. The copy was switched on and off by a check every 150 ms, so while the map moved, labels kept vanishing for a moment.
- Labels placed by the web map itself (zone codes, parcel numbers, street names, contour heights) are now drawn exactly where they are put. The web map keeps them clear of each other where it can; where there is no free spot, a label overlaps rather than disappearing. Other "if required" labels use MapLibre's built-in cooperative overlap, with no copy layer.
- Measured on a 40-layer zoning plan in a 2-second pan: 0 labels blinking, before 3–6.

### Turned (Free) labels stay inside their parcel
- A label turned along its polygon is placed **wholly inside** the parcel, as QGIS does (it drops label positions that stick out). Among the spots where it fits, the best-centred one wins, so a street name sits in the middle of the street, not along one edge. If it fits nowhere, it is not shown.
- Its overlap with other labels is checked with the turned rectangle itself, not its much larger bounding box.

Re-export (or re-publish) the map to get the new viewer.

| Run | Result |
|---|---|
| `pytest tests/browser/test_visible_labels.py` | 23 passed (new: turned labels inside and centred, none when the street is too narrow or short, clear of other labels along the street; cooperative overlap instead of copy layers) |
| browser and web builder suites | 82 passed |
