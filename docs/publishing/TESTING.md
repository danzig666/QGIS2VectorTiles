# Web publishing — tests

All tests run with `pytest` from the repository root (see also `docs/fidelity/TESTING.md`).

| File | Level | Covers |
|---|---|---|
| `tests/unit/test_publishing_validation.py` | pure | vector-only contract, paths, forbidden files, leak scan |
| `tests/unit/test_publishing_pmtiles.py` | pure (+ go-pmtiles CLI) | MBTiles → PMTiles: every address and decompressed payload, TMS→XYZ, zoom 0, sparse, dedup, Unicode paths, raster/non-MVT refusal, malformed inputs, truncated metadata, immutability, cancellation, tampering |
| `tests/unit/test_publishing_profile.py` | pure | profile round trip, migration, validation, no secrets, review fingerprint, JSON Schemas |
| `tests/unit/test_publishing_web_builder.py` | pure | release layout/inventory, transport-only style, schemas, XYZ, failure injection, cancellation, conflict/rollback, retention, recovery, ZIP |
| `tests/unit/test_publishing_preview_server.py` | pure | byte ranges (206/416/suffix/open), HEAD, MIME, traversal, nested Unicode paths |
| `tests/unit/test_publishing_indexes.py` | pure + Node | keys, Hungarian normalization (Python = JS), 120k-record sharded search, lookup shards |
| `tests/unit/test_publishing_providers.py` | pure | upload/verify/activate protocol on a fake S3 client with failure injection |
| `tests/unit/test_publishing_s3_moto.py` | opt-in | real vendored boto3 against a moto S3 server |
| `tests/unit/test_publishing_ssh.py` | pure + local sshd | SSH / SFTP destination: batch quoting (`./` before a leading `-`), askpass (UTF-8 `.cmd` helper, OpenSSH 8.4 check on Windows), key paths with `%`, state-file plan with the folder listing (repair, replaced files, never deleting a foreign file), failed deletions, batch order, errors; end to end against a throwaway OpenSSH server with umask 027 (`tests/publishing_sshd.py`, skipped without `/usr/sbin/sshd` and the client): two publishes into a folder with other files, folder modes, repair + failed deletion + taking over another map's folder, passphrase via askpass, unusable key file, host key change, cancel midway, public URL check |
| `tests/integration/test_publish_dialog_ssh.py` | PyQGIS (offscreen) + local sshd | SSH fields per kind, pasted target vs a typed host:port / IPv6 address, settings without the password, R2 keys and domain never used for SSH, closing with unfinished settings, Test connection and a publication through the window |
| `tests/integration/test_publishing_export.py` | PyQGIS | layer selection incl. hidden layers, PMTiles/Both, Processing outputs |
| `tests/integration/test_publishing_pipeline.py` | PyQGIS | logical model, keys in tiles, disclosure canary, indexes, identity errors |
| `tests/integration/test_publish_dialog.py` | PyQGIS (offscreen) | the Publish window end to end |
| `tests/browser/test_pmtiles_transport.py` | browser | XYZ vs PMTiles pixel parity, partial reads, stable entry, error states |
| `tests/browser/test_web_viewer_features.py` | browser | toggles, opacity, labels/filters, identify/XSS, search, deep links, permalinks, measure, phone |
| `tests/unit/test_publishing_basemap.py` | pure | basemap extract (local + HTTP ranges), build discovery, retries, schema/size refusal, 10 flavors vector-only |
| `tests/integration/test_publishing_raster.py` | PyQGIS | raster layers → own image archive, z-order, transparency skipped, empty/oversized |
| `tests/integration/test_publishing_basemap_themes.py` | PyQGIS | bundled basemap + glyphs, theme presets, locked layers/groups, manifest schema |
| `tests/integration/test_publish_dialog_layers.py` | PyQGIS (offscreen) | raster rows, bulk changes, group locks, map themes, raster/basemap settings saved |
| `tests/integration/test_publishing_parcel_report.py` | PyQGIS | parcel report: exact part areas, cut lines, restriction overlaps, graphics, keys, privacy |
| `tests/browser/test_web_viewer_parcel.py` | browser | parcel card, markers, link, legend "only visible" |
| `tests/browser/test_web_viewer_basemap_raster.py` | browser | basemap flavors under the map, raster drawn, presets, locks vs links, no third party, layouts, dark switch |

Optional tools:

```bash
python3 tools/publishing/fetch_pmtiles_cli.py            # official go-pmtiles for tests
Q2VT_PMTILES_CLI=~/.cache/q2vt/go-pmtiles-1.28.0/pmtiles pytest tests/unit/test_publishing_pmtiles.py

pip install --target /tmp/moto_env "moto[server]"        # dev only
Q2VT_MOTO_PATH=/tmp/moto_env Q2VT_MOTO_PYTHON=python3.11 pytest tests/unit/test_publishing_s3_moto.py
```

Live R2 tests are deliberately not automated: they need an explicitly authorised sandbox
bucket/prefix and credentials, and must never run from untrusted pull requests.
