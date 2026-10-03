**QGIS2VectorTiles 4.6.4 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Printing on one page
The web map's **Print** button (Tools tab), Ctrl+P and the parcel report's **Print** now produce one A4 landscape page. Before, the print ran over three pages with a faded copy of the whole legend.
- The map is on the left. Before printing it is resized to its printed size and redrawn, so it is not stretched or clipped.
- The right side shows the title, the date and the attribution, then:
  - for a map print, a two-column legend of only what is drawn in the printed area;
  - for a parcel print, the parcel report without its buttons.
- If a legend is too long for one page, it continues on the next page between entries, never in the middle of one.
- After printing, the map returns to the view it had before.

### Parcel report: the zone code, never a feature ID
If the parcel report's zone code field was set to a feature ID (`fid`, `id`, `objectid` or the primary key), the report listed numbers such as 241 or 488 as zone codes. The export now notices this, writes a warning, and uses the field that holds the zone code instead. It picks the field the zoning layer's categorized style uses; otherwise a field used in its rule filters or labels, or one named like a code (`szab_ov`, `ovezet`, `kod`…).

The Publish window makes the same choice when you pick the zoning layer. Each part of the parcel shows the zone code in **bold** as its title. The code is not repeated among the zone's values.

| Run | Result |
|---|---|
| `pytest tests/browser/test_web_viewer_parcel.py` | 3 passed (new: a parcel print and a map print each fill the one-page sheet, with no buttons and only the drawn legend, and the view is restored afterwards) |
| `pytest tests/integration/test_publish_dialog_parcel.py tests/integration/test_publishing_parcel_report.py` | 6 passed (new: a feature-ID zone code field is replaced by the categorized style's field) |
| `pytest tests/browser/test_web_viewer_features.py tests/browser/test_static_package.py tests/unit/test_publishing_web_builder.py` + the two above | 27 passed |
| Print to PDF of the test plan's map (Chromium) | 1 page for the map print, 1 page for a parcel print |
