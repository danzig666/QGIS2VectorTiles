**QWebMap 4.26.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Full regulation texts in the parcel report
- **Parcel report → Full texts table** (optional): choose a table with a zone code field and a text field holding every regulation that applies in the zone. The text is simple HTML: headings, paragraphs, lists, emphasis and tables. The plugin fills in the two fields when their names are familiar (`szab_ov`, `eloiras_html` …).
- **In the web map:** under each zone of a clicked parcel there is a closed section, "Full regulations of zone Lke-1". The text is downloaded only when it is opened, one small file per zone.
- **Safety:** everything else is removed twice, at export and again in the browser. That means links, images, scripts, styles and all attributes, except a table cell's `colspan`/`rowspan`. The text of removed elements stays.
- **Printing:** an opened text is printed with the parcel report.
- **Review tab:** lists the table. The texts become public.
- **Viewer add-ons** of special editions can show the same text in popups (`ParcelReport.regulationBlock`).

| Run | Result |
|---|---|
| Full test suite (`pytest`: unit, PyQGIS, browser; QGIS 3.34, Chromium) | 667 passed, 5 skipped |
| New: `tests/unit/test_rich_text.py`, `tests/browser/test_parcel_texts.py`, `test_regulation_texts_table_round_trip` | 5 passed (scripts, links, images and attributes removed at export and in the browser; one file per zone, loaded when opened; the window keeps the table) |
