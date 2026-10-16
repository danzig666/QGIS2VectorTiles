**QWebMap 4.40.2**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)


### Changed
- **The browser's Back button closes what is open** instead of leaving the map: a feature popup, the reading window, the panel on phones, Street View, and a measuring or drawing tool, newest first. Things closed with their own button or Escape leave no extra step behind, so with nothing open Back leaves the page as usual. A Back that only closes something keeps the current view (it no longer reloads the map as a pasted link would).
