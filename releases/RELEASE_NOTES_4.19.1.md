**QWebMap 4.19.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Joined fields, virtual fields and unsaved edits are published
The export read each layer again from its file or database. That copy lacks what exists only in the open project, so — besides temporary layers (fixed in 4.19.0) — these were lost too:

- **Joined fields** (layer properties → Joins), including label positions moved by hand (QGIS keeps them in auxiliary storage, which is a join). Labels or styles using them came out empty, and the export could stop with "No glyphs for font".
- **Virtual fields** (made with the field calculator's "Create virtual field").
- **Unsaved edits** of a layer in edit mode: new, changed or deleted features.

Such layers are now taken from the open project, as QGIS shows them.

| Run | Result |
|---|---|
| `pytest tests/integration/test_publishing_pipeline.py -k "only_in_the_project or temporary"` | 4 passed (new: joined field, virtual field, unsaved edit; all three fail without the fix) |
| `pytest tests/integration/test_publishing_pipeline.py tests/integration/test_export_cache.py tests/integration/test_end_to_end.py tests/integration/test_publishing_parcel_report.py tests/integration/test_publishing_label_always.py` | 39 passed, 1 failed: an export-cache test that fails the same way since 4.16.0 (being looked at separately) |
