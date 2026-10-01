**QGIS2VectorTiles 4.1.8 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

Install it in QGIS with *Plugins → Manage and Install Plugins → Install from ZIP* and choose `QGIS2VectorTilesFork-4.1.8.zip`. Run it from *Processing → Toolbox* (Ctrl+Alt+T) → **QGIS2VectorTiles (fork)**.

### Labels
- **Labels avoid each other.** Labels that QGIS may overlap (*Allow overlaps if required* or *without penalty*) no longer pile up in MapLibre. Each label first looks for a free spot. Horizontal and free polygon labels can also move to just above, below or beside their point. A zone label and a parcel number that share a centre now sit one above the other. The web viewer still draws a label overlapping others when no spot is free, as QGIS does. Other MapLibre clients leave such a label out instead.
- Map symbols (markers) no longer push labels away; in QGIS they never hide another layer's labels. Labels keep MapLibre's 2 px spacing (was 10 px), so more of them fit without overlapping.
- **Visible-polygon labels stay put while panning.** A label keeps its place as long as that point is still on the visible part of its polygon and not at the screen edge. It moves only when it would leave the polygon or the screen. Labels on tiles that are still loading no longer blink.

### Fixes
- **Parcel numbers** (*Földrészletek*, *Alrészlet feliratok* and other visible-polygon labels) did not render in the viewer. Their polygon layer was exported without the label size field.
- Visible-polygon labels whose polygon centre lies outside the export extent were missing from the static label layer.
- The viewer now keeps the polygons of visible-polygon labels loaded even when no other layer draws them.
