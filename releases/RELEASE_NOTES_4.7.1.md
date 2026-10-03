**QGIS2VectorTiles 4.7.1 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Label every feature
On a narrow strip parcel (e.g. hrsz 1119, 118 × 5 m) the parcel number was left out of the web map: a 4 m tall label does not fit inside a 5 m wide strip, and labels must not hang off their parcel.

New option: Publish window → Interaction tab → pick a layer → **Label every feature (smaller where the label does not fit)**. Every feature of that layer then gets its label in the web map:
- A label that fits is placed as before.
- A label that doesn't fit is drawn smaller: 80 %, 65 %, then 50 % of its size, still inside the polygon and turned along it if needed.
- If even half size doesn't fit, the label goes at the polygon's roomiest point at half size, along the polygon. It is never left out.

**Speed:** for these parcels the spot is computed at export, not live in the browser. Per feature, QGIS adds four small numbers to the tiles: the roomiest point (pole of inaccessibility), its free radius and the polygon's direction (`main_angle`). The browser puts a label that cannot fit at that point right away, with no search. On the test plan, placing every label takes 450 ms at zoom 16, against 390 ms without the option (and 500 ms with live searching). At zoom 18 it is 45 ms against 43 ms.

The option is off by default. In the Hungarian HÉSZ edition the preset switches it on for the parcel layer.

| Run | Result |
|---|---|
| `pytest tests/browser/test_visible_labels.py` | 26 passed (new: a label is shrunk to fit a strip or forced at half size; without the option there is still no label; the precomputed spot is used as it is; label sizes stay a dense zoom curve) |
| `pytest tests/integration/test_publishing_label_always.py` | 1 passed (new: the manifest marks the layer; only its label tiles carry the precomputed spot) |
| export cache, pipeline, Publish window, street search, profile, web builder, validation and viewer suites | 114 passed |
| Test plan, parcels with the option | every strip parcel near 1119 gets its number at zooms 16.5, 18 and 19; framed zone code labels stay correct |
