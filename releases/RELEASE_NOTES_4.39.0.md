**QWebMap 4.39.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)


### Improved (adopted from the original plugin's v4.0)
- **No blank map between zoom levels:** where a layer starts or ends at a zoom level (scale-dependent rules, per-zoom marker layouts), the published web map no longer goes blank for a moment while the next zoom's tiles load; the layer is drawn from the tiles already loaded until they arrive. Only where those tiles do not hold its data, so nothing is drawn twice. The layer list still greys a layer out at the zooms it does not draw.

### Fixed
- **Safer export of data-defined properties:** they are read by following only the style objects that hold them (symbol layers, sub-symbols, label text format and callout), no longer by calling every method of every style object. One of those methods renders symbol previews, which crashed the export process in the original plugin. The properties found are the same (checked on every QGIS symbol layer and callout type).
