**QWebMap 4.35.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fix: hatches stay inside their polygons
- **Line-pattern fills clipped during render** (QGIS's default "Clip during render only"): QGIS cuts the drawn lines at the polygon's edge. The web map cut each line along its centre and drew it with its caps, so zoomed in the hatch stuck out of the polygon, most where a line runs along a narrow strip. Hatches in map units are now exported as the strips QGIS paints, cut to the polygon, from the zoom where they are 2 px wide; below that, lines without caps.
- **Hatches clipped before render or not clipped**: QGIS draws these line by line from each part's bounds; they were laid out like the default pattern, up to a whole spacing off. They now match QGIS.
