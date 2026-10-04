**QWebMap 4.13.2**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Key problems are reported at once
*Layer "…": key not unique* appeared only after the whole tile export, often minutes later. The keys are now checked in the first seconds of the export, before any tile is made. The message names the key field(s) and where to change them (Interaction tab, or the Parcel report tab for the parcel layer). Missing parcel report layers or fields are also reported at once.

### Export cache: shared GeoPackages
When several layers live in one GeoPackage, an edit in any of them (or a style saved into the file) made every layer of that file look changed, so all were exported again. Each layer now follows only its own table. When a layer's feature key (unique id) changes, only that layer is redone, and the export log says "feature key (unique id) changed".

The first export after this update is a full one, because the exporter code changed. Later exports reuse unchanged layers again. The export log lists which layers were redone and why (*Redone (…)* lines).

### Scales column
For layers with no web scale limit, the *Scales* column in the Map tab showed only "(QGIS)". It now shows the layer's QGIS range, e.g. "(QGIS 1:2 000 –)".

| Run | Result |
|---|---|
| Pipeline tests | 5 passed (duplicate keys stop the export before the tile export) |
| Export cache tests | 4 passed (new: two layers in one GeoPackage, each redone only for its own change; fails without the fix) |
| Publish window layer tests | 4 passed (new: the Scales column shows the QGIS range) |
| Pipeline, parcel report, publish dialog, raster and cache unit tests together | 31 passed |
