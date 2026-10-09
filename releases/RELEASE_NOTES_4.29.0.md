**QWebMap 4.29.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### A few hundred files instead of tens of thousands
A city's map was over 40,000 files, slow to upload anywhere. Almost all of them were small pieces of the search index (the street and house number search of a whole city) and of the feature records behind popups and links. Each of these indexes is now **one file** (`.pack`). The viewer reads only the part a search or a click needs, with a byte-range request, the same way it reads the map archive. On a city-size test (2,012 streets, 37,838 addresses, 110,000 parcels):

| | 4.28 | 4.29 |
|---|---|---|
| Search index and feature records | 47,959 files, 45.8 MB | 2 files, 4.8 MB |
| Search manifest, loaded when the search starts | about 7 MB | 224 KB (64 KB compressed) |

A search for a street name then loads about 40 KB.
The parcel report's records are one file too. Any host that serves the map archive serves these files the same way (byte ranges, no added compression); a server that ignores byte ranges still works, it just sends each such file whole.

### Draw the published area on the map
*Map* tab → *Extent* → **Draw…**: the window steps aside, drag a rectangle on the QGIS map, and it becomes the published area. Esc cancels.

### Right in the Web menu
**Web → QWebMap: Publish Web Map…**, without a QWebMap submenu (also on the Web toolbar, as before).

| Run | Result |
|---|---|
| City-size test index | 47,959 files / 45.8 MB → 2 files / 4.8 MB |
| Tests | 244 passed, 5 skipped (publishing unit and integration tests, indexes, browser: feature lookup, parcel report, texts, extras, smoke; plugin package, Publish window, review fixes, export cache) |
