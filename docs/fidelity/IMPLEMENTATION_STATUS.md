# Fidelity plan — implementation status

Status of each backlog item of *QGIS2VectorTiles: high-fidelity export implementation
plan* (30 Sep 2026) in this fork. "Done" means implemented **and** covered by tests in
`tests/`; everything else is stated explicitly. Tested on QGIS 3.34 only (see
[BASELINE.md](BASELINE.md)).

| ID | Deliverable | Status | What exists / what is missing |
|---|---|---|---|
| PR-01 | Baseline, fixtures, environment lock, smoke browser test | **Done** (3.34) | Baseline record; programmatic fixtures; unit / PyQGIS / browser levels; pinned MapLibre 5.11.0 + style-spec 24.3.1 + Chromium 1194; QGIS-vs-browser gallery (`tools/gallery`) and parity tests. *Missing:* runs on QGIS 3.44 and 4.x. |
| PR-02 | Falsy values, expression arithmetic, sprite errors, enums | **Done** | Typed property evaluation, typed expression builder, sprite error reporting, named marker-line flags, Qt5/Qt6 enum adapters, enum-based data-defined property names. |
| PR-03 | Typed bindings, diagnostics, strict mode, capability registry | **Done** | Stable `Q2VT_*` diagnostics (JSON + HTML), strict mode, generated capability table, per-layer field dependencies, data/zoom-driven property bindings in the report, empty-output diagnostics. |
| PR-04 | Context-aware units, valid camera/data expressions | **Mostly done** | One unit service, map-unit zoom curves with clamp knees, zoom-curve arithmetic (`mul`, `add`), hairlines. *Missing:* `@map_scale` properties still split datasets per zoom (now written only to their own zoom's tiles). |
| PR-05 | Visibility intervals, overzoom, explicit GDAL metadata | **Done** | Exact intervals, overzoom policy, per-layer tile zooms through the MVT `CONF` option (the VRT options were ignored by GDAL), truncated-metadata-aware archive inspection. |
| PR-06 | Marker/pattern renderer separation, deterministic atlas | **Done** | Whole-symbol renderer, oversampled sprites with `pixelRatio`, deterministic packer, true 2×, straight-alpha test. |
| PR-07 | LinePatternFill texture and materialized-hatch routes | **Done** | Screen units: verified seamless texture. Map units: hatch lines materialized per feature. |
| PR-08 | PointPatternFill and image/SVG patterns | **Done** | Exact repeat cells (fractional/dense periods repeated in the cell); map-unit grids materialized from the zoom where spacing ≥ 8 px, per-zoom textures below; SVG fills follow QGIS (only with parsable SVG data, stroke sub-symbol exported); raster fills tiled. |
| PR-09 | Feature-context properties, native circles, sprite variants | **Done** | Native circle layers for plain circles; per-value sprite variants with a budget; static-vs-feature detection. |
| PR-10 | Exact marker-line positions | **Done** | Vertex/first/last/inner/central/segment-centre and map-unit interval markers (offset along the line, averaged angles) materialized at the QGIS positions; polygon outlines offset like QGIS (ring buffers); ring filters. *Approximate:* screen-unit intervals (native; spacing within ±41 % between integer zooms), corner-angle averaging exact at one zoom per dataset. |
| PR-11 | Label typography, glyph calibration, offsets | **Done** | Glyph metrics calibrated (24 px em, bearings), size factor removed, font stacks shared with glyph generation, `ő`/`ű`. |
| PR-12 | Render ordering, geometry hardening | **Done** | QGIS draw order (later rules on top, rendering passes, layer tree), renderer order-by as sort keys, symbol-reach extent buffer with tile pruning, generators in the layer CRS, same-type generators drawn with their sub-symbol. *Not reproducible:* per-feature interleaving of different style layers. |
| PR-13 | Pinned labels and callouts | **Partial** | Data-defined X/Y labels exported at the point with the data-defined alignment, always shown; simple callouts as leader lines ending at the label anchor. *Missing:* QGIS PAL-computed placements; leaders ending on the label box. |
| PR-14 | Arrows / hash lines / filled lines | **Done** | Arrows as in `QgsArrowSymbolLayer` (straight first→last, circular arcs, per-segment, triangular heads); hash lines as marker lines; filled lines as strokes. *Approximate:* half and tapered arrows. |
| PR-15 | Raster fallback / compositing groups | Not started | *Hybrid* mode is selectable but reports `Q2VT_HYBRID_NOT_AVAILABLE`. |
| PR-16 | Atomic publication, HTTP packaging, UI report | **Mostly done** | Cancellable `ogr2ogr`, XML-safe VRT, JSON/HTML report, strict failures remove only the new output; optional static web package (`web/`: XYZ tiles, relative-URL style, viewer; written atomically, works from any sub-directory of a plain web server); popups escape attribute text. *Missing:* PMTiles output, fidelity panel inside the viewer. |

## Behavior changes users may notice

* Scale breakpoints between integer zooms are now kept (fractional `minzoom`/`maxzoom`),
  and rules whose range lies between two integer zooms are no longer lost.
* ELSE rules now follow the scale ranges of their siblings as QGIS does.
* Horizontal/free polygon labels are centred instead of using variable anchors; only
  "around point" placements use variable anchors.
* Unsupported fills (gradient, shapeburst, …) are omitted and reported instead of being
  drawn black; failed sprites are omitted and reported instead of being transparent.
* Data-defined widths, sizes and opacities are converted to the browser's units.
* Valid polygons keep their rings exactly as stored (start vertex, hole orientation);
  only invalid geometries are repaired. Marker intervals, dashes and offsets on polygon
  outlines now start where QGIS starts them, on every ring.
* Map-unit custom dashes are exported as their dashes from the zoom where the pattern is
  6 px long, so markers drawn in the gaps stay in the gaps.
* Random marker fills draw the QGIS number of markers (positions differ: QGIS draws them
  in screen space); map-unit font markers on points are exported as glyph outlines;
  font markers with characters beyond U+FFFF are sprites.
* Point-pattern fills clipped to the shape are drawn by QGIS through a texture whose cell
  is truncated to whole pixels (`int(2 × spacing)`): at a given scale its rows drift up to
  a cell against the true spacing, and patterns of markers larger than their spacing break
  at the texture seams. The export draws the pattern the style describes; the gallery
  scores such items high although nothing is misplaced.
* Line labels with map-unit text sizes are drawn (MapLibre dropped them below zoom 18);
  "show all labels" layers allow overlapping labels. A line label may still sit on a
  different stretch of the line than in QGIS (QGIS prefers the middle) and repeats every
  `symbol-spacing` pixels.
* Line hatches (`LinePatternFill`) are visible and seamless; previously they were
  transparent.
* Later rules of a rule-based renderer now draw above earlier ones (as in QGIS); symbol
  layers follow their rendering passes.
* Zero-width lines are drawn as one-pixel hairlines.
* Offsets of polygon outlines move inwards for positive values whatever the ring
  orientation (QGIS buffers each ring).
* Tile archives no longer repeat per-zoom datasets at every zoom (smaller archives).
