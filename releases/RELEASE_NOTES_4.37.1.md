**QWebMap 4.37.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fix
- **The Web menu disappeared from the QGIS menu bar**: since 4.29, the "QWebMap: Publish Web Map…" entry sits right in the Web menu. QGIS only puts the Web menu on the menu bar when a plugin adds a submenu to it and that submenu is its only entry. So when QGIS happened to load QWebMap before MetaSearch (or another plugin with a Web submenu), the whole Web menu was missing, with both plugins in it. QWebMap now puts the Web menu on the menu bar itself, in the place QGIS uses (after Database, else after Raster). The toolbar button was not affected.
