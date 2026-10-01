# Web publishing — baseline record (PUB-01)

Recorded on 1 October 2026 before any publishing code was added.

## Repository state

| Item | Value |
|---|---|
| HEAD | `4b9bace6535f45f659677cbee58fddb63e3bdb9d` (fork **4.1.8**, "labels avoid overlaps, stable visible-polygon labels, parcel numbers") |
| Plan's inspected baseline | `8607c980c3d4c80e96fbdaac93c1c5e01d6c6f6a` (4.1.7). HEAD is one commit newer; that commit is kept and is authoritative. |
| Working tree | clean (no uncommitted changes) |
| Branch | `ccr-9afda0b0-euuxrd`, fast-forwarded to `main` |

Changes since the plan's baseline that the publishing work must preserve:
`resources/ml_viewer/visible_labels.mjs` now also exports `enableOverlapFallback`
(label layers with metadata `q2vt:overlap = "if-required"` get a runtime copy that draws
labels MapLibre could not place; uses feature state). Labels keep
`q2vt:visible-polygons` / `q2vt:label-per-part` metadata.

## Existing behaviour (unchanged by the publishing work)

* Processing algorithm `QGIS2VectorTilesFork:QGIS2VectorTiles_action`, flag `NoThreading`,
  parameters `MIN_ZOOM, MAX_ZOOM, EXTENT, CPU_PERCENT, FIELDS_INCLUDED,
  POLYGONS_LABELS_BASE (default 2), BACKGROUND_TYPE (0 = OpenStreetMap **raster**),
  VIEWER, FIDELITY_MODE, OVERZOOM, STATIC_PACKAGE (default false), PARALLEL (default
  false), OUTPUT_DIR`.
* Output folder `<OUTPUT_DIR>/<timestamp>/`: `tiles.mbtiles` (GDAL MVT, TMS rows, gzip
  payloads; the `json` metadata's `vector_layers` is truncated by GDAL when there are many
  layers — 164 of 264 layers missing on the owner's project), `style/style.json`,
  `style/sprite*`, `style/glyphs/`, `utils/` (viewer + `tiles_server.py`),
  `fidelity_report.json|html`, `export_log.txt`; optional `web/` (XYZ static package,
  `src/core/publisher.py`).
* Layers exported = vector layers whose layer-tree node `isVisible()`
  (`RulesFlattener._is_valid_layer`).
* The default background is the OpenStreetMap **raster** source; the vector-only
  publishing path must not publish it (`publishing.validation.strip_raster_background`).

## Pinned resources

| Resource | Version | SHA-256 |
|---|---|---|
| `resources/ml_viewer/maplibre-gl.mjs` | MapLibre GL JS 6.11.2 (ESM) | `3f5556…ee5d` |
| `resources/ml_viewer/maplibre-gl-shared.mjs` | 6.11.2 | `76b5f5…a47960` |
| `resources/ml_viewer/maplibre-gl-worker.mjs` | 6.11.2 | `01ad19…296e` |
| `resources/ml_viewer/maplibre-gl.css` | 6.11.2 | `d8617d…02d3` |
| `resources/ml_viewer/visible_labels.mjs` | fork 4.1.8 | `d73200…de768` |
| `resources/ml_viewer/viewer.html` | fork 4.1.8 | `446d79…3445` |
| Browser-test devDependencies | `@maplibre/maplibre-gl-style-spec` 26.4.4, `playwright-core` 1.56.1, Chromium 1194 | |

## Test environment

QGIS 3.34.4-Prizren, GDAL 3.8.4, Python 3.12.3, Node v22.22.2, Linux (cloud container).
No QGIS 3.44 / 4.x or Windows available here: **not tested** (limitation, not a pass).

## Baseline test results

```
QT_QPA_PLATFORM=offscreen python3.12 -m pytest -q -rs tests
292 passed, 967 warnings in 291.57s (0:04:51)     # 0 skipped, 0 failed
```

All three levels ran (unit, PyQGIS integration, browser with Chromium 1194).
The XYZ transport baseline renders used by the PMTiles parity test (PUB-07) are produced
by the same browser harness in the same run, so XYZ and PMTiles are always compared
against each other on the identical renderer/style/assets.
