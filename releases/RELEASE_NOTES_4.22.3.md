**QWebMap 4.22.3**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### The export cache is no longer reset by other plugins
The cache treated every project variable as an export setting. A plugin that keeps its own project variables up to date (e.g. a time tracker: `@time_tracker_total_minutes`) therefore made every export redo everything ("Redone (export settings changed (@time_tracker_…))"). Now only the variables your styles and labels actually use count; changing one of those still redoes the layers that use it.

### Version in the window title
The Publish window shows the plugin version: "Publish Web Map — QWebMap 4.22.3".

Note: the first export after this update redoes everything once (the plugin's own code changed); from the second export on, unchanged layers are reused.

| Run | Result |
|---|---|
| `pytest tests/integration/test_export_cache.py tests/unit/test_export_cache.py` | 13 passed (a new test: a time-tracker variable changes → all reused; a label's variable changes → that layer redone) |
| `pytest tests/integration/test_publish_dialog*.py tests/integration/test_publishing_pipeline.py` | 33 passed |
