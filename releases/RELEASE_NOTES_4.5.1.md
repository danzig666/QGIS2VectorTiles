**QGIS2VectorTiles 4.5.1 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fix
- Parcel report: choosing an optional **Zone regulations table** now picks its zone code field by itself (the field named like the zone layer's code field, e.g. `szab_ov`). If no field can be found, the message now explains what to do instead of showing "parcelInfo: regulation table needs both a layer and its zone code field". The table is optional; leave *Table* empty if you do not use one.
