**QWebMap 4.24.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Internal changes
- Special editions can add small viewer add-ons without changing the generic plugin (used by the Hungarian edition's zone regulations). The generic web map is unchanged.
- The data schemas list the fields added in 4.24.0 (the plugin's behaviour was not affected).

| Run | Result |
|---|---|
| New browser tests (incl. the add-on mechanism) | 10 passed |
| Schema-checking and parcel report suites | 30 passed |
