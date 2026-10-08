**QWebMap 4.25.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Overview map, 3D view and drawing are optional
- All three are **off by default**. Turn them on under *Interaction → Viewer*: **Overview map (inset)**, **3D view** and **Drawing tools**.
- **3D view** is a new switch. The 3D button needs it, and so do the raised buildings and the terrain relief.
- Without the 3D view, the terrain still gives the hillshade and the elevation profiles.
- The Review tab lists the extras that are on. It warns when a 3D height field is set but the 3D view is off.
- **Existing settings:** drawing was on by default in 4.24. Turn it on again if you want it. Maps with a 3D height field or terrain need **3D view** ticked to keep their 3D button.

### Fixes from a review of 4.22–4.24
**Publish window**
- Applying a preset or importing a settings file no longer loses per-layer settings (labels, snapping, search fields, popup fields). Before, the layer shown on the Interaction tab overwrote them.
- Parcel report → Zone regulations: a field's type (link, number) is kept when the settings are saved again.
- A layer removed from the project while the window is open no longer causes an error.
- House numbers: the number field starts empty unless the plugin finds one named like a house number (`hsz`, `hazszam`, `housenumber` …). Before, the first field (often `fid`) was chosen.
- The basemap shown at start stays the same when web basemap rows are added or unticked.
- **3D height field:** text fields holding numbers can be chosen.
- **Terrain:** only file rasters are offered; hillshade and exaggeration are enabled once an elevation layer is chosen.
- **Review tab:** it names the third parties visitors' browsers contact (web basemaps, Google Street View), instead of always saying "none".
- **Approval:**
  - A new review is needed when web basemaps or Street View change.
  - A new review is also needed when a published document's file content changes.
  - Because of these checks, the first publish after updating asks for approval once.
- **WMTS layers** are no longer offered under *Add WMS*.
- **Web basemaps as QGIS XYZ connections:** they are saved when the settings are saved, not on every look at the Review tab. A QGIS connection of the same name with a different address is never overwritten.

**Export**
- **Terrain problems are warnings:** an unreadable or online elevation layer, or running out of memory, is reported and the map is published without terrain. Before, the whole export stopped.
- **Terrain uses the layer's CRS as set in QGIS:** a DEM file without a CRS, or one overridden in QGIS, is placed correctly.
- **Progress bar:** raster layers and terrain each get their own part of the progress bar, so it keeps moving during the terrain step.
- **House numbers far north:** they find the nearest street within 150 m at any latitude; north of about 48° some were missed. Same-name street pieces are joined correctly far north.
- **Empty house numbers:** a NaN (empty number) house number is skipped.
- **WMS addresses with a server path** (MapServer `map=/srv/…`) no longer stop the release as a "leaked local path".
- **Documents:** a published `.txt` document may mention paths.
- **Checked before the export starts:** a missing document file and a missing house-number field. Before, this was found only after the tiles were made.
- A value of the wrong type in a hand-edited settings file is reported as a settings problem.
- **Zone regulations:** zone codes match also when one side stores `12.0` and the other `12`, or has spaces around the code.

**Web map**
- **Drawing:**
  - A drawing opened from a shared link shows at once. Before, it appeared only after the next edit.
  - A drawing too long for a link is refused with a message, instead of breaking the whole link.
  - Long texts no longer split an emoji.
  - Saved file names keep accented letters as plain letters (`Orbottyan`, not `O-rbottya-n`).
- **Enter key:** pressing Enter in the drawing text box or in the search box no longer ends drawing or measuring.
- **Street View** now stops an active drawing.
- **3D view:**
  - Outline-only, transparent and hatched fills are no longer raised as solid black or grey blocks.
  - Stacked symbol layers no longer make duplicate blocks.
  - With reduced motion turned on, leaving 3D also switches tilting off.
- **Terrain:** a missing or unreadable terrain file no longer stops the map from loading; it shows a warning.
- **Print:**
  - Printing leaves the 3D view for the sheet and returns to it afterwards.
  - A parcel prints centred on the parcel also at a chosen scale.
  - The map is redrawn before Ctrl+P takes its snapshot.
- **Elevation profile:**
  - A profile that cannot load says so, instead of showing "Loading…" forever.
  - Starting a new measurement hides the old profile.

| Run | Result |
|---|---|
FULLRESULT
