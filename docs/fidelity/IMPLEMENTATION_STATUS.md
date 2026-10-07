# Fidelity plan — implementation status

Status of each backlog item of *QGIS2VectorTiles: high-fidelity export implementation
plan* (30 Sep 2026) in this fork. "Done" means implemented **and** covered by tests in
`tests/`; everything else is stated explicitly. Tested on QGIS 3.34 only (see
[BASELINE.md](BASELINE.md)).

| ID | Deliverable | Status | What exists / what is missing |
|---|---|---|---|
| PR-01 | Baseline, fixtures, environment lock, smoke browser test | **Done** (3.34) | Baseline record; programmatic fixtures; unit / PyQGIS / browser levels; pinned MapLibre 6.11.2 (ES module build) + style-spec 26.4.4 + Chromium 1194; QGIS-vs-browser gallery (`tools/gallery`) and parity tests. *Missing:* runs on QGIS 3.44 and 4.x. |
| PR-02 | Falsy values, expression arithmetic, sprite errors, enums | **Done** | Typed property evaluation, typed expression builder, sprite error reporting, named marker-line flags, Qt5/Qt6 enum adapters, enum-based data-defined property names. |
| PR-03 | Typed bindings, diagnostics, strict mode, capability registry | **Done** | Stable `Q2VT_*` diagnostics (JSON + HTML), strict mode, generated capability table, per-layer field dependencies, data/zoom-driven property bindings in the report, empty-output diagnostics. |
| PR-04 | Context-aware units, valid camera/data expressions | **Mostly done** | One unit service, map-unit zoom curves with clamp knees, zoom-curve arithmetic (`mul`, `add`), hairlines. *Missing:* `@map_scale` properties still split datasets per zoom (now written only to their own zoom's tiles). |
| PR-05 | Visibility intervals, overzoom, explicit GDAL metadata | **Done** | Exact intervals, overzoom policy, per-layer tile zooms through the MVT `CONF` option (the VRT options were ignored by GDAL), truncated-metadata-aware archive inspection. |
| PR-06 | Marker/pattern renderer separation, deterministic atlas | **Done** | Whole-symbol renderer, oversampled sprites with `pixelRatio`, deterministic packer, true 2×, straight-alpha test. |
| PR-07 | LinePatternFill texture and materialized-hatch routes | **Done** | Screen units: verified seamless texture. Map units: hatch lines materialized per feature. |
| PR-08 | PointPatternFill and image/SVG patterns | **Done** | Exact repeat cells (fractional/dense periods repeated in the cell); map-unit grids materialized from the zoom where spacing ≥ 8 px, per-zoom textures below (rendered at the integer zoom: MapLibre grows `fill-pattern` with the map until the next zoom, so map-unit textures stay exact and screen-unit parts of such a mixed pattern, drawn at 1/√2, stay within ±41 %); patterns sized only in screen units are drawn at their true size at every zoom, on whole device pixels (patched MapLibre, `q2vt:screen-pattern`), screen hatches with QGIS's whole-pixel spacing; SVG fills follow QGIS (only with parsable SVG data, stroke sub-symbol exported); raster fills tiled. |
| PR-09 | Feature-context properties, native circles, sprite variants | **Done** | Native circle layers for plain circles; per-value sprite variants with a budget; static-vs-feature detection. |
| PR-10 | Exact marker-line positions | **Done** | Vertex/first/last/inner/central/segment-centre and map-unit interval markers (offset along the line, averaged angles) materialized at the QGIS positions; polygon outlines offset like QGIS (ring buffers); ring filters. Screen-unit intervals materialized per zoom (native placement beyond the last tile zoom). *Approximate:* screen-unit intervals and corner-angle averaging over a screen length are exact at the middle of each zoom (spacing within ±19 % inside the zoom). |
| PR-11 | Label typography, glyph calibration, offsets | **Done** | Glyph metrics calibrated (24 px em, bearings), size factor removed, font stacks shared with glyph generation, `ő`/`ű`. |
| PR-12 | Render ordering, geometry hardening | **Done** | QGIS draw order (later rules on top, rendering passes, layer tree), renderer order-by as sort keys, symbol-reach extent buffer with tile pruning, generators in the layer CRS, same-type generators drawn with their sub-symbol. *Not reproducible:* per-feature interleaving of different style layers. |
| PR-13 | Pinned labels and callouts | **Partial** | Data-defined X/Y labels exported at the point with the data-defined alignment, always shown; simple callouts as leader lines ending at the label anchor. *Missing:* QGIS PAL-computed placements; leaders ending on the label box. |
| PR-14 | Arrows / hash lines / filled lines | **Done** | Arrows are the polygons `QgsArrowSymbolLayer` fills (a port of its straight and curved arrow construction with Qt's own arcs, every head and arrow type, its vertex pairing; pixel-identical to QGIS in tests), built in painter pixels per eighth of a zoom for screen sizes and filled with the arrow's fill symbol; opaque multi-layer fills (drop shadows) keep QGIS's per-arrow drawing order; arrow lines keep all their vertices (no base-layer simplification). Hash lines as marker lines; filled lines as strokes. |
| PR-15 | Raster fallback / compositing groups | Not started | *Hybrid* mode is selectable but reports `Q2VT_HYBRID_NOT_AVAILABLE`. |
| PR-16 | Atomic publication, HTTP packaging, UI report | **Mostly done** | Cancellable `ogr2ogr`, XML-safe VRT, JSON/HTML report, strict failures remove only the new output; optional static web package (`web/`: XYZ tiles, relative-URL style, viewer; written atomically, works from any sub-directory of a plain web server); popups escape attribute text. *Missing:* PMTiles output, fidelity panel inside the viewer. |

## Symbology added beyond the plan (4.14)

Every built-in QGIS 3.34 symbol layer type is now converted; only plugin-provided symbol
layer types are reported as unsupported. Measured with the gallery (`tools/gallery`,
zooms 14.6 / 16.25 / 17.8, colour mismatch = pixels whose colour is outside the browser's
blend tolerance):

| Item | How | Gallery (worst zoom) |
|---|---|---|
| Lineburst | `line-pattern` image of the gradient across the line (colour 1 on the left), caps and joins included | 0.0 % |
| Interpolated line | Pieces with QGIS's colour and width for the middle of each piece | 0.0 % |
| Raster line | `line-pattern` image with QGIS's repeat (whole-pixel width), start and orientation; patched MapLibre keeps screen size at every zoom | 7.4 % (restarts at tile edges) |
| Vector field marker | Line from each point by the vector, per zoom for screen units | 4.3 % |
| Merged features / inverted polygons | The symbol's features united (inverted: the area beyond them) before any recipe | 0.0 % |
| Heatmap | MapLibre heatmap: radius and colours fitted to QGIS's quartic kernel, intensity per zoom from the densest point | 4.7 % |
| Point cluster | QGIS grouping per eighth of a zoom; cluster symbol with `@cluster_size` | 1.9 % |
| Point displacement | Ring / concentric rings / grid around the centre symbol, circle or grid lines | 0.7 % |

### Line effects and offsets (4.15)

| Item | How | Gallery (worst zoom) |
|---|---|---|
| Pointing arrow (curved repeated arrows, drop-shadow fill) | QGIS's arrow polygons; per-arrow layer order | 3.0 / 1.3 % at 14.6 / 16.25 (was 38 / 35 %); 17.8: see below |
| Effect emboss (inner shadow) | Runs by screen direction (36 buckets, direction over one line width per zoom); strips about 1 px wide across the line, each coloured by QGIS's rendering of a straight line of that direction; line ends and turns by QGIS's cap colour | 24 / 11 / 5 % (was 59 / 68 / 76 %); the rest where the zigzag's legs nearly touch |
| Effect neon (outer glow + inner shadow) | Glow as a blurred line from a copy simplified at an eighth of the glow's width per zoom (no spikes on dense vertices); inner shadow as above | 1.6 / 0.1 / 1.0 % (was 4.1 / 1.1 / 0.9 %, with spikes) |
| Topo steps (±1.4 mm offsets) | GEOS offset curves (QGIS's `offsetLine`) per eighth of a zoom up to the zoom where the layer's corners no longer make MapLibre's `line-offset` loop (99.5 % of the corners), native above | 15 / 16 / 15 % (was 24 / 20 / 16 %): the offset lines match; the rest is the phase of the tick markers (a screen interval is exact at the middle of each eighth of a zoom, so the phase drifts along long lines) |

At 17.8 the pointing-arrow line runs out of the view: QGIS clips it to the view (plus 10 %)
first and pairs the clipped vertices into different arrows, so its own arrows change while
panning (the gallery scores 70 %; with the symbol's "clip features to extent" off, QGIS
and the web map differ by 2.2 % of the pixels, the rest a three-vertex semicircle whose head side QGIS
decides by floating-point rounding).
Inner effects are computed for straight lines: where lines overlap or nearly touch, QGIS
shades their union, the web map each line (lighter strips are drawn last, so overlaps are
light as in QGIS).

*Not reproducible:* a line pattern or dash restarts where a vector tile cuts the line
(MapLibre measures the distance along a line per tile); QGIS fits gradients to the visible
part of a feature that runs out of view; QGIS clips lines to the view before pairing the
vertices of curved repeated arrows; pattern fills aligned to the feature (QGIS's default
"Align pattern to: Feature") are anchored to the map, while QGIS starts them at the
top-left corner of each feature part as clipped to the view (same size and look, a
different phase; the gallery's per-pixel score counts that phase as mismatch). Patterns
aligned to the viewport ("Align pattern to: Viewport", raster fills "Coordinate mode:
Viewport") start at the corner of the map canvas, exactly as in QGIS (4.14.1).

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
  in screen space) as one multipoint per polygon; map-unit font markers on points are exported as glyph outlines;
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
* 4.14: pattern fills sized in screen units keep the QGIS size at every zoom and are
  pixel-sharp (they were 0.7×–1.4× between zooms and resampled); screen hatches use QGIS's
  whole-pixel spacing.
* 4.14: gradient and shapeburst fills step about one colour level (more band polygons per
  feature, merged where narrower than a pixel two zooms past the archive).
* 4.14: screen-size marker intervals, point clusters and point displacement are exported in
  eighths of a zoom: more datasets and a longer export for such layers.
* 4.14: arrows draw their tapered body, QGIS-size heads and every fill layer (drop shadows).
