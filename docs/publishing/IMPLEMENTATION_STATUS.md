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
