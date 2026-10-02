# Web publishing — architecture

```text
Publish Web Map window (src/gui)          Processing algorithm (unchanged + TILE_ARCHIVE_FORMAT)
        │  PublicationProfile (saved in the project, no secrets)
        ▼
publishing.controller.export_local            (QGIS main thread, NoThreading kept)
  PLAN        profile vs project (layers, fields, expressions)
  EXPORT_MVT  existing compiler: QGIS2VectorTiles(layer_ids, feature_keys, extra_tile_fields,
              background without raster, add_result_layer=False, cache) -> tiles.mbtiles + style
              (export cache: datasets and per-layer tile sets of unchanged layers reused,
              per-layer tiles merged; core/export_cache.py)
  RECORDS     qgis_model.collect_records: published features (extent x exported rule filters),
              keys validated, label/terms/anchor/bounds/approved attributes (private JSONL)
  LEGEND      QGIS-rendered swatches of the original legend items; logical model
              (groups / layers / rules / components from flattener provenance), locked
              layers/groups, QGIS map themes as presets
  PARCELS     optional parcel report: parts (zones x cut lines), restrictions, legend
              graphics, sharded JSON without geometry (parcel_report.py)
  RASTER      QGIS raster layers rendered by QGIS into their own image PMTiles
              (raster_tiles.py; vector layers never go here)
  BASEMAP     optional OpenStreetMap extract (Protomaps schema) by HTTP ranges or from a
              file, flavor styles, glyphs for its labels (basemap.py)
  BUILD       web_builder.build_release -> releases/<id>/ (staging, validation, rename),
              pmtiles_builder (same MVT payloads), search/feature indexes, disclosure check,
              current.json atomically
        ▼
publishing.deployments.publish                (QgsTask: files + network only)
  pointer+ETag -> upload inventory (unchanged files copied from the current release inside
  the bucket) -> stable entry -> public_verify -> conditional activation
```

## Packages

| Path | Role |
|---|---|
| `src/publishing/models.py`, `profile.py` | Profile and ExportBundle contracts, validation, migrations, disclosure fingerprint. |
| `src/publishing/pmtiles_builder.py`, `validation.py`, `mvt.py` | MBTiles → PMTiles v3 (official writer, vendored), archive validation, MVT decoding, vector-only checks. |
| `src/publishing/bundle.py`, `web_builder.py`, `content_types.py` | Portable style, immutable releases, inventories, atomic pointer, retention, ZIP. |
| `src/publishing/provenance.py`, `identifiers.py`, `disclosure.py`, `qgis_model.py` | Logical model, stable keys, public-field contract, QGIS-side records and legend. |
| `src/publishing/search_index.py`, `feature_index.py` | Publication-wide search shards and exact lookup shards. |
| `src/publishing/providers/`, `public_verify.py`, `deployments.py`, `credentials.py` | Hosting providers, public checks, activation protocol, QGIS auth adapter. |
| `src/publishing/preview_server.py` | Loopback HTTP server with byte ranges. |
| `src/publishing/raster_tiles.py` | QGIS raster layers → PNG/JPEG/WebP tiles in their own PMTiles archive. |
| `src/publishing/parcel_report.py` | Parcel report (telekinformáció) computed at export time. |
| `src/publishing/basemap.py` | Vector basemap: build discovery, range reader, region extract, flavors, glyphs. |
| `resources/basemaps/protomaps/` | Vendored `@protomaps/basemaps` 5.7.2 layer definitions (hu/en × 5 flavors). |
| `src/core/export_cache.py` | Export cache: content-addressed datasets, per-layer tile sets and the parcel report of unchanged layers (`<output folder>/.q2vt-cache`). |
| `src/gui/` | Publish window, project-saved profiles, release history, Cloudflare R2 guide (`r2_guide.py`). |
| `resources/web_viewer/` | Static MapLibre viewer (ES modules, no framework, no CDN). |
| `resources/ml_viewer/visible_labels.mjs` | The single maintained visible-polygon label helper (also used by the legacy viewer). |
| `schemas/publishing/` | JSON Schemas of profile, manifest, release, current pointer and search index. |

## Invariants

* Map data is MVT in PMTiles v3 (or XYZ MVT for the legacy package). Vector layers are never
  rasterized. The only raster sources are the QGIS *raster* layers' own image archives
  (`q2vt_raster_*`, `data/raster-<id>.pmtiles`, listed in the manifest, no URL in the style);
  any other raster, image, video, canvas or persisted GeoJSON source is refused before writing
  and again in the browser. (Owner decision of 1 Oct 2026: raster layers are published as
  image tiles in separate files; this extends, not replaces, the vector-only rule.)
* The basemap is vector tiles too: its own archive (`data/basemap.pmtiles`), flavor styles in
  `basemaps/*.json` added by the viewer under every project layer at runtime; `style.json`
  never references it.
  Runtime GeoJSON is limited to visible-polygon label points (derived from loaded tiles), the
  search marker and measurement drawings.
* Packaging is transport-only: `bundle.style_semantic_diff` must be empty (only tile URLs,
  zoom bounds, sprite/glyph URLs and removed raster basemaps may differ).
* Releases are immutable; `current.json` is the only mutable object and is replaced
  atomically (locally) or with `If-Match` / `If-None-Match` (object storage).
* The QGIS project is never modified by an export (layer tree, visibility, styles); only the
  user's *Save settings* (and successful publishing) write the profile into project
  properties.

## Compatibility notes (paths changed since the plan's baseline)

* `src/core/publisher.py` keeps `portable_style`, `write_xyz_tiles`, `write_static_package`;
  the first two are wrappers of `src/publishing/bundle.py`.
* `FlattenedRule` gained `provenance` (default `None`).
* `RulesExporter` gained `feature_keys` and `extra_tile_fields` (defaults empty).
* `QGIS2VectorTiles` gained `layer_ids`, `archive_format`, `add_result_layer`,
  `feature_keys`, `extra_tile_fields` and `export_bundle()` (defaults: previous behaviour).
