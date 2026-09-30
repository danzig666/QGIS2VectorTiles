# Baseline record (PR-01)

## Commits

| | Commit | Date |
|---|---|---|
| Upstream baseline (plan) | `GallPeters/QGIS2VectorTiles@637e960d1217b8753e0ccabeb662209f442a223e` | 2026-08-06 |
| This fork before the fidelity work | `637e960d1217b8753e0ccabeb662209f442a223e` (identical to the baseline) | 2026-08-06 |

The fork had no changes relative to the upstream baseline, so no reconciliation was needed.

## Environment used for the recorded results

| Component | Version |
|---|---|
| OS | Ubuntu 24.04.4 LTS (container), locale C.UTF-8 |
| QGIS | 3.34.4-Prizren (Ubuntu `python3-qgis`), Qt 5.15.13, Python 3.12 |
| GDAL | 3.8.4 (MVT + MBTiles drivers) |
| Pillow / NumPy / SciPy | 10.2.0 / 1.26.4 / 1.11.4 |
| Browser | Chromium build 1194 (Playwright 1.56.1), SwiftShader WebGL, device-pixel ratio 1 |
| MapLibre GL JS | 5.11.0 — the build bundled in `resources/ml_viewer/maplibre-gl.js` |
| Style validator | `@maplibre/maplibre-gl-style-spec` 24.3.1 (the dependency floor of maplibre-gl 5.11.0) |
| Node | 22.22.2 |
| Fonts | System DejaVu/Free fonts (fontconfig); the default QGIS label font resolves to *DejaVu Sans Book* |

**QGIS version caveat.** `metadata.txt` declares QGIS 3.44 as the minimum, but only
QGIS 3.34 was available in the recording environment (the qgis.org package repositories
were not reachable). All PyQGIS results below were produced on 3.34; the code uses
version adapters for the API differences found (enum `.value`, `QgsSymbol` property
names, marker-line placement flags). The suite must be re-run on 3.44 and on the QGIS 4.x
build intended for the fork before those versions are advertised — see
[TESTING.md](TESTING.md).

## Confirmed defects (reproduced on the unchanged baseline)

Each item was reproduced with PyQGIS against the baseline code before it was changed and
now has a regression test.

| # | Defect | Reproduction on the baseline | Regression test |
|---|---|---|---|
| 1 | Falsy data-defined values dropped | `QgsProperty.fromExpression('0')` → static fallback `5` returned instead of `0` (`if evaluation:`) | `test_units_and_properties.py::test_falsy_numeric_literals_are_preserved` |
| 2 | Data-defined icon size crashes | `get_icon_size` → `TypeError: unsupported operand type(s) for /: 'list' and 'int'` | `test_data_defined_icon_size_is_a_valid_expression` |
| 3 | Pattern fills exported as transparent 10×10 images | `SymbolImage(QgsLinePatternFillSymbolLayer())` → `AttributeError` swallowed, transparent image | `test_sprites.py::test_symbol_layer_is_rejected_with_actionable_error`, `test_generator_reports_wrong_input_instead_of_transparent_image`, `test_end_to_end.py::test_vector_first_export` |
| 4 | Rendering exceptions hidden | any `RuntimeError`/`AttributeError` in `SymbolImage` replaced by a transparent image | `test_render_exception_is_not_replaced_by_transparent_image` |
| 5 | Marker-line placement misread | `placement()` returns flag values: *LastVertex* (4) became `line-center`, *CentralPoint* (16) became repeated `line` | `test_marker_line_placement_uses_named_flags` |
| 6 | Data-defined opacity ignored | `QgsSymbolLayer.Property.Property.PropertyOpacity` raises, caught → static opacity; values are also 0–100, not 0–1 | `test_data_defined_opacity_is_percent` |
| 7 | Data-defined widths not unit-converted | DDP stroke width in mm emitted as raw `["get", field]` pixels | `test_data_defined_width_is_converted_from_its_unit` |
| 8 | Map units treated as millimetres | `convert_length_to_pixels(10, MapUnits)` → `37.8` | `test_line_width_units`, `tests/unit/test_units.py` |
| 9 | Static DDP stored as a quoted string | `'3'` → icon size string, then `str / int` crash | `test_quoted_static_number_becomes_a_number` |
| 10 | Rule between two integer zooms never visible | scale range 1:z3.2–1:z3.8 → `o=4`, `i=3` (inverted) | `tests/unit/test_zoom.py::test_fractional_single_zoom_rule_is_nonempty`, `test_flattener.py::test_single_zoom_rule_has_nonempty_interval` |
| 11 | Export modifies the user's project | after flattening, a categorized layer's renderer was `QgsRuleBasedRenderer` and its ELSE rule read `NOT (("zone"='K1')) IS 1` in the user's project (`layer.setRenderer(rule_system)`) | `test_flattener.py::test_flattening_does_not_mutate_project` |
| 12 | ELSE ignores sibling scale ranges | ELSE excluded a sibling's features even at scales where the sibling is hidden | `test_else_respects_sibling_scale_ranges_and_disabled_siblings` |
| 13 | Per-layer zoom cap of 16 | VRT wrote `MAXZOOM=min(max_zoom, 16)` | `test_end_to_end.py::test_zooms_above_16_are_generated` (archive inspected) |
| 14 | Unsupported fills drawn black | gradient/shapeburst produced a `fill` layer with no paint (MapLibre default black) | `test_unsupported_fill_is_reported_not_drawn_black` |
| 15 | Label glyphs missing | `text-font` = `"{family} {styleName}"` (e.g. `"DejaVu LGC Sans "`) never matched the glyph directory (`"… Regular"`/resolved family) | `test_vector_first_export` (glyph files for the exact `text-font`, incl. `ő`/`ű`) |
| 16 | `round_numeric_values` converted numeric strings | `["get", "2020"]` → `["get", 2020]`; small factors rounded to 0 | `test_round_numeric_values_keeps_strings` |
| 17 | Geometry generators: `$geometry` not handled | only `@geometry` was rewritten; the rewrite also touched string literals | `test_geometry_generator_uses_layer_crs_for_both_geometry_spellings` |
| 18 | QGIS < 3.44 crash in outline split | `key.value` on sip enums (`AttributeError`) | covered by all flattener tests on 3.34 |
| 19 | Python < 3.12 syntax error | PEP 701 nested quotes in an f-string in `rules_flattener.py` | `pyflakes` under Python 3.11 |
| 20 | Sprite anchors / rotation | tight cropping moved `icon-anchor: center` off the symbol origin; marker angle was baked into the sprite *and* emitted as `icon-rotate` | `test_offset_marker_keeps_origin_at_image_centre`, `test_rotation_is_not_baked_when_style_rotates` |
| 21 | @2x sprites upscaled | the @2x sheet was a LANCZOS upscale of the 1x sheet | `test_2x_sheet_is_rendered_at_double_resolution` |

### Verified non-defects

* **Overzoom above the archive.** The legacy style declared no source zoom range, so
  MapLibre requested tiles above the archive maximum (answered with HTTP 204). MapLibre
  5.11 still rendered the parent tiles at zoom 15.5 over a zoom-14 archive (checked in the
  browser), so this was *not* a visible defect. The source now declares its range anyway,
  which avoids those requests and makes the overzoom policy explicit.

* **Line offset sign.** QGIS and MapLibre both offset positive values to the right of the
  line direction (checked by rendering in QGIS; `test_positive_line_offset_is_right_of_direction_like_maplibre`).
* **ELSE with NULL values.** The `NOT (…) IS 1` form correctly matches features whose
  sibling filters evaluate to NULL.

### Found later (QGIS-vs-browser gallery on real styles)

| # | Defect | Evidence | Fix / test |
|---|---|---|---|
| 22 | Per-layer tile zooms ignored | VRT `LayerCreationOption` is not an OGR VRT element; every dataset was written at every zoom | MVT `CONF`; `test_zoom_split_datasets_only_fill_their_own_tiles` |
| 23 | Rule draw order inverted | QGIS draws a feature's rules in tree order (later on top) and honours rendering passes | `test_later_rules_draw_on_top`, `test_rendering_pass_orders_symbol_layers` |
| 24 | Polygon outline offsets on the wrong side | QGIS buffers each ring (`offsetLine`); MapLibre offsets by ring direction | `test_polygon_outline_offsets_follow_the_source_ring` (browser) |
| 25 | Zero-width lines invisible | QGIS draws a cosmetic one-pixel pen | `test_line_width_units` |
| 26 | Interval markers misplaced | MapLibre starts its own way and has no offset along the line; QGIS places markers at offset + k * interval (`renderPolylineInterval`) | `test_map_unit_interval_markers_match_qgis` |
| 27 | Centroid-fill markers misplaced | QGIS uses the exterior-ring centroid, point-on-surface only when needed | `test_centroid_fill_position_matches_qgis` |
| 28 | Nested geometry generators dropped | inner generators evaluated on mirrored painter geometry, outputs coerced to the sub-symbol type | `test_nested_geometry_generators_match_qgis` |
| 29 | Ring filter ignored | exterior-only / interior-only outlines drew every ring | `test_ring_filters_match_qgis` |
| 30 | SVG fill stroke missing; empty SVG drawn | the stroke sub-symbol was never exported; an SVG fill without SVG data draws nothing in QGIS | `test_svg_fill_without_svg_draws_only_its_stroke` |
| 31 | Stroke-only point patterns (Line, Cross, Cross2, ArrowHead markers) drawn as tiny sprites | QGIS renders a texture of stroked marker paths clipped per its clip mode | clipped line geometry on the pattern grid; `test_stroke_marker_patterns_match_qgis` |
| 32 | Dash patterns wrong for pen styles and square caps | Qt pen presets (Dash 4/2, Dot 1/2, …) were not exported; Qt extends every dash by square caps, MapLibre only by round caps | Qt presets, square-cap dashes lengthened; `test_dash_patterns_follow_qt` (browser) |
| 33 | Map-unit line labels missing | MapLibre checks that a line label fits along the line with the text size at zoom 18; map-unit text is 4x its z16 size there | one style layer per zoom below 18 with the size curve clamped to that zoom; labels that may overlap in QGIS (`displayAll`) allow overlap; `test_map_unit_line_labels_are_drawn` (browser) |

### Resolved suspicions

* `_MAPLIBRE_LABELS_FACTOR` (1.4) came from wrong glyph metrics; with calibrated glyphs
  (24 px em, bearings) the factor is 1.0.
* The zoom ↔ scale convention now uses the 96-DPI Web Mercator resolution divided by the
  Mercator scale of the project map units (≈1.48 for EOV in Hungary).

### Untested suspicions (not yet reproduced)

* Label `yOffset` sign relative to MapLibre `text-offset` (kept unchanged).
* `TilesStyler.get_label_priority` uses a numeric property key (87) that may differ
  between QGIS versions.
