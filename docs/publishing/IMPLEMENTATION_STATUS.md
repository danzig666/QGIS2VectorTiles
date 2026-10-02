# Web publishing — implementation status

Status of each ticket of *QGIS2VectorTiles Fork: Vector-Tile Web Publishing* (plan dated
1 October 2026) in this fork. "Done" means implemented **and** covered by executed tests;
anything else is stated explicitly. Skipped or unavailable environments are limitations,
not passes. Baseline: [BASELINE.md](BASELINE.md).

Test environment for every result below: QGIS 3.34.4, GDAL 3.8.4, Python 3.12.3,
Node 22.22.2, Chromium 1194 (Playwright 1.56.1), Linux. Not available: QGIS 3.44/4.x,
Windows, Firefox/Safari, a real R2 bucket.

| Ticket | Status | Behaviour | Tests (executed) | Limits / next |
|---|---|---|---|---|
| PUB-01 Baseline, vector-only guard | **Done** | `BASELINE.md` (HEAD, resources, 292-test baseline). `publishing.validation`: vector-only style contract (raster / raster-dem / image / video / canvas / persisted GeoJSON refused; sprites, patterns, glyphs allowed; runtime helper GeoJSON only at runtime), visible-polygon helper datasets counted as required, safe relative paths, forbidden private/source files, leak scan (absolute paths, secrets, canaries incl. decoded MVT). Independent MVT decoder `publishing.mvt`. | `pytest tests/unit/test_publishing_validation.py` → 27 passed | — |
| PUB-05 PMTiles builder | **Done** | `publishing.pmtiles_builder.build_pmtiles`: read-only MBTiles preflight (format, relation, address range, duplicates, MVT decode), on-disk tile-id index, official writer (vendored pmtiles 3.8.1) with SHA-256 dedupe, gzip normalisation (deterministic, `mtime=0`), complete `vector_layers` (GDAL truncates them), bounds/center repair, cancellation with cleanup, validation before atomic rename, never overwrites. `validate_pmtiles`, exhaustive `compare_archives`. | `Q2VT_PMTILES_CLI=… pytest tests/unit/test_publishing_pmtiles.py` → 18 passed (incl. official go-pmtiles 1.28.0 `verify`/`show`/`tile`). Owner project (4,604 tiles, 264 layers): exhaustive compare + go-pmtiles verify passed, 14 s. | Peak memory not yet profiled on a >1 GB archive. |
| PUB-02 Profile / schema / result contracts | **Done** | `publishing.models` (PublicationProfile with every Publish-window setting, ExportBundle, ReleaseState), `publishing.profile` (load + migration hook, validation with all problems listed, secrets rejected — only a credential *reference* is stored, stable JSON, disclosure fingerprint: changed exposed fields/layers/destination require a new review), JSON Schemas `schemas/publishing/{profile,manifest,release,current,search}-v1.schema.json`. Exporter hooks (defaults unchanged): `layer_ids` (selected layers, hidden ones included, tree untouched), `archive_format` mbtiles/pmtiles/both, `add_result_layer`, `export_bundle()`. Processing: optional `TILE_ARCHIVE_FORMAT` (default MBTiles) and declared outputs `OUTPUT_FOLDER, MBTILES, PMTILES, STYLE, REPORT`. | `pytest tests/unit/test_publishing_profile.py` → 18 passed (incl. jsonschema 4.10.3 agreement); `pytest tests/integration/test_publishing_export.py` → 4 passed (hidden selected layer exported, tree/visibility and layer count unchanged, PMTiles==MBTiles, old parameter sets unchanged). | PMTiles-only Processing exports add no QGIS result layer (QGIS 3.34 cannot open PMTiles). |
| PUB-06 Portable bundle, safe local releases | **Done** | `publishing.web_builder.build_release`: release assembled in `releases/.staging-<id>`, validated (vector-only style, allowlisted files only, safe paths, glyphs/sprite present, PMTiles structure, transport-only style diff `bundle.style_semantic_diff`, leak/canary scan) and renamed to `releases/<id>`; `release.json` inventory (size, SHA-256, content type, cache policy); stable `index.html` + content-hashed `bootstrap.*.mjs`; `current.json` replaced atomically (fsync + `os.replace`), optional expected-release conflict check; rollback = re-activation; retention never deletes the current release; publication lock; crash recovery of staging/temp files; offline ZIP with the release at its root. Raster basemaps (legacy OSM / Blue Marble) are removed from published styles. Legacy `core/publisher.py` keeps its API, shares `portable_style`/`write_xyz_tiles`, and swaps `web/` with a recoverable backup instead of delete-then-rename. | `pytest tests/unit/test_publishing_web_builder.py` → 11 passed (failure injection: canary/validation failure and cancellation leave the previous release current and no leftovers; conflict, rollback, retention, recovery, ZIP, schema agreement for manifest/release/current). `tests/browser/test_static_package.py` → 1 passed (legacy package). | Windows path-length and locked-file behaviour not tested here (no Windows). |
| PUB-07 PMTiles viewer + range preview | **Done** | `resources/web_viewer/`: `index.html` (meta-CSP, no inline script), `app.mjs` (manifest validation: schema 1, vector-only, MVT, safe paths; WebGL check; transport binding; map; visible-polygon helper with private-field assertion; controls hook), `transport.mjs` (official pmtiles 4.5.0 browser build vendored unmodified, protocol registered once, archive header checked for MVT, `pmtiles://` absolute URL bound from the manifest, `{placeholder}`-safe URL resolution), `diagnostics.mjs` (distinct error states), `i18n.mjs` + en/hu locales, `styles.css` (responsive, print). `bootstrap.mjs` routes the stable entry to `current.json`'s release keeping query/hash. `visible_labels.mjs` (single maintained copy) gained `setEligibility/pause/resume/destroy`, skips identical `setData` (ended the idle→setData→idle loop) and reports missing MapLibre private fields. `publishing.preview_server`: loopback-only, single ranges (206/416, open/suffix), HEAD, MIME table, no outer encoding, traversal/symlink protection, request log. | `pytest tests/unit/test_publishing_preview_server.py` → 17 passed. `pytest tests/browser/test_pmtiles_transport.py` → 5 passed: XYZ vs PMTiles of the same QGIS export (map-unit hatch + outline, visible-polygon labels) pixel-identical within 0.2 % at zooms 10.6, 12, 13.5, 14, 15.4 (overzoom), same label counts; cold view of a 21,845-tile archive reads < 25 % via 206 ranges only; stable entry through `nested dir/ő …` keeps `?query#hash`, no third-party requests, no CSP violations; server without ranges → `Q2VT_PUB_RANGE_UNSUPPORTED`; schema 7 manifest and raster style refused. Owner project: 8 range requests / 199 KB of a 21 MB archive for the start view. | Firefox/Safari not tested. |
| PUB-03 Provenance and stable identity | **Done** | `FlattenedRule.provenance` (`publishing.provenance.RuleProvenance`) captured by the flattener before cloning/conversion: original legend key (rule-based keys kept; categorized/graduated/single mapped to their stable legend items in order), label, parent keys, ELSE origin, callout leaders; copied by `derive()` and every construction. `publishing.qgis_model.logical_model`: groups from the layer tree, layers from the profile, rules from the original renderers' legend items, components mapped from compiled style layers (per-zoom splits included) with roles geometry/decoration/label/callout and visible-polygon helper dependencies. `q2vt_feature_key` (string) computed on the *source* layer in Phase 1 (`identifiers.key_expression`: single field text, typed length-prefixed compound keys, export-scoped `e<FID>` fallback) and carried through every dataset; uniqueness/NULL validation stops publication (`Q2VT_PUB_IDENTITY`). | `pytest tests/integration/test_publishing_pipeline.py` → 4 passed (group/hidden layer/category rules/components, `00123/4`, `0099`, 20-digit keys exact in decoded tiles, export-scoped keys = provider FIDs, duplicate keys refused, project tree unchanged); `tests/unit/test_publishing_indexes.py::test_keys`. Existing `test_flattener.py`, `test_end_to_end.py` still pass. | Rule-based *labeling* rules are exposed per layer (labels switch), not as individual legend rules. |
| PUB-04 Disclosure and membership | **Done** | Publish exports required fields only (+ approved filter fields + `q2vt_*` values) unless the profile explicitly includes all fields. `disclosure.assert_disclosure` decodes every tile and fails on any property that is neither generated nor approved (`Q2VT_PUB_FIELD_DISCLOSURE`), run before the release is renamed into place. Popup attributes live in feature-lookup shards, not in tiles. Membership = features in the export extent matched by at least one exported rule (union of flattened rule filters), deduplicated before cartographic multiplication. Canary scan of every public file. | Pipeline tests: canary field value absent from decoded tiles and every published file; an unapproved field in tiles refuses the release; NULL-category feature not drawn/labelled by QGIS is not published anywhere. | Credentials scan uses known values only (cloud stage). |
| PUB-10 Legend | **Done** | `qgis_model.render_swatches`: QGIS renders the original legend symbols (`QgsSymbolLayerUtils.symbolPreviewPixmap`) as PNG UI assets (`legend/rule-*.png`); rules carry scale ranges; `legend.mjs` shows what is switched on, zoom-aware (out-of-scale rules marked), used for print. | Pipeline test checks a swatch per category; browser tests render the legend. | Proportional-symbol samples use QGIS's own legend items; no invented classes. |
| PUB-11 Search and feature lookup | **Done** | Records of published features (key, label from display expression or search field, terms, inside anchor `pointOnSurface`/line midpoint, WGS84 bounds, approved popup/filter values; no geometry). `search_index`: Unicode/diacritic-insensitive normalization (Python = JS twin, same vectors), `single` file under the budget (10 MiB / 50k records) else deterministic prefix shards (length grows for skew; oversized buckets split into parts), coverage per layer, no truncation; Web Worker (`search_worker.mjs`) loads lazily; ranking exact → prefix → word prefix (→ substring in single mode); most-selective-word shard choice; "type more characters" instead of silent caps. `feature_index`: FNV-1a hash shards for exact (layer, key) lookup independent of the search UI. | `pytest tests/unit/test_publishing_indexes.py` → 6 passed (120,000 records, 90 % skewed prefix, every record reachable, shard size bound, JS shard selection and ranking, Hungarian vectors identical in Python/JS); browser: offscreen search result opens its popup, 20-digit key kept as text. | — |
| PUB-08 Layer/group/rule controls, state | **Done** | `state.mjs` (defaults from the manifest < saved preferences per publication < URL; reset to published defaults), `style_control.mjs` (visibility from layer/group/rule/labels state with OR over owning rules, attribute filters composed with the original filter, opacity multipliers on the original paint values with zoom curves kept top-level and exact restore at 1, helper loader hidden with its labels, eligibility predicate for the label helper), `layer_controls.mjs` (tri-state groups, rules with swatches, out-of-scale shown apart from the checkbox, opacity sliders, labels switch, reset). | `pytest tests/browser/test_web_viewer_features.py` → 9 passed: rule toggle hides only its category, labels switch hides only labels, group toggle hides all and unloads helper polygons, opacity 0.5/0.3/0.8 never nested and 1 restores the original, labels follow toggles and filters with no orphans. | — |
| PUB-09 Identify, popups, highlight | **Done** | `identify.mjs`: queries component style layers only, one record per (layer, key) despite fills/outlines/labels/tile copies, chooser for overlaps, popup values via `textContent` (strings, numbers in locale format, booleans, NULL vs empty, safe http(s)/mailto links with noopener), hidden-layer/filtered notes, export-scoped link note; selection and hover overlays filtered by the key on the original geometry dataset (never hatch elements); touch devices get no hover. | Browser: click on a parcel → one popup with approved fields; `<script>` shown as text, nothing executed (A15). | Hollow-polygon interior picking relies on fill layers (opacity 0 still hit); no extra interaction tiles yet. |
| PUB-12 Filters and URL state | **Done** | `filters.mjs` (values with published counts and NULL option, numeric ranges, text contains; domains from the whole publication), `permalink.mjs` (versioned hash: camera, layer/group/rule diffs, labels, opacity, filters, selection; strict parsing and size limits; stable vs versioned share links), cold deep links through the feature index even with search off; missing keys show a message. | Browser: cold link to an offscreen parcel opens it; unknown key → Hungarian "not found" message; permalink round trip restores rule/labels/opacity/zoom; malformed URL state ignored safely. | — |
| PUB-17 (part) Tools | **Partly done** | `tools.mjs` + `geo.mjs`: WGS84 readout; EOV readout with PROJ's default operation (3-parameter HD72 shift + somerc, labelled approximate, null outside Hungary); distance (Vincenty) and area (authalic sphere) measurement on temporary in-memory geometry, Escape/Enter, no popups while measuring; print layout with legend, title, date, attribution. | EOV equals pyproj at 5 reference points (< 1 µm), distance equals pyproj geodesic (0.2 mm over 115 km), area within 1 ppm; browser measurement test. | Vector basemaps not implemented (blank background only). |
| PUB-13 Publish window, saved settings, credentials | **Done** | Web menu / Web toolbar action "Publish Web Map…" (`src/gui/publish_dialog.py`): tabs Map (title, slug, description, language, attribution, logo, zooms, extent, layer tree with *Publish* separate from *Visible at start*), Interaction (viewer features; per layer: title, display expression, opacity, legend, deep links, field table for popup/alias/type/search/key/filter), Output (PMTiles / both / MBTiles, folder, XYZ package, ZIP, CPU, fidelity, overzoom, polygon labels, all-fields warning), Destination (local / R2 / S3, account, endpoint, bucket, prefix, public URL, QGIS auth configuration or session-only keys, retention, conditional writes, test connection, CORS policy text, releases/rollback), Review (exposed fields per layer, target URL, costs, approval). Every non-secret setting is saved in the project (`publication_profiles.py`, scope `QGIS2VectorTilesFork`; several profiles, copied-project prompt: update same map or new map). Keys: QGIS Authentication Manager (`credentials.py`), never in the project, Processing history or logs. Export runs on the main thread (NoThreading kept); upload in a `QgsTask`. Preview servers stop on plugin unload. | `pytest tests/integration/test_publish_dialog.py` → 4 passed (defaults from the tree, settings written to and read back from the .qgz, no secrets in the project, project tree unchanged by export, preview served, local publish, R2 publish gated by the review and completed in a background task). | Not tested in the QGIS desktop GUI on Windows/3.44 here (offscreen Qt only). |
| PUB-14 S3/R2 upload | **Done** | `providers/s3.py` (boto3/botocore 1.43.106 vendored, S3 data only, licences included, used only when QGIS's Python lacks boto3, appended to the end of `sys.path`; explicit credentials, TLS verified; prefix containment; immutable objects with `If-None-Match: *`; multipart with a private resumable journal; bounded retries with jitter for 429/5xx/timeouts, none for credential errors; secrets redacted from errors), `providers/r2.py` (account endpoint, region `auto`, checksum mode for R2), `providers/local.py`. Uploads consume the release.json inventory only (folder changes after validation are refused). | `pytest tests/unit/test_publishing_providers.py` → 13 passed (fake client + range server as public domain); `Q2VT_MOTO_PATH=… Q2VT_MOTO_PYTHON=python3.11 pytest tests/unit/test_publishing_s3_moto.py` → 4 passed (real SigV4, conditional writes, 11 MiB multipart, listing/deletion, full publish verified over HTTP; ambient AWS env keys ignored). | No live R2 test: no authorised sandbox bucket in this environment (needs owner credentials and approval). |
| PUB-15 Public probes, activation, rollback, retention | **Done** | `public_verify.py` (MIME of page/modules/style, manifest identity, archive HEAD size, no outer encoding, 206 ranges at start/middle/end equal to the local bytes, 200-for-range aborted without a full download, CORS with the viewer origin when it differs, glyph URL with spaces, one tile read through the remote PMTiles directory and decoded); `deployments.py` (pointer read with ETag before upload, foreign-publication prefix refused, credential leak scan, upload, stable entry, verification, conditional activation with read-back reconciliation of lost responses, CONFLICT keeps the release, activation check of the public pointer, rollback without re-upload, retention plan + explicit deletion never touching the current release, abort of interrupted uploads); history dialog. | Provider tests above: failed range check → `UPLOADED_NOT_ACTIVE` and previous pointer kept (A18); stale ETag → conflict, newer pointer kept (A19); rollback without uploads (A20); lost activation response reconciled. | CDN caching of `current.json` on a real custom domain not verifiable here; reported as a warning when stale. |
| PUB-16 Release integration, diagnostics, ZIP, docs | **Done (local); see limits** | Plugin ZIP (`tools/build_release.py`, new `--out`) ships the publishing package, the vendored PMTiles writer and S3 SDK with licences, the web viewer and the visible-polygon helper. Normal use needs no shell commands or `pip`. Dependency/licence inventory: `src/publishing/vendor/VENDOR.md`. Each release carries `public-diagnostics.json` (counts and codes only); raw fidelity reports stay in the private work folder. Docs: `ARCHITECTURE.md`, `HOSTING.md` (R2 one-time setup, other S3, static servers, troubleshooting codes), `SECURITY.md`, `TESTING.md`, README section, release notes 4.2.0. Viewer pages declare an empty icon (no `/favicon.ico` 404). | `pytest tests/integration/test_plugin_package.py` → 1 passed: zip built, unpacked into an empty plugin folder and loaded in a fresh QGIS Python (`classFactory` → `initGui` → Web menu/toolbar action → Publish window opens → vendored boto3 loads → `unload` removes both); no tests, project files or archives in the zip. **Owner project** (`arlowebtest.qgs`, 153 datasets, z13–17, popup/search fields `hrsz`/`NEV`) through `controller.export_local`: 164 s, 4,585 tiles, 16.5 MB PMTiles, 15,889 searchable features, 128 files / 21.9 MB, release validated (vector-only, disclosure, inventory, leak scan). Opened through the preview server in Chromium: ready, 0 errors, 0 third-party requests, search for `12` lists parcels and buildings, desktop and phone layouts render; start view read 1.7 MB in 14 range requests. Full regression: see *Regression* below. | Clean install verified on QGIS 3.34 (Linux) only; metadata says 3.44–4.99, which was not available here. Windows not verified. |
| PUB-17 Vector basemaps | **Done (4.3.0); live source not verified** | See *4.3.0 additions*. | See below. | The Protomaps build server was not reachable from the test environment. |
| PUB-18 Measured optimization, provider extensions | **Not started** | — | — | Every publication re-exports and re-uploads the archive (files with the same SHA-256 are skipped on upload). No viewer-only or style-only reuse, and no GitHub Pages provider. |

## Milestones (plan §18.1)

| Milestone | Tickets | State |
|---|---|---|
| A: local PMTiles transport with symbology and helper parity | PUB-01/02/05/06/07 | **Implemented and tested** (pixel parity XYZ vs PMTiles, range-only reads, owner project). |
| B: interactive vector viewer and public data model | PUB-03/04/08–12 | **Implemented and tested** in Chromium; Firefox/Safari not tested. |
| C: one-click R2 publication and installable release | PUB-13–16 | **Implemented. Live R2 not verified.** Upload, public verification, conditional activation, rollback and retention were tested against a fault-injecting fake S3 client and a moto S3 server with real SigV4. They were never run against Cloudflare R2, because no sandbox bucket or prefix was authorised for this work. The plan's definition of done ("publish … to an already configured R2 destination") is therefore **not yet demonstrated**. |
| D: planning tools and optimizations | PUB-17/18 | **Partly**: coordinates, measuring, print and vector basemaps (4.3.0) are done; PUB-18 is not started. |

To close Milestone C:
1. Publish once to an authorised sandbox prefix on the owner's R2 bucket (see `HOSTING.md`).
2. Check the result in a second browser.
3. Repeat the clean install on QGIS 3.44 or later and on Windows.

## Regression (4.2.0)

| Run | Result |
|---|---|
| Baseline before publishing work (`BASELINE.md`) | 292 passed, 0 skipped |
| `pytest` (full suite, after PUB-16) | **429 passed, 5 skipped**, 0 failed, 7 min 49 s |
| The 5 skips are opt-in tools, run separately: `Q2VT_PMTILES_CLI=… Q2VT_MOTO_PATH=… Q2VT_MOTO_PYTHON=python3.11 pytest tests/unit/test_publishing_pmtiles.py tests/unit/test_publishing_s3_moto.py` | 22 passed (go-pmtiles 1.28.0 `verify`, moto S3 server) |

A first full run failed 6 browser tests: a tile server leaked by the Processing test held
port 9000. The test now stops the server from launching, and the result above is from the
second run.


## 4.3.0 additions (owner request of 1 Oct 2026)

Owner decision: QGIS **raster layers** are published as image tiles in separate archives. This
extends the vector-only rule. It does not replace it: vector layers are never rasterized, and
the only raster sources a release may contain are those archives.

| Item | Status | Behaviour | Tests (executed) | Limits |
|---|---|---|---|---|
| Raster layers | **Done** | `raster_tiles.py`: QGIS renders each raster layer (metatiles of 8×8, `RenderMapTile`, its own renderer/opacity/scale range) into PNG / WebP / JPEG tiles. Transparent tiles are skipped and identical tiles stored once. Each layer gets `data/raster-<id>.pmtiles`, validated by image signature. Per-layer settings: zooms, quality, 512 px HiDPI. Size is estimated before rendering (400 000-tile limit). A layer that draws nothing in the extent is dropped with a warning. The style layer sits at the layer's tree position, under all labels. The contract (`vector_only_violations(raster_sources=…)`, viewer `bindStyle`) accepts raster sources only for these manifest archives. | `pytest tests/integration/test_publishing_raster.py` → 3 passed (own WebP archive, every tile checked, z-order under parcels; above vectors but under labels; transparent tiles skipped; empty raster dropped; oversized refused). | Blending modes are not reproduced. Online services (WMS/XYZ) are flagged for licence review. |
| Vector basemap (PUB-17) | **Done; live source not verified** | `basemap.py` discovers the latest build (`build-metadata.protomaps.dev/builds.json`) or uses a given URL or file. It reads directories and tiles with HTTP ranges (retries; whole-file answers are refused), and only the area's tiles are copied: zooms 0..overview over a wide square, detail zooms over the padded extent. The extract is `data/basemap.pmtiles`. Flavors come from the vendored `@protomaps/basemaps` 5.7.2 (hu/en × 5): sprite icons dropped, Noto stacks replaced by glyphs generated for the characters actually in the extract's labels. The viewer adds the chosen flavor under every project layer and hides the project background while one is shown. | `pytest tests/unit/test_publishing_basemap.py` → 15 passed (local extract = the area's tiles with unchanged payloads; HTTP extract with 206 ranges only, fewer bytes than the file; retries; 200-for-range refused; build discovery; wrong schema / too many tiles refused; 10 flavor files vector-only and sprite-free). `tests/integration/test_publishing_basemap_themes.py` → 1 passed (bundled extract, flavor files, glyph folders, manifest schema). | `build.protomaps.com` was not reachable from this environment (proxy policy), so a real planet extract has **not** been run. POI icons and road shields are drawn as text only. |
| Map themes | **Done** | Dialog: *Publish its layers* and *Use as start view* from a QGIS map theme. Chosen themes become viewer presets (QGIS's effective layer visibility plus checked legend items); one can be the start view. Locked layers stay on. | `test_publishing_basemap_themes.py`, `test_publish_dialog_layers.py`, browser test below. | Themes with a non-current layer style use the current style (warning). |
| Locked layers/groups, bulk settings | **Done** | Profile `toggleable` per layer and group; *Can be switched off* column; group Publish boxes tri-state; *Selected layers* menu and right-click menu apply to many rows, and a selected group applies to its contents. The viewer shows a lock, and presets, saved state and links cannot switch locked items off. | `pytest tests/integration/test_publish_dialog_layers.py` → 3 passed; `tests/unit/test_publishing_profile.py` → 30 passed. | — |
| Viewer redesign | **Done** | Floating glass header and panel with icon tabs (desktop); top bar plus bottom sheet (phones); switches, opacity drawers, checklist filters, card popups and legend; basemap switcher with previews; theme chips; plain-text attribution; light/dark switch; accent colour; locate button on https. | `pytest tests/browser/test_web_viewer_features.py tests/browser/test_pmtiles_transport.py` → 14 passed (unchanged behaviour); `tests/browser/test_web_viewer_basemap_raster.py` → 5 passed (basemap under the map, flavor switch, none restores the background, raster drawn, presets, locks vs links, no third-party requests, no overflow at 1366/390/360 px, dark switch). Screenshots reviewed (desktop, phone, dark, basemap menu, popup, search). | Firefox/Safari not tested. |

### Regression (4.3.0)

| Run | Result |
|---|---|
| `pytest` (full suite) | 466 passed, 1 failed, 5 skipped (9 min 12 s). The failure was `test_local_publication_end_to_end`: it compares the group entry exactly, and the entry now has the new `toggleable`/`initialVisibility` fields. After updating the expectation: `tests/integration/test_publishing_pipeline.py` → 4 passed. |
| The 5 opt-in skips with the tools enabled (go-pmtiles CLI, moto) | 22 passed |
| Owner project `arlowebtest.qgs` with 3 map themes as views | exported in 175 s (15 889 searchable features); opened in Chromium (desktop, theme applied, phone sheet) with 0 errors and 0 third-party requests. The project has no raster layers, so raster export was tested on fixtures only. |


## 4.4.0 additions: parcel report (telekinformáció)

| Item | Status | Behaviour | Tests (executed) | Limits |
|---|---|---|---|---|
| Settings | **Done** | `ParcelInfoConfig` in the profile, saved in the project: parcel layer and unique id, data fields shown, zone layer and code field (its style gives the zone graphics), zone values per part, cut-line layers, restriction layers (title, explanation, legal reference, approved name field, protection distance for lines/points), an optional regulation table joined by zone code (for the local building code later), thresholds, notice. Edited on the new *Parcel report* tab. `interaction.legendVisibleOnly`. | `tests/unit/test_publishing_profile.py` (round trip, schema); `tests/integration/test_publish_dialog*.py` (window still saves/restores). | — |
| Export-time computation | **Done** | `parcel_report.py`, stage PARCELS, QGIS main thread. Per parcel inside the extent: area in the parcel layer's projected CRS (EOV), parts = parcel × zone polygons split by the cut lines (GEOS polygonize), cut lines bounding a part *inside* the parcel (its own edge excluded), restrictions by overlap (polygons, buffered lines/points) with area and share. Slivers < `minArea` and overlaps < `minShare` are ignored. Legend graphics are rendered from the symbols QGIS actually draws for the feature (rule, category and data-defined colours; scale-dependent rules evaluated at several scales; deduplicated by image). Labels come from the most specific active legend entry. The parcel key uses the same expression as the tiles' `q2vt_feature_key`. Output is sharded JSON (`parcels/`, FNV-1a like the feature lookup) without geometry. | `pytest tests/integration/test_publishing_parcel_report.py` → 4 passed: exact areas (2240/960/800 m² of 4000), regulation-line and zone-boundary flags, an own edge is not a cut, polygon/line-buffer/point-buffer overlaps (circle segment within 5 %), edge-only contact ignored, graphics exist, keys = tile keys, unapproved values (owner, other fields) never public, manifest schema. Owner project: 2815 parcels in 7.8 s, 22 graphics, 1.2 MB. | Planar areas only for projected CRS (geographic data uses a UTM zone). The project's land-registry area field is not used; the area is computed from the geometry. |
| Viewer card | **Done** | Clicking a parcel opens its report in the panel (*Telek* tab; bottom sheet on phones) instead of a popup: total area (ha + m²), parcel data, numbered parts (marker per part on the map) with zone graphic, share bar, cutting lines and zone values (+ regulation table rows), restrictions with graphics, overlap, names, explanation, reference, protection distance, the notice, link and print. Text only. | `pytest tests/browser/test_web_viewer_parcel.py` → 2 passed (click → card without popup, 3 numbered parts and markers, graphics loaded, notice; the link reopens it; legend "only visible" empties far away and returns when switched off). Screenshots on the owner project reviewed (desktop, phone, restrictions). | — |
| Legend: only visible | **Done** | Publisher default (Interaction tab) + visitor switch on the legend; lists only rules/layers with features drawn in the view (rendered-feature query), raster layers by area and zoom; recomputed when the map is idle. | browser test above. | Labels alone do not make a rule "visible". |
| Accuracy check (owner project) | **Done** | 20 parcels (10 with several parts, 10 random) compared with an independent QGIS Processing `native:intersection` (parcels × zone layer, parcels × Natura 2000). Largest difference: **0.045 m²**. | See the table. | — |

| Hrsz | Area m² | Parts | Report: zone m² | Processing: zone m² | Max diff m² | Natura report / processing m² |
|---|---|---|---|---|---|---|
| 1298/2 | 2 259.78 | 2 | Lf-1 2124.5  Ut-2 135.3 | Lf-1 2124.5  Ut-2 135.3 | 0.001 | 0.0 / 0.0 |
| 034/2 | 37 036.22 | 2 | Má 24815.2  Ut-1 12221.0 | Má 24815.2  Ut-1 12221.0 | 0.002 | 0.0 / 0.0 |
| 1407 | 2 008.52 | 2 | Lf-1 1697.7  Ut-1 310.8 | Lf-1 1697.7  Ut-1 310.8 | 0.005 | 0.0 / 0.0 |
| 303 | 469.38 | 2 | Ut-1 187.1  Z 282.3 | Ut-1 187.1  Z 282.3 | 0.001 | 0.0 / 0.0 |
| 0148/1 | 21 483.41 | 2 | Ev 19270.5  Mt 2212.9 | Ev 19270.5  Mt 2212.9 | 0.045 | 21483.4 / 21483.4 |
| 026/8 | 14 130.13 | 3 | Ev 9215.4  Má 4914.8 | Ev 9215.4  Má 4914.8 | 0.004 | 0.0 / 0.0 |
| 1476 | 5 045.26 | 2 | Lf-1 4611.9  Ut-1 433.4 | Lf-1 4611.9  Ut-1 433.4 | 0.005 | 0.0 / 0.0 |
| 030/1 | 18 050.63 | 3 | Má 16351.9  Ut-1 1698.7 | Má 16351.9  Ut-1 1698.7 | 0.006 | 0.0 / 0.0 |
| 1391 | 1 322.41 | 2 | Ut-1 1245.0  Vt-2 77.5 | Ut-1 1245.0  Vt-2 77.4 | 0.003 | 0.0 / 0.0 |
| 1599 | 708.47 | 2 | Lf-1 652.8  Ut-2 55.7 | Lf-1 652.8  Ut-2 55.7 | 0.004 | 0.0 / 0.0 |
| 017/7 | 36 813.13 | 1 | Má 36813.1 | Má 36813.1 | 0.0 | 0.0 / 0.0 |
| 655 | 1 851.71 | 1 | Lf-1 1851.7 | Lf-1 1851.7 | 0.0 | 0.0 / 0.0 |
| 1273 | 1 486.94 | 1 | Lf-1 1486.9 | Lf-1 1486.9 | 0.005 | 0.0 / 0.0 |
| 0131/3 | 187 625.43 | 1 | Ev 187625.4 | Ev 187625.4 | 0.003 | 0.0 / 0.0 |
| 04/5 | 7 751.92 | 1 | Má 7751.9 | Má 7751.9 | 0.003 | 0.0 / 0.0 |
| 2274 | 300.48 | 1 | Lf-2 300.5 | Lf-2 300.5 | 0.0 | 0.0 / 0.0 |
| 2202 | 206.39 | 1 | Lf-2 206.4 | Lf-2 206.4 | 0.003 | 0.0 / 0.0 |
| 02/14 | 5 070.99 | 1 | Má 5071.0 | Má 5071.0 | 0.001 | 0.0 / 0.0 |
| 1381 | 734.21 | 1 | Lf-1 734.2 | Lf-1 734.2 | 0.005 | 0.0 / 0.0 |
| 043/14 | 734.36 | 1 | Má 734.4 | Má 734.4 | 0.004 | 0.0 / 0.0 |

### Regression (4.4.0)

| Run | Result |
|---|---|
| `pytest` (full suite) | **473 passed, 5 skipped** (the opt-in go-pmtiles/moto tests), 0 failed, 9 min 13 s |

## 4.4.2: visible-polygon labels appear with the other labels

Owner report: polygon labels on the visible part of their polygon were right but showed up
noticeably after the other labels. The helper (`resources/ml_viewer/visible_labels.mjs`) waited
for the whole tile source to load, then a 60 ms timer, and never updated while the map moved;
every change rewrote the whole label source.

| Change | Effect |
|---|---|
| Recompute as polygon tiles arrive (throttled 80 ms) and while the map moves (150 ms), not only after everything loaded / `moveend` | the points reach MapLibre when the other labels' tiles arrive |
| Labels in advance for polygons in the loaded tiles up to half a screen beyond each edge (not within 48 px of it, so no text reaches the screen) | panning reveals labels that are already placed |
| Use the tile level the map wants as soon as it has data (zooming out: before the old, deeper tiles are dropped) | new area's labels while zooming out |
| Label point tiles stop at z15 (overzoomed beyond; ~0.1 m precision) | fewer, bigger label tiles: panning rarely needs new ones laid out |
| Incremental `updateData` (only added / moved / removed points) instead of `setData` | only label tiles around changes reload |
| `snapshot()` API (tests no longer read MapLibre's private `_data`) | |

Measured on the owner project (EOV, 2815 parcels) in headless Chromium (SwiftShader, 4 cores),
16 jump-pans / 12 one-level zooms, lag = when 90 % of the final visible-polygon labels are drawn
minus the same for the other labels: pans median **540 ms → 0 ms**; zoom steps median
**2.3 s → 0.8 s** with the 40 ms probe (the heavy probe inflates zoom times; without it the new
zoom's label tiles load in 0.2–0.35 s, 1–2 SwiftShader frames). After 8 mixed pans/zooms every
drawn label matches the written point (max 0.6 px at z19), no page errors.

| Run | Result |
|---|---|
| `pytest tests/browser/test_visible_labels.py tests/browser/test_web_viewer_features.py tests/browser/test_pmtiles_transport.py tests/browser/test_browser_parity.py tests/unit/test_publishing_web_builder.py` | **59 passed** (2 new: labels in advance with the edge guard; wanted tile level) |

## 4.4.3: visible-polygon labels stay glued to the map while it moves

Owner feedback on 4.4.2: labels no longer late, "but not feels right". Cause: 4.4.2 recomputed
labels while the map moved and applied the edge / visible-part rule each time, so labels near
the screen edge were re-centred again and again during a drag. Now, while `map.isMoving()`,
placed labels keep their position and only polygons without a label get one; the rule is
applied once when the map stops (as before 4.4.2).

Measured during three smooth 2-second mouse drags on the owner project (headless Chromium,
label sampled every 60 ms): jumps of a placed label / blinks (vanish and come back) **during
the drag**: 4.4.1 1+1+13 / 0+1+8, 4.4.2 24+17+34 / 4+0+6, **4.4.3 0+0+0 / 0+0+0**; after
release at most one settling move (the visible-part rule). Pan lag median stays 0 ms; drawn
labels still match the written points (max 0.6 px at z19), no page errors.

| Run | Result |
|---|---|
| `pytest tests/browser/test_visible_labels.py tests/browser/test_web_viewer_features.py tests/browser/test_pmtiles_transport.py tests/browser/test_browser_parity.py tests/unit/test_publishing_web_builder.py tests/integration/test_plugin_package.py` | **61 passed** (new: labels do not move while the map moves) |

## 4.5.0: faster re-exports and uploads, Cloudflare R2 guide

Owner request (2 Oct 2026): "Every layer is regenerated every time? Could they be cached so
small changes don't take so long? … How do I get the Cloudflare values? Some explanation in
the plugin itself would be nice."

| Change | Where |
|---|---|
| **Export cache**: datasets of rule groups whose layer source files (size/mtime; GeoPackage `gpkg_contents.last_change`, not `-wal` files), rule snapshot and export settings are unchanged are copied from `<output folder>/.q2vt-cache`; only the sources of changed rules are read again; diagnostics are stored and replayed | `core/export_cache.py`, `core/rules_exporter.py` |
| **Per-layer tile sets** (with the cache): one ogr2ogr run per QGIS layer, several in parallel, reused while the layer's datasets are unchanged, merged by concatenating MVT layers | `core/tiles_generator.py` |
| **Parcel report** reused while its layers (data and style), settings and extent are unchanged | `publishing/parcel_report.py` |
| Deterministic keys: expression variables without object addresses, canonical style XML (Qt orders attributes randomly per session, also inside nested symbol XML), data-defined property keys sorted (were set-ordered per session — also makes exports reproducible) | `export_cache.py`, `ddp_fetcher.py`, `maplibre_converter.py` |
| Output tab: *Reuse unchanged layers* (default on), *Clear cache…*; profile `output.reuseUnchanged` | `gui/publish_dialog.py`, schema |
| **Uploads**: files with the same SHA-256 and size as in the current release are copied inside the bucket (S3/R2 `CopyObject`, new headers + checksum); a failed copy falls back to an upload | `deployments.py`, `providers/` |
| **Cloudflare R2 guide** in the Publish window (10 steps, dashboard links), tooltips on every destination field, Cloudflare addresses pasted into the fields fill the account id and bucket, fields per destination kind, live *Map address*, *Save keys in QGIS…* (encrypted Basic configuration) | `gui/r2_guide.py`, `providers/r2.py` (`parse_pasted`) |

Owner project (EOV, 40 layers, 153 datasets, 4585 tiles, parcel report on), headless:

| Export | Time |
|---|---|
| Before (4.4.3) | 168 s (95 s datasets, 46 s tiles, 7 s parcel report) |
| First export with the cache (empty) | 142–153 s (tiles 30 s: layers tiled in parallel) |
| Again, nothing changed | **15 s** (153/153 datasets, 40/40 tile sets, parcel report reused) |
| One feature of one layer moved | **15 s** (1 dataset and 1 tile set redone) |
| A fill colour + a label rule changed | **22 s** (the label's dataset; the parcel report, whose restriction legend changed colour) |

Equivalence: the cached exports' tiles equal an uncached export of the same data in every
layer's features (geometry, attributes); the order of features inside some label layers
differs, as it does between two uncached exports (367 of 4585 tiles), and one z17 tile of one
layer differs by one vertex (GDAL simplifies a tile slightly differently when that layer is
tiled alone). Fidelity diagnostics identical (18). A second publish against moto uploads less
than half of the release; copied objects carry the new release's checksum and headers.

| Run | Result |
|---|---|
| `pytest tests/unit/test_export_cache.py tests/integration/test_export_cache.py` | 7 passed (keys, fingerprints incl. GeoPackage edits in the `-wal`, stable variables, canonical XML, entries, replay, pruning; two-layer project exported twice / after a data and a label edit = uncached export; merge) |
| `pytest tests/integration/test_publish_dialog_r2.py` | 3 passed (pasted addresses, fields per kind, address preview, guide content, save keys) |
| `pytest tests/unit/test_publishing_providers.py` (+ moto opt-in) | 14 passed (+4 with moto: unchanged files copied, changed and missing ones uploaded) |
| Publishing suites (pipeline, parcel report, raster, basemap/themes, dialogs, presets, profile, web builder) with the cache on by default | 76 passed |
| `pytest` (full suite) | **489 passed, 5 skipped** (opt-in go-pmtiles/moto), 0 failed, 8 min 49 s |

## 4.5.2: zone labels in the middle of the zone, none on slivers

Owner report: zone codes (*Szabályozás övezetkódok*, QGIS **Horizontal** placement, visible
polygon, z-index 10) were placed at the bottom of their zone, cut at the screen edge, or
pushed out of the zone; "they should be inside the zone, in the middle if space is enough"
and "not shown if only a small part of the polygon is in view".

| Cause | Fix |
|---|---|
| Horizontal / Free polygon labels used the centroid; QGIS ranks their candidates by the distance from the polygon's pole of inaccessibility | exporter: static point `pole_of_inaccessibility`; style metadata `q2vt:label-anchor: pole`; viewer: the roomiest point of the visible part (grid search; edges where tiles cut the polygon do not count) |
| After a pan a label stayed wherever it still was inside its polygon (e.g. at the bottom) | kept only with ≥ 80 % of the best room (pole) or within 24 px of the centroid; otherwise moved to the middle once the map stops and its tiles are loaded |
| Labels shown with only a sliver of their polygon in view, cut by the screen edge | a label is shown only if its estimated box (text, size and frame padding evaluated from the style at the current zoom) fits on the screen and the visible part is at least as large as the box (QGIS drops candidates outside the map extent) |
| A zone and the parcel number of the same parcel shared one point; MapLibre's variable anchors shifted one a box width (out of the zone, off the screen) | labels placed by QGIS priority, then z-index (`q2vt:label-rank`), larger polygons first; later ones take the roomiest spot clear of the boxes placed before; the computed point is kept (no variable-anchor shift) |

Owner project, the reported view (z18.2): every zone code inside its zone, Vt-2 in the middle,
Vt-1 clear of parcel 902, no label at a screen edge; the same after arriving by panning.
Label placement time (headless, 4 cores): z15 2039 labels 0.37 s, z16 0.18 s, z17 0.06 s
(only when the map stops). In the project CRS QGIS draws the zone codes at the same size as
the web map (Vt-2 frame 147×68 px vs 153×78 px).

| Run | Result |
|---|---|
| `pytest tests/browser/test_visible_labels.py` | 15 passed (5 new: tile cuts ignored, fit on screen / slivers hidden, labels keep clear of each other, old edge spot moves to the middle, style size expressions) |
| label-related suites (web viewer features, transport, parity, web builder, export cache) | 67 passed |

## 4.5.3: contour labels on the visible part of the line

Owner report: "I don't see labels on szintvonalak". The contours (*Szintvonal*, 6240 lines,
QGIS **Parallel** placement, no repeat distance, one label per line) were labelled at the
middle of the whole line (`_single_line_label_as_point`), which for long contours is far off
screen; QGIS places line labels inside the map extent.

| Cause | Fix |
|---|---|
| A once-per-line label was a static point at the middle of the whole line | exporter: the label's lines ship as `<label dataset>_vl` (clipped to the extent) with the label's fields; style metadata `q2vt:visible-polygons` → the `_vl` layer, `q2vt:visible-kind: line`; the static midpoint stays for other clients |
| — | viewer: the line pieces of the loaded tiles are cut to the screen (Liang–Barsky), joined where tiles cut them, and the label goes to the middle of the longest visible stretch, rotated along it (kept upright); none if the label does not fit along the stretch or on the screen; it slides along the line (then to shorter stretches) to keep clear of labels placed before; a kept spot stays while near the middle; frozen while the map moves |
| MapLibre hid labels the placer thought clear: its collision boxes use the text size of the next whole zoom (layout size), up to 2× the drawn size for map-unit text (measured with `showCollisionBoxes`: "190" drawn 30 px wide, box 60 px) | the clearance test of line labels uses MapLibre's collision box (text size at ⌊zoom⌋+1, rotated envelope); every placed label now also reports its collision box |

Owner project, the reported view (z18.2): contour labels 190, 195 and 185 on screen (none
before), also after arriving by panning. QGIS also draws 187.5 there; in the web map its
collision box (~96 px tall at z18.2) does not fit between the parcel numbers anywhere along
its visible stretch, so MapLibre hides it (at zooms closer to the next whole zoom the boxes
are smaller). Label placement time (headless): z15 3235 labels 0.43 s, z16 0.16 s, z17
0.05 s, z18 0.02 s (only when the map stops).

| Run | Result |
|---|---|
| `pytest tests/browser/test_visible_labels.py` | 20 passed (5 new: middle of the visible stretch, rotation upright, too short → none, slide along to a clear collision box / keep a spot near the middle, other visible stretches) |
| `pytest tests/integration/test_end_to_end.py -k once_per_line` | 1 new passed (`_vl` lines with the label fields, metadata, rotated midpoint) |
| label-related suites (web viewer features, transport, parity, web builder, export cache) | 77 passed |
| exporter / pipeline suites (end to end, units and properties, publishing pipeline, materialize) | 134 passed |

## 4.5.4: every contour label placed is shown

Owner report on the 4.5.3 screenshots: "only 190 is showing, 185 and 187.5 don't" (185 was
pushed to the screen edge, 187.5 hidden). Both were MapLibre collision rejections: its
collision boxes use the text size of the next whole zoom (up to 2× the drawn size for map-unit
text), so a spot clear by the drawn size was rejected, and clearing the oversized box left no
spot between the parcel numbers.

| Cause | Fix |
|---|---|
| MapLibre's collision boxes are up to 2× the drawn label at fractional zooms | line-label layers are drawn through the overlap fallback (`q2vt:overlap: if-required` at runtime): what MapLibre rejects, the copy draws |
| The placer must then do the whole collision check | line labels are placed clear (drawn size, rotated envelope) of the labels placed before and of the point labels MapLibre draws itself (`queryRenderedFeatures` on the other symbol layers: building numbers, names); no clear spot: no label |
| 4.5.3's collision-size boxes (`hitBox`) | removed |

Owner project, the reported view (z18.2): 187.5, 190, 185 and 195 shown (QGIS shows the same
four), also after arriving by panning. Label placement time (headless): z15 0.5 s, z16 0.17 s,
z17 0.06 s, z18 0.02 s.

| Run | Result |
|---|---|
| `pytest tests/browser/test_visible_labels.py` | 20 passed (line tests: avoid by drawn size, no clear spot → no label) |
| label-related suites (visible labels, web viewer features, transport, parity, web builder, end to end) | 89 passed |

## 4.5.5: shared boundaries stay on top of each other

Owner report: "the övezethatár is not exactly on the parcel boundary on the south boundary of
parcel 862". In the source data (EOV) the zone boundary's vertices lie exactly on the parcel's
edge (0.000 m). The base-layer pipeline simplified every layer with a fixed tolerance of 1 CRS
unit (1 m in EPSG:3857), each layer on its own, so the parcel ring and the zone line (and the
marker points placed along it, `…m02`) lost different vertices.

| Cause | Fix |
|---|---|
| `_DATA_SIMPLIFICATION_TOLERANCE = 1` m, independent of zoom (at z22, 1 m is ~50 px) | `_DATA_SIMPLIFICATION_TOLERANCE = 0.25` tile units at the export's max zoom (`RulesExporter._simplification_tolerance`: 1.9 cm at z17, 3.7 cm at z16) - below the tiles' own coordinate rounding, which keeps shared vertices identical |

Owner project, in the exported data (EPSG:3857): the zone-boundary marker points were up to
0.96 m off parcel 862's boundary, now 0.000-0.001 m; the dots sit on the parcel line in the
viewer. Export 159 s (was 154 s), web release 25.8 MB (was 25.5 MB).

| Run | Result |
|---|---|
| `pytest tests/integration/test_end_to_end.py -k detail_below` | 1 passed (new; fails with the old tolerance) |
| exporter / pipeline / cache suites (end to end, units and properties, publishing pipeline, materialize, export cache) | 141 passed |

## 4.5.6: Publish window - extent from a layer, roomy Interaction tab, settings files

Owner reports: "web map extent setting is bad now, it shows cut off coordinates and only a
button with use the canvas extent; there should be a lookup combo for selecting a layer";
"on the interaction tab the left layer list is very cramped"; "there should be an option to
export and import settings to and from file in the gui".

| Change | Detail |
|---|---|
| Extent | layer combo (any layer; empty entry = fixed extent) + *Map canvas* button; `view.extentLayer` (schema) - the layer's extent is recomputed at every export; a removed layer keeps its last extent, fixed; summary "Layer extent: about 13.2 × 10.2 km / E … / N …" instead of EPSG:3857 numbers |
| Interaction tab | the field note did not wrap (≈2000 px minimum width) and squeezed the layer list; it wraps now; the list has a 220 px minimum, wraps names, shows layer icons and tooltips; the splitter cannot collapse it |
| Settings file | *Settings file…* menu: export (`.q2vt.json`: profile + names of the layers it refers to) and import (bare profiles too); layers matched by id, else unique name; unmatched left out and reported; same web map or a new one |

| Run | Result |
|---|---|
| `pytest tests/integration/test_publish_dialog_extent.py` | 4 passed (new) |
| Publish window and profile suites (`test_publish_dialog*.py`, `test_publishing_profile.py`) | 45 passed |

## 4.6.0: Free (angled) labels, visible scales per layer, no views bar for one view

Owner reports: street names not rotated ("(878) Kossuth Lajos utca"); "a column where the
scales the layer is seen can be set, also en masse ... the display can be very slow when
zoomed out"; "don't display Nézetek when there's only one".

| Change | Detail |
|---|---|
| Free (angled) placement | Földrészletek labels use QGIS Free placement (5); it was treated like Horizontal. QGIS (pal): horizontal if the label fits inside the polygon, else along the polygon's oriented box. Converter: `q2vt:label-orient: free` (unless a QGIS rotation is set); viewer: `text-rotate` from `q2vt_free_rotation`; horizontal if `fitsFlat` (free room ≥ half diagonal, else exact `boxInside`), else `localDirection` (length-weighted mean edge direction, tile cuts left out, around the spot) and the turned box's envelope for screen fit / clearance (searched again only if it does not fit) |
| Visible scales | `LayerConfig.min_scale` / `max_scale` (schema `minScale` / `maxScale`); Map tab column *Scales* + `ScaleRangeDialog` (QgsScaleWidget), per row (double-click) or for the selection / groups; `combine_scale_ranges` with the layer's own range in the flattener (root rule: renderer and labels), raster tile zooms and the manifest; not tiled where hidden; short column headers (tooltips) to leave room for layer names |
| Views bar | hidden when fewer than two views |

Owner project: "(878) Kossuth Lajos utca", Ady Endre, Vásártér, Zombori utca, Vasút út along
their streets; narrow parcels turned, wide ones horizontal. Label placement z15 0.64 s with Free,
0.57 s without (same run); z16 unchanged. Views bar: 3 views shown, 1 view hidden.

| Run | Result |
|---|---|
| `pytest tests/browser/test_visible_labels.py` | 22 passed (2 new) |
| `pytest tests/integration/test_publish_scale_limits.py` | 3 passed (new) |
| Publish window and profile suites | 48 passed |
| browser, web builder, end to end, flattener, publishing pipeline, export cache suites | 116 passed |
