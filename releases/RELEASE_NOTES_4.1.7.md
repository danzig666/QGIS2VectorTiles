**QGIS2VectorTiles 4.1.7 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

Install it in QGIS with *Plugins → Manage and Install Plugins → Install from ZIP* and choose `QGIS2VectorTilesFork-4.1.7.zip`. Run it from *Processing → Toolbox* (Ctrl+Alt+T) → **QGIS2VectorTiles (fork)**.

### New: polygon labels on the visible part of the polygon
- QGIS places a polygon label on the part of the polygon that is visible on screen ("Centroid: visible polygon", the QGIS default). The export used the centre of the whole polygon, so labels of long or large polygons were missing whenever that centre was off screen.
- The web viewer now does what QGIS does. After every move it clips each polygon to the screen and puts one label at the centre of the visible part, or at a point inside it when the centre falls outside, as QGIS does. The export ships the polygons for this in extra `_vp` layers.
- *Polygon Labels Base* has a new default, **As set in each layer's labels**, which follows each layer's QGIS setting. *Whole Polygon* and *Visible Polygon* still force one mode for all layers.
- Other clients (the OpenLayers viewer, QGIS reading `style.json`, other MapLibre apps) still draw the labels at the centre of the whole polygon.

### Fixes
- **Layers whose file has no `.prj`** (or whose CRS is set in the project) were exported empty, without a message, for example *Épületek*. The export now uses the CRS the layer has in the project.
- **Text replacements** of labels are applied as in QGIS: in order, with the case option, and with whole-word matching as QGIS does it.
- **Data-defined properties that use a missing field** are ignored, as QGIS does. QGIS then draws the static value. The export used to evaluate them, so *Szabályozás övezetkódok* labels were blue instead of dark grey. The fidelity report lists such properties (`Q2VT_DDP_MISSING_FIELD`).
- **Data-defined label sizes in map units** were up to 4× too small (*Földrészletek*). MapLibre clipped the sizes at zoom 24. These curves now have a stop at every zoom.
- **Layers drawn per zoom disappeared beyond the maximum zoom + 1**, for example *Övezethatár* past zoom 18. The last zoom now follows the overzoom setting.
- **Label frames with a stroke in map units** were drawn as a pale hairline. The stroke now has the QGIS width at every zoom, and a thick stroke no longer fills the whole frame.

### Tools (not in the zip)
- `tools/gallery/project_compare.py` compares a real project with its export: chosen layers or the whole map (`--layers "*"`), QGIS rendered in the project CRS at each browser zoom, with the comparison page.
