**QWebMap 4.34.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fixes: names and expressions that broke the export
After the `Regioni d'Italia` fix in 4.34.0, a check of every place where the exporter builds QGIS expressions from project text found more of the same kind. Each could make a layer, a rule or its labels silently go missing from the web map, or stop the export:
- **Field names with a double quote** (`my"field`), used for labels, popups, filters, 3D height or all fields, and **filter fields with capital letters** (`Zona`): the field was missing from the tiles, or the export stopped.
- **Expressions ending in a `-- comment`** (rule filters, labels, data-defined properties, geometry generators, label positions): the comment swallowed the closing bracket the exporter added after it.
- **A backslash as the label wrap character**, and **whole-word label text replacements** with characters that are not a valid pattern: now handled as QGIS does (an invalid one is skipped, the label stays).
- **`$area`, `$length` and `$geometry` after a comment or an escaped quote** were not measured in the layer's CRS (wrong values, no error).
- **Heatmap weights from a text field** are read as numbers, as QGIS does.
- **Export folders with spaces, quotes or `$`** in the name: the preview launcher script and the result layer quote the path.

| Run | Result |
|---|---|
| New tests for each case (each fails without its fix) | all passed |
| Expression, label, flattener, end-to-end and publishing test files after integration | 236 passed |
