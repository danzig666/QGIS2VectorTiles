**QWebMap 4.33.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

This release fixes conversion bugs found by an audit of the QGIS symbology and labelling conversion. The Publish window exports from zoom 0 to 16 and the web map zooms in to 22, while earlier tests had mostly used narrow ranges such as 14–19. Most bugs were values worked out once at zoom 0 or at the last tile zoom, sizes mixing map units and millimetres, and places where the export did not follow QGIS's own rules.

### Zooming in past the last tile zoom (17–22)
- **Symbols in millimetres keep their size:** arrows, point patterns, shapeburst bands, glow and shadow, offset markers and vector-field lines grew 2× per zoom (or thinned out until they vanished). Fine map-unit dashes now switch to drawn dashes there too.
- **Rules, layers and labels shown only beyond the last tile zoom** (for example a detail rule from 1:1000) were left out of the web map; they are drawn now.
- **`@map_scale` expressions** (rule filters, data-defined sizes and switches) keep changing past the last tile zoom, label filters no longer keep the value of the first zoom, `@zoom_level` works, and point clusters regroup as you zoom in.
- **Map-unit pattern fills** keep growing with the map (they stayed 2–16× too small) and stay sharp.
- The extra drawing this needs is budgeted per tile. Maps with many such symbols get bigger last-zoom tiles: on a 64-layer town plan the tiles grew by about a third and the export took about a fifth to a third longer.

### Map units and millimetres
- **Labels mixing units:** map-unit text with a millimetre offset or distance disappeared, and millimetre text with a map-unit distance sat on its point, line or polygon centre. Offsets, around-point distances and line distances now follow the zoom; label frames of map-unit text no longer grow huge when zoomed in; map-unit frames around screen-size text are no longer 1 px.
- **Map-unit custom dashes** (and millimetre dashes on map-unit lines) were a solid line from zoom 0.
- **Line pattern fills with map-unit spacing or offset**, and the arrow's map-unit shadow offset, were frozen at their zoom-0 size.
- **Min/max size and scale limits** (map unit scale) were applied twice or ignored for markers, patterns, intervals and split outlines.
- **Projects in degrees (EPSG:4326) or feet**, the renderer's **reference scale**, **"Meters at scale"** in Web Mercator projects (1/cos(latitude) too large) and **geometry-generator units** are converted as QGIS does.
- **Random marker fills with a map-unit density** made the Publish export run out of memory; very dense fills become a tint.

### Rules and labels
- **ELSE / "all other values"** drew features that QGIS hides (unchecked or out-of-scale siblings).
- A symbol layer's data-defined **enabled** no longer hides labels; a label's own **scale range** is honoured when its rule has one.
- **Polygon labels** avoid each other and respect *Never overlap*; **Label every part**, **Outside polygons** placement, small polygons and HTML-formatted labels work in the viewer.
- **Line labels:** *Horizontal* labels on lines sit on the line's middle (not its first vertex); upside-down labels are turned; long labels across tile edges, repeated curved labels and *Merge connected lines* (as QGIS merges them) work.

### Symbols
- A **map-unit hatch** vanished where a hatch line touched a polygon corner; hatches of very large polygons were dropped without a warning.
- **Translucent shapeburst** fills stacked into a wrong tint; a symbol's **opacity** is kept on its drawn parts (marker lines, hatches, pattern grids), while geometry-generator sub-symbols keep their own opacity as in QGIS.
- **Geometry-generator fills** keep their outline; a generator on a point layer no longer shares its data with the plain symbol.
- **Fill offsets in map units**, **data-defined marker opacity**, **percentage offsets** of point patterns, **"Draw line only inside polygon"**, **hashed lines'** data-defined interval/angle/length, **markers before a line's start** (negative offset) and **vertex markers after a repeated first vertex** are converted.
- **Millimetre line offsets** no longer draw as thin tapered slivers; dense low-zoom tiles no longer drop features silently; the fidelity report is no longer flooded at min zoom 0.

| Run | Result |
|---|---|
| Tests (full suite, each file in its own process) | 1342 passed, 5 skipped |
| Built-in QGIS style library (118 styles), QGIS vs web, against 4.32.0 | 109 pixel-identical, the others equal or closer to QGIS on a visual check (one label regression found on the way was fixed) |
| 57 label configurations, QGIS vs web, against 4.32.0 | 20 better, 36 unchanged, none worse |
| Four real projects, exported with the Publish defaults, against 4.32.0 | no errors; three with tile sizes within 2 %, one (64 layers) with tiles +36 % (symbol sizes kept past the last tile zoom) |
