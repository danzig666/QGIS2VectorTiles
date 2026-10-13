**QWebMap 4.36.7**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fixes
- **Large markers in map units when zoomed in**: a simple marker sized in map units — for example a 920 m dashed circle on a polygon's centroid (Centroid fill) showing a protection distance — was one picture: the browser draws a picture only from the map tile of its centre point, so the circle vanished once its centre was off screen, and past its last prepared zoom it was scaled up (blurred, with a thick outline and stretched dashes). From the zoom where such a marker is 128 px wide it is now drawn as its real shape around the point: exact at every zoom, its outline a line that keeps its width and dash pattern, and visible wherever the circle is on screen. Below that zoom it is a picture as before. (Zoomed in on the edge of the circle, QGIS itself does not draw it, as the feature is outside the view; the web map keeps showing it.)
