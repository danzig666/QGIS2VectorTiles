**QWebMap 4.30.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Which layers slow the map down when zoomed out
*Map* tab → **Zoomed-out load…**: QWebMap estimates, from a sample of each published layer's features in the export extent, how heavy the layer is when the web map is zoomed out. Zoomed out, one tile holds a whole area: every feature in it is loaded and drawn, even when it is smaller than a pixel, and every label is laid out though few of them fit.

For each layer the window shows its part of the heaviest tile (features, size) and suggests:

- a zoom **from which its features are drawn**: where the typical feature is at least a pixel or two across, for layers that are heavy below it;
- a zoom **from which only its labels are shown**, the features still drawn below it: where at least half of the labels can be placed, for layers whose labels mostly cannot be placed where they start now.

Each suggestion has a checkbox and a zoom you can change (its scale is shown next to it). The summary gives the heaviest tile of all layers together, now and with the checked limits. **Apply the checked limits** puts them into the *Scales* column. Only the web map changes, not the QGIS project. Where a layer or its labels are hidden, they are not tiled either, so the export gets smaller and faster too.

### Labels-only scale limit
A layer's labels can be hidden when zoomed out beyond a scale while its features are still drawn: *Selected layers → Visible scales…* → **Hide only the labels when zoomed out beyond**. The *Scales* column shows it as "labels 1:10 000 –".

| Run | Result |
|---|---|
| Synthetic city project (85,320 parcels, 47,520 buildings, 38,880 house numbers, sub-parcel names and lines) | analysed in about 10 s; heaviest tile ~5.0 MB → ~1 MB with the suggestions (parcels from z11, buildings from z15, house number and sub-parcel labels from z13) |
| Tests | 84 passed (profile, flattener, plugin package, Publish window, publishing pipeline, scale limits) |
