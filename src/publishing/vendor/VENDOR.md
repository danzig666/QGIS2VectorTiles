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
