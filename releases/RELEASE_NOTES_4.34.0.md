**QWebMap 4.34.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Exports that failed or never finished
- **Layer names with an apostrophe** (`Regioni d'Italia`) broke the export: the name went into a QGIS expression unquoted, every rule of the layer failed and there was nothing to publish. Names, field names and expressions are now quoted and escaped everywhere the exporter builds expressions, filters and launch scripts (quotes, backslashes, `--` comments at the end of an expression).
- **Huge exports of coarse data finish.** Country borders of a continent down to zoom 16 were tens of millions of filled tiles: the export seemed to hang near its end for hours. When the tiles would be far too many (over 200,000), they now end at the zoom that holds all of the data's detail, and the web map zooms in further on them — it looks the same. Small and detailed maps (a town's parcels or buildings) keep every zoom. The export log and the fidelity report say where the tiles end.
- **Nothing to publish** now stops right after the export with the cause (the rules that failed), instead of an unclear "No MBTiles file" error at the end. Rules that fail in an otherwise successful export are listed in the Publish window.

### Expressions that depend on the map view
- **`@map_extent`, `@map_extent_center`, `@map_extent_width`, `@map_extent_height`, `@map_crs`, `@map_rotation`** and the like were empty at export, so rules filtered by the map centre, crosshairs and labels generated from it were missing. They now hold the web map's starting view (the export extent). QGIS updates them as you pan; the web map keeps the starting view's values, and the fidelity report says so.

### Symbols and labels
- **Gradient and shapeburst fills with a data-defined colour** (a random or per-attribute colour) are coloured per feature (they took the static colour).
- **Labels with a geometry generator on polygon layers** are placed on the generated geometry (they went to the polygon's middle).

| Run | Result |
|---|---|
| Two user projects that failed or did not finish (map-view expressions with a quoted layer name; country borders and 2,143 earthquakes over Southeast Asia) | both export: the first in seconds, the second in about 2 minutes instead of hours; QGIS vs web matches at the starting view and past the last tile zoom |
