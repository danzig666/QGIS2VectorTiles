# Fidelity plan — implementation status

Status of each backlog item of *QGIS2VectorTiles: high-fidelity export implementation
plan* (30 Sep 2026) in this fork. "Done" means implemented **and** covered by tests in
`tests/`; everything else is stated explicitly. Tested on QGIS 3.34 only (see
[BASELINE.md](BASELINE.md)).

| ID | Deliverable | Status | What exists / what is missing |
|---|---|---|---|
| PR-01 | Baseline, fixtures, environment lock, smoke browser test | **Done** (3.34) | Baseline record with 21 reproduced defects; programmatic fixtures; unit / PyQGIS / browser levels; pinned MapLibre 5.11.0 + style-spec 24.3.1 + Chromium 1194. *Missing:* runs on QGIS 3.44 and 4.x; QGIS-vs-browser reference image comparison. |
| PR-02 | Falsy values, expression arithmetic, sprite errors, enums | **Done** | Typed property evaluation (0/False/'' kept, NULL → static, eval errors reported), typed expression builder (no list arithmetic), sprite input-type/exception reporting, named marker-line flags, Qt5/Qt6 enum adapters. |
| PR-03 | Typed bindings, diagnostics, strict mode, capability registry | **Mostly done** | Stable `Q2VT_*` diagnostics (JSON + escaped HTML, path redaction), Strict mode (fails before publication), capability registry that generates `CAPABILITIES.md`, per-source-layer field dependencies. *Missing:* render-order planner; `PropertyBinding` records are defined but not yet collected into the report. |
| PR-04 | Context-aware units, valid camera/data expressions | **Mostly done** | One unit service (mm/pt/px/in/map units/meters-at-scale; unknown = error), map-unit zoom curves with exact clamp knees, feature arithmetic inside zoom stops, label sizes/halos with their units. *Missing:* adaptive QGIS sampling for non-linear sizes; unit-accuracy calibration against QGIS renders. |
| PR-05 | Visibility intervals, overzoom, explicit GDAL metadata | **Done** | Exact half-open `[min, max)` intervals from scales, tile ranges `floor/ceil−1`, overzoom policy option, source zoom range, explicit per-layer VRT zooms (no zoom-16 cap), archive inspection. |
| PR-06 | Marker/pattern renderer separation, deterministic atlas | **Mostly done** | Whole-symbol renderer, pre-rendered pattern cells, deterministic packer, centre-preserving crop, single rotation, true 2× rendering. *Missing:* explicit alpha-premultiplication tests. |
| PR-07 | LinePatternFill texture and materialized-hatch routes | **Partial** | Texture route: seamless cells solved for angle/spacing tolerance (0/45/90/17°…), analytic 1×/2× rendering, seam test, browser render. *Missing:* materialized-hatch route; map-unit spacing is frozen at the rule's first zoom (reported). |
| PR-08 | PointPatternFill and image/SVG patterns | Not started | Exported from a wrapped QGIS preview and reported as `Q2VT_PATTERN_APPROXIMATE`. |
| PR-09 | Feature-context properties, native circles, sprite variants | **Partial** | Static-vs-feature detection (feature functions/variables, geometry), `@map_scale` bound with `with_variable`, stable non-localized field names. *Missing:* native circles, per-feature sprite variants and budgets. |
| PR-10 | Exact marker-line positions | Not started | Vertex/first/last/segment placements are approximated and reported (`Q2VT_MARKER_PLACEMENT_APPROX`). |
| PR-11 | Label typography, glyph calibration, offsets | **Partial** | Text size units, offsets in ems, placement-specific properties, font-stack name shared with the glyph generator (fixes missing glyphs), `ő`/`ű` verified in the browser, unresolved fonts reported. *Missing:* glyph-metric calibration of the empirical 1.4 factor. |
| PR-12 | Render ordering, geometry hardening | **Partial** | Geometry generators evaluated in the layer CRS for both `@geometry` and `$geometry` (literal-safe substitution). *Missing:* render-order planner, source buffering by symbol reach, output geometry validation. |
| PR-13 | Pinned labels and callouts | Not started | |
| PR-14 | Arrows / hash lines / filled lines | Not started | Reported as unsupported. |
| PR-15 | Raster fallback / compositing groups | Not started | *Hybrid* mode is selectable but reports `Q2VT_HYBRID_NOT_AVAILABLE`. |
| PR-16 | Atomic publication, HTTP packaging, UI report | **Partial** | Cancellable `ogr2ogr` (argument list, terminated on cancel), XML-safe VRT, JSON/HTML report per export, strict failures remove only the new output directory. *Missing:* relative URLs, XYZ/PMTiles output, preview panel. |

## Behavior changes users may notice

* Scale breakpoints between integer zooms are now kept (fractional `minzoom`/`maxzoom`),
  and rules whose range lies between two integer zooms are no longer lost.
* ELSE rules now follow the scale ranges of their siblings as QGIS does.
* Horizontal/free polygon labels are centred instead of using variable anchors; only
  "around point" placements use variable anchors.
* Unsupported fills (gradient, shapeburst, …) are omitted and reported instead of being
  drawn black; failed sprites are omitted and reported instead of being transparent.
* Data-defined widths, sizes and opacities are converted to the browser's units.
* Line hatches (`LinePatternFill`) are visible and seamless; previously they were
  transparent.
