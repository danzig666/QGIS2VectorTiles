**QWebMap 4.28.2**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Faster packaging
"PMTiles: validating..." and "Validating the web release..." took long on a city's map. Both checks stay (a broken archive or a field you did not approve must not be published), but they no longer read the whole city again. On a city-size test map (85,000 parcels, 47,000 buildings, zoom 0–16) packaging took 107 s and now takes 32 s.

- **The archive check** still checks the whole structure: every tile's place and size, the tile count and the zoom levels. Of the sample tiles it decodes, those up to 512 KB are decoded feature by feature as before. The big zoomed-out tiles (one holds the whole city, several MB) are checked by their compression and their layers. 41 s → 8 s.
- **The web release check** no longer decodes the sample tiles of the same archive a second time: it compares the SHA-256 checksum and checks the structure only. The check that no unapproved field is in the tiles uses the fields of every tile, gathered while the archive was written; an archive made elsewhere is still read tile by tile. 50 s → 2.5 s.
- A damaged tile is reported as such ("not MVT") instead of a bare decompression error.

| Run | Result |
|---|---|
| City-size test map, packaging | 107 s → 32 s |
| Publishing test suites (17 files) and review fixes | 192 passed, 5 skipped |
| New tests | big tiles checked by their layers; a checked archive is not decoded again; a damaged big tile is still refused; field check from the gathered fields, an archive made elsewhere read tile by tile |
