**QGIS2VectorTiles 4.4.0 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

Install it in QGIS with *Plugins → Manage and Install Plugins → Install from ZIP* and choose `QGIS2VectorTilesFork-4.4.0.zip`.

### Parcel report (telekinformáció)
- New *Parcel report* tab in the Publish window. Choose:
  - the parcel layer and its unique id (e.g. `hrsz`), and the parcel fields to show;
  - the zone layer and its code field (e.g. `szab_ov`); that layer's style gives the zone graphics;
  - the lines that cut parcels (regulation line, zone boundary);
  - the restriction layers, each with a title, explanation, legal reference, a name field and, for lines and points, a protection distance;
  - optionally, a zone-regulations table, for the local building code later.
- **Clicking a parcel** in the web map opens its report in the panel (bottom sheet on phones):
  - the **total area**;
  - the **parts cut by the zoning, the regulation line and the zone boundary**, each with its area and share, its zone graphic, the lines that cut it and the zone values; the parts are numbered on the map too;
  - **every restriction touching the parcel**, with its **legend graphic**, overlap area and share, names, explanation and reference;
  - a notice that the data is informative. The report can be linked and printed.
- Everything is computed when exporting, in QGIS, from the exact geometry. In a check on the owner's project, 20 parcels matched an independent QGIS Processing intersection to within 0.05 m². The browser only shows the result, and only the fields you choose become public.
- The legend graphics show what QGIS really draws: rule-based, categorized and data-defined colours.

### Legend
- **Only what is visible**: the legend can list only the entries drawn in the current view. The publisher sets the default (Interaction tab), and visitors can switch it on the legend.
