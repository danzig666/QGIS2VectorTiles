**QWebMap 4.24.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### About the map, and its documents (new Info tab)
- **Issued by, decree, in force from, data as of**: the dates show under the map's title (separate from the export date); the rest in a collapsible "About this map" block in the panel, and on prints.
- **Documents** (PDF, Word/ODT, RTF, text, images) are copied into the web map and listed in the panel. A popup value naming one (its file name or title) becomes a link to it.

### Web basemaps from WMS
**Add WMS…** in the Basemap tab: pick a WMS layer of your project (or type the service address and layer names) and visitors can choose it in the basemap menu, like an XYZ basemap.

### Overview map
An optional small map in the corner shows where you are (Interaction tab → *Overview map*). It can be collapsed.

### House numbers in the search
Choose a point layer of house numbers (Interaction tab → *House numbers*): the search then finds "Fő utca 12". The street comes from a field, or — if you leave it empty — from the nearest named OpenStreetMap street.

### Drawing
Tools → **Drawing**: points, lines, areas and text in a few colours. Nothing is stored on the site: the drawing travels in the shared link, and can be saved as GeoJSON or KML.

### 3D view, terrain and elevation profiles
- Give a polygon layer (e.g. buildings) a **3D height field** (Interaction tab) and the map's new **3D button** raises them.
- Choose a **DEM** raster layer in the Basemap tab (*Terrain*): the 3D view shows the relief, an optional **hillshade** is drawn above the basemap, and measured lines get an **elevation profile** (lowest/highest point, climb, descent).

### Map extract printing
Tools → Print now offers **A4/A3, landscape/portrait**. The printed sheet has a north arrow, a scale bar and a "Map extract" header with the issuer, decree, legal and data date, the exact scale and the print date.

| Run | Result |
|---|---|
| New browser tests (info, documents, WMS, overview, drawing, house numbers, terrain, 3D, profile, print layouts) | 9 passed |
| Related browser suites | 45 passed |
| Publishing unit, pipeline, export cache, progress and Publish window tests | 195 passed, 5 skipped |
