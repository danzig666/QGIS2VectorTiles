**QWebMap 4.45.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fixed
- **Trimmed lines** (*Trim lines* start / end distance of a simple line) are drawn trimmed; the web map drew the whole line.
- **Values computed per vertex** (`@geometry_point_num`, `@geometry_point_count`) of markers on line vertices are evaluated at every vertex, as in QGIS: an orienteering course's control numbers were all the same. Such a data-defined offset in map units or meters moves the marker.
- **Geometry generators** evaluate their sub-symbol's values on every part as QGIS does (`@geometry_part_num` of each part, `$currentfeature` the source feature): a measure written at every node showed 0.
- **Glows and drop shadows of lines** fade out as wide and as strong as QGIS's blur, and are painted once per pixel, as QGIS paints the effect: a dark wedge appeared inside sharp corners.
- **An arrow's drop shadow** is soft, as in QGIS (it was a hard grey copy).
- **Hatch lines with a custom dash pattern** are dashed even when their line style is set to solid, as QGIS draws them.
- **Shapeburst fills with a blur** fade as QGIS's blurred fill does (they faded with a sharp edge).
