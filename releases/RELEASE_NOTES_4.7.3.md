**QGIS2VectorTiles 4.7.3 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Measurement snapping
- **Setting it up:** Publish window → Interaction tab → pick a layer → **Measurements snap to this layer (corners and edges)**. Choose any layers, e.g. parcels and regulation lines.
- **Measuring:** in the web map's distance and area tools, a point snaps:
  - to the nearest corner (vertex) of those layers within about 12 px of the pointer (22 px on touch screens);
  - otherwise to the nearest point of an edge or line.
- **What you see:**
  - a red ring shows where the point will go (bigger on a corner);
  - a dashed line previews the next segment;
  - holding **Alt** places a free point;
  - the Tools tab has an on/off switch ("Snap to: …"), which the browser remembers.
- **What it snaps to:** only what the map draws at that moment, so a switched-off layer does not catch the pointer. It uses the geometry already loaded with the map tiles, with no extra download. Precision is a few centimetres at the deepest zoom and coarser when zoomed out, so zoom in for precise points. The measurement remains informational, not a survey.
- **Tile edges:** where a polygon is cut at a tile edge, the cut is neither a corner nor an edge, so the pointer never snaps to it.

| Run | Result |
|---|---|
| `pytest tests/browser/test_snap.py` | 2 passed (new: corner first, then edge, within the radius only; no snapping to tile cuts or to layers not marked; label layers left out) |
| `pytest tests/integration/test_publishing_label_always.py` | 1 passed (now also: the manifest marks the snap layer) |
| Publish window, profile, web builder, validation and viewer suites | 126 passed |
| Test plan, parcels and regulation lines marked, in the browser | the pointer 8 px from a parcel corner snaps to it (≈2 cm), near an edge to the edge; Alt: no snapping; the clicked point is the snapped one |
