# Vendored third-party code (shipped in the plugin zip)

Runtime dependencies are vendored so the plugin needs no `pip install`, npm or network
access at runtime. Each entry is an unmodified copy of a released upstream file set.

## pmtiles (Python) 3.8.1 — BSD-3-Clause

* Upstream: https://github.com/protomaps/PMTiles (`python/pmtiles`), PyPI `pmtiles==3.8.1`
  (wheel `pmtiles-3.8.1-py3-none-any.whl`, SHA-256
  `718561bb21f8c7dd5464fdcc3b9ad0e7b1c917be60ddfdf9a5ab56b8c67f7bde`).
* Files copied unmodified: `__init__.py`, `tile.py`, `reader.py`, `writer.py`
  (`convert.py` is not shipped; `publishing/pmtiles_builder.py` is the fork's adapter).
* License: `pmtiles/LICENSE`.

| File | SHA-256 |
|---|---|
| `pmtiles/__init__.py` | `3e97c43913c07fabe6659d67bf6500a0d44901f31469d8db474366a7e06d739c` |
| `pmtiles/reader.py` | `a0f12fc75f5a6fc4950d90ecdec976f07c6366e0eaf28232a1dd413935b55b0e` |
| `pmtiles/tile.py` | `3b72e470e5d953fa588d03582422b0daadbfc82273df19fe0c7c1b78f0f4a558` |
| `pmtiles/writer.py` | `6a4dba52163bc93c0dae471a195835f19ece4f337bd6ec64d0c13e52cc6a40fb` |

The adapter subclasses `writer.Writer` only to deduplicate tile payloads by SHA-256
(upstream keys on Python's 64-bit `hash()` without comparing bytes) and to place its
temporary file next to the output; headers, directories and metadata are written by the
upstream code.

## S3 SDK — `s3/` (used only when QGIS's Python has no boto3)

Unpacked from pinned PyPI wheels by `tools/publishing/vendor_s3.py`, which checks
each wheel against `tools/publishing/vendor_s3.sha256`. botocore and boto3 data are
trimmed to the S3 service, endpoints, partitions, default configuration and retry
rules. Tests and `.dist-info` are not shipped; licences and notices are in
`s3/licenses/`.

| Package | Version | License |
|---|---|---|
| boto3 | 1.43.106 | Apache-2.0 |
| botocore (incl. its `cacert.pem`, MPL-2.0) | 1.43.106 | Apache-2.0 |
| s3transfer | 0.19.2 | Apache-2.0 |
| jmespath | 1.1.0 | MIT |
| python-dateutil | 2.9.0.post0 | Apache-2.0 / BSD-3-Clause |
| six | 1.17.0 | MIT |
| urllib3 | 2.8.0 | MIT |

## Web viewer (`resources/`)

| Component | Version | License | Files |
|---|---|---|---|
| MapLibre GL JS | 6.11.2 (unchanged from the existing viewer, not upgraded) | BSD-3-Clause | `resources/ml_viewer/maplibre-gl*.{mjs,css}`, `MAPLIBRE-LICENSE.txt` |
| pmtiles (JavaScript) | 4.5.0 browser build `dist/pmtiles.js`, unmodified (SHA-256 `caf981bc46f6327ee7e65d5dc964d89d38a69f60edca2bd4c5c890c21b554c6c`) | BSD-3-Clause | `resources/web_viewer/vendor/pmtiles.js`, `PMTILES-LICENSE.txt` |

Every web release copies these licences into `licenses/`. The go-pmtiles CLI that
`tools/publishing/fetch_pmtiles_cli.py` downloads is used by tests only and is not shipped.
