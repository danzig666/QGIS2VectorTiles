**QWebMap 4.36.3**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Labels
- **Too small to read**: labels whose text size is set in map units (metres) shrink as the map zooms out until they are unreadable specks. They are now hidden where their text is smaller than 6 px on screen. Publish window, Output tab: "Hide labels smaller than [6] px", on by default; untick it to show them at every size. Labels sized in points, millimetres or pixels and data-defined sizes are not affected.
- **Pixel size-based visibility**: the Rendering tab's "Pixel size-based visibility" of a QGIS label (minimum and maximum px) is now honoured for labels sized in map units.

### Lines
- **Dash patterns zoomed out**: a dash pattern in map units whose size has a scale range ("Min/max size" in the unit settings) became a solid line far zoomed out. The dashes are now limited with the same scale range as the line width, so the pattern keeps its shape.
