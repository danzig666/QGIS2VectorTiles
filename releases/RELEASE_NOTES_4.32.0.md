**QWebMap 4.32.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### New: finding layers in projects with hundreds of them
- **Map tab:** a filter box over the layer list shows, as you type, the layers whose name contains the text (upper or lower case, accents not needed) with their groups; a group's name shows its whole group. **Only published layers** hides the rest. Filtered rows keep their settings.
- **Interaction tab:** the published layers are listed by name, with a find box when there are more than 10.
- **Every layer drop-down** (extent, house numbers, parcel report tables, terrain, the WMS picker) takes typing and lists the layers whose name contains the text.
- The **Zoomed-out load…** window has a find box too.

### New: Zoomed-out load… zooms can be edited
- Every layer has its own feature zoom and label zoom, not only the suggested ones; they start at the suggestion (ticked) or at the zoom the layer is shown from now (unticked). Changing a zoom ticks it; the lowest zoom removes a limit.

### Fixes
- **Pattern fills in map units showed only their outline** on maps exported from zoom 0 (the Publish window's default), e.g. a hatched building area: the per-zoom textures were spent on the lowest zooms, where the pattern is smaller than a pixel. The highest zooms get them now.
- **Dimension labels longer than their line** were not drawn. With *Allow degraded placement* QGIS draws such a label level, its bottom-left corner on the line; so does the web map now.
- **Line labels overlap where QGIS allows it** (*Allow overlap if required*): a label with no clear spot was left out.
- **Label frames turn with their label** (*Sync with label*, *Offset of label*), corners included (MapLibre left the corners of a turned frame unturned).
- **Free (angled) polygon labels sit in the middle of long strips**, nearest the polygon's centroid as in QGIS.
- A closed Publish window is freed with its layer lists (they kept reacting to every project change).

| Run | Result |
|---|---|
| Tests (full suite, each file in its own process) | 902 passed, 5 skipped |
| Viewer, label and browser comparison tests after the last change | all passed |
