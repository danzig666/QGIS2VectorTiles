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
| MapLibre GL JS | 6.11.2 — the ES module build bundled in `resources/ml_viewer/` (`maplibre-gl.mjs`, `maplibre-gl-shared.mjs`, `maplibre-gl-worker.mjs`); 5.11.0 (`maplibre-gl.js`) before plugin 4.1.6 |
| Style validator | `@maplibre/maplibre-gl-style-spec` 26.4.4 (the dependency of maplibre-gl 6.11.2; was 24.3.1 with 5.11.0 before plugin 4.1.6) |
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
| 34 | Random marker fill drawn as a solid block | the texture was a cropped symbol preview with the map-unit density evaluated at preview scale | QGIS count (`ceil(count * area / densityArea)` or absolute) as seeded points; seamless texture at the QGIS density where dense; `test_random_points_follow_the_qgis_count`, `test_random_marker_fill_is_materialized` |
| 35 | Outline symbols on polygons started at the wrong vertex; holes rewound | `fixgeometries` (structure) rewrites *valid* polygons too (new start vertex, normalised ring orientation); QGIS draws marker intervals, dashes and offsets along the rings as stored | only invalid geometries are repaired; holed case of `test_map_unit_interval_markers_match_qgis` |
| 36 | Interval markers on holes continued the exterior's phase | rings of a polygon outline were one multi-line | one line per ring |
| 37 | Font markers beyond U+FFFF drawn as boxes; data-defined text cut off in sprites | MapLibre glyph ranges end at U+FFFF; sprite canvas sized without the feature's attributes | such markers become sprites (per distinct value); `test_font_markers_beyond_the_bmp_become_sprites`, `test_data_defined_font_marker_text_fits_the_sprite` |
| 38 | Dashes misplaced against markers drawn in their gaps | MapLibre restarts a dash array wherever a tile clips the line; Qt runs the pattern from each line or ring start | map-unit dashes exported as their dashes; `test_map_unit_dashes_match_qgis` |
| 39 | Data-defined sprite variants intermittently drawn with the static value | symbol clones share each property's cached expression state (about 40 % of runs evaluated the character to NULL) | sprites render from an XML copy of the symbol |
| 40 | Large map-unit font markers blobby | browser text is a 24 px signed distance field; an 80-280 m letter is drawn 3-12x larger | static map-unit font markers on points exported as their glyph outlines; `test_map_unit_font_markers_become_their_glyphs` |
| 41 | Cell-sized image patterns spill over polygon edges | point-pattern markers clipped to the shape were exported as whole sprites | such patterns stay clipped textures; `test_point_patterns_of_cell_sized_images_stay_textures` |
| 42 | Sprites aliased (thin outlines as dots, ragged circles) | sprites were drawn 3x (screen size) or up to 24x (map units) larger than displayed; the sprite atlas has no mipmaps | static screen-size sprites 1:1; map-unit sprites one image per zoom (1.5x); mixed markers keep their screen size; `test_map_unit_sprites_are_drawn_per_zoom`, `test_static_screen_sprites_are_drawn_one_to_one` |
| 43 | Font marker text too high; DejaVu drawn bold | text box centred instead of the QGIS baseline (half the ascent below the point); the default style of a family without a "Regular" face was the first style alphabetically | baseline offset; fonts resolved to the face Qt draws; `test_font_marker_text_sits_where_qgis_draws_it` |
| 44 | Sub-pixel lines too dark | MapLibre's line antialiasing inks a 0.2 px line with ~0.36 px, Qt with 0.2 px (measured, averaged over sub-pixel positions) | `line-opacity` compensates by width (zoom curve for map units); `test_thin_lines_get_the_ink_qgis_gives_them` (browser) |
| 45 | Offset point patterns drawn unshifted in textures (zig-zags became crosses) | the texture cell ignored the pattern offset | offset applied in the cell; `test_point_pattern_texture_applies_the_offset` |
| 46 | Random deviation of pattern markers ignored | markers stood on the exact grid | seeded uniform deviation per grid cell (QGIS's range, not its sequence); `test_point_pattern_random_deviation_stays_in_range` |
| 47 | All icons and patterns missing when the sprite sheet is too tall | the atlas stacked images in a 1024 px wide column (85 000 px tall with per-zoom sprites; 12 500 px at @2x before), above the GPU texture size | square shelf packing, a per-marker image budget, and an error when a sheet exceeds 16384 px; `test_atlas_stays_roughly_square` |
| 48 | Markers sized by map-unit extents drawn at a fixed screen size | an ellipse's width/height in map units with a nominal size in mm was classified by its size unit | the marker's scaling is measured (bounds at two map scales); `test_marker_sized_by_map_unit_extents_grows_with_the_map` |
| 49 | Label frames kept their integer-zoom size | MapLibre reads a size curve only at the stops covering [tile zoom, +1], so the one sawtooth `icon-size` stayed at 0.5; the padding was read at the tile zoom while the text was shaped at zoom + 1 | one style layer per zoom with its own icon-size ramp; padding doubled; `test_label_frames_follow_map_unit_text_between_zooms` (browser) |
| 50 | Scale-dependent marker intervals drawn by MapLibre | `CASE WHEN @map_scale > 3000 THEN 10 ELSE 3 END` intervals disabled exact placement | one rule per zoom with the value at its scale, placed exactly |
| 51 | Shape-clipped pattern markers not cut at the polygon edge | sprites cannot be clipped | closed simple markers exported as clipped polygons and outlines; `test_shape_clipped_marker_patterns_are_cut_at_the_edge` |
| 52 | Patterns of very large polygons silently missing | a per-feature cap (200 000 grid cells) returned nothing: a 25 km² polygon with a 10 m pattern lost it | polygons cut into pieces (anchored to the whole feature) before the grid is built; `test_very_large_polygon_keeps_its_pattern` |
| 53 | Clipped pattern line work empty on detailed polygons | one GEOS intersection of a whole feature's line work returned mixed collections or failed | clipped per piece (at most 100 x 100 cells, 256 vertices), only lines/polygons kept; `test_pattern_pieces_give_the_whole_feature_pattern` |
| 54 | Random fills slow on detailed polygons (6-8 s per 0.8 km²) | point-in-polygon test per candidate without a prepared geometry | QGIS's native random points in polygons; `test_large_detailed_layer_patterns_scale` |
| 55 | Dense random fills slow (25 s for 458 000 points) | every point went through the per-feature steps (fields, geometry expression, cleaning, single-part split) | points collected into one multipoint per polygon and kept as multipoints in the tiles: 5.5 s; `test_large_detailed_layer_patterns_scale` |
| 56 | Marker-line symbols tilted along straight edges (Műemléki környezet "MK") | the direction was averaged over +-`averageAngleLength` instead of +-half of it, and a 4 mm length was converted once for the whole zoom range (72 m instead of 15 m at z16) | averaged over the length centred on the marker, one dataset per zoom for screen lengths; `test_interval_marker_angle_averages_like_qgis`, `test_screen_averaged_marker_angles_are_per_zoom` |
| 57 | Screen-spaced marker lines with gaps (Tervezési terület corners, Tervezett fasor trees) | MapLibre's line placement drops a symbol that would overhang the end of a line piece: every tile edge and ring start | positions materialized per zoom (interval, offset along and line offset converted at the middle of each zoom); native placement only beyond the last tile zoom; `test_screen_interval_markers_are_placed_per_zoom` |
| 58 | Marker line missing (Vasúti fővonal white half-squares) | a 5.55e-17 offset (float noise in the style) built a zero offset curve and the rule failed silently | offsets below 1e-9 are zero; failed rules are reported as `Q2VT_RULE_EXPORT_FAILED`; `test_marker_line_with_float_noise_offset_is_exported` |
| 59 | Pattern textures 1.4x too sparse or large between zooms (Kis szaggatott) | MapLibre draws `fill-pattern` in the pixels of the tile's integer zoom, so a texture grows 2x with the map until the next zoom; map-unit textures were laid out for the middle of the zoom and screen-unit ones at their QGIS size | map-unit textures rendered at the integer zoom (exact at every zoom), screen-unit parts at 1/sqrt(2) (0.71x-1.41x instead of 1x-2x); `test_pattern_textures_keep_the_qgis_spacing_between_zooms` |
| 60 | Dense point-pattern textures with a grid of lighter seams (Építési hely Eger) | markers 2.1 px apart were pasted at whole pixels: gaps of 2 and 3 px | markers between pixels painted on a 4x cell and averaged down; `test_dense_off_grid_markers_have_no_seams` |
| 61 | Label wrap character shown as text (Változó területek "...*4.35 ha") | `wrapChar` was ignored | the label text breaks the line at every wrap character; `test_html_labels`, gallery |
| 62 | HTML label markup shown as text (Szerkezeti változás területtel "<sub>...</small>") | MapLibre has no markup | sections (text + scale) computed per feature and drawn with `format`: `<sub>`/`<sup>` at 2/3 like QGIS, `<small>`, `<br>`, entities; `test_html_labels` |
| 63 | Label halos twice as wide; percentage buffers drawn as pixels (white boxes) | QGIS strokes the outline with a pen as wide as the buffer (reaches half of it); `%` is of the text size | halo = half the buffer, percentages of the text size; `test_label_halo_is_half_the_qgis_buffer` |
| 64 | Multi-line labels always centred | the alignment was read from a non-existent attribute (`multiLineAlignment`) | `multilineAlign`; follow placement = `auto`; `test_single_line_label_placement` |
| 65 | Line labels repeated, on the line instead of above it, on the wrong segment (Méretvonal felirat 2, Útfelirat, Településfelirat) | placement flags and "no repeat" were ignored; MapLibre places per tile piece | above/below from the flags; a label without repeat distance is exported at the middle of the (longest part of the) line, rotated along it; `test_single_line_label_placement` |
| 66 | Map-unit line offsets crossing at sharp corners (Gyorsforgalmi út) | MapLibre `line-offset` | the offset line itself (mitred offset curve, as QGIS); `test_map_unit_line_offset_is_the_offset_line` |
| 67 | Wide pattern strokes overshooting the polygon edge in steps (Csíkozás) | a data-defined stroke colour sent the markers to sprites drawn whole | stroke colour carried to the line work; map-unit strokes exported as polygons, merged and clipped at the edge; `test_wide_map_unit_pattern_strokes_are_cut_at_the_edge` |
| 68 | Dimension styles showing "A" and arrows at the corners (Measure Meters, Polygon méretezés) | geometry-dependent properties of a generator's sub-symbol were evaluated on the source feature; screen offsets applied to the markers | line generators are applied first and their layers exported like line layers, properties evaluated per generated part; screen-unit marker-line offsets placed on the offset line per zoom; `test_generator_marker_text_is_evaluated_per_generated_part` |
| 69 | Dashes running over the marker text (Felszín alatti vízbázis védőidom) | a zero-length dash rejected the pattern; MapLibre's own dashes restart at tile edges | zero-length dashes merged into the gap (Qt draws nothing for them), the pattern exported as its dashes; `test_zero_length_dash_is_merged_into_the_gap` |
| 70 | Forests hidden under a nature reserve (showcase Land use, Marloth Nature Reserve) | a categorized layer without symbol levels: QGIS draws feature by feature, so forests 54 and 55, drawn after the reserve, cover it; the style drew one style layer per category, every reserve above every forest (3135 pixels of the forests' box off at z14; 202 with a prototype of the fix), and no diagnostic said so | feature-order strata: such features are drawn by copies of their rule's style layers filtered on `q2vt_orig_id` (`Q2VT_FEATURE_ORDER_ACROSS_RULES` beyond the limits); `test_render_order.py` (`test_categorized_overlap_keeps_feature_order`, `test_simplified_neighbours_are_not_lifted`), `test_overlapping_features_of_different_rules_keep_qgis_order` (browser, 10 % of the pixels off before) |

### Resolved suspicions

* `_MAPLIBRE_LABELS_FACTOR` (1.4) came from wrong glyph metrics; with calibrated glyphs
  (24 px em, bearings) the factor is 1.0.
* The zoom ↔ scale convention now uses the 96-DPI Web Mercator resolution divided by the
  Mercator scale of the project map units (≈1.48 for EOV in Hungary).

### Untested suspicions (not yet reproduced)

* Label `yOffset` sign relative to MapLibre `text-offset` (kept unchanged).
* `TilesStyler.get_label_priority` uses a numeric property key (87) that may differ
  between QGIS versions.
