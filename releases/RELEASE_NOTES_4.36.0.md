**QWebMap 4.36.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Speed slider for the zoomed-out load
- The *Zoomed-out load…* window (Publish window, Map tab) has a slider from **very slow** to **very fast**. Faster: small features and crowded labels are suggested to be hidden from closer zooms on, so there is less to load and draw when zoomed out. Slower: more is shown zoomed out. Balanced (the middle) is the earlier behaviour.
- Moving the slider suggests the zooms again; zoomed in, every layer is shown in full either way. The position is remembered for next time.
