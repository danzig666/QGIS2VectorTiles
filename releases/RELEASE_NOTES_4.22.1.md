**QWebMap 4.22.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Export on a Windows network drive
When the output folder was on a network share (a mapped drive such as `Z:\` or a `\\server\share` path), the export stopped while packaging the vector tiles with `Q2VT_PUB_MBTILES_INVALID: invalid uri authority: <server name>`. The tile database is now opened in a way that works on network shares too.

| Run | Result |
|---|---|
| `pytest tests/unit/test_publishing_*.py tests/integration/test_publishing_pipeline.py tests/browser/test_static_package.py` | 157 passed, 5 skipped (a new test reproduces the error, then opens and packages the file) |
