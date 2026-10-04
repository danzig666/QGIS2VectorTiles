**QWebMap 4.13.3**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Street View: why the blue lines are missing
The blue Street View lines come from Google's **Map Tiles API**; the photos come from the **Maps JavaScript API**. If your key may use only the second one, the photos worked but the lines silently did not appear. The viewer now says so ("The blue Street View lines cannot be shown…") with the reason, and tapping still opens the nearest Street View.

To get the lines, in Google Cloud (same project as the key):
1. *APIs & Services → Library → Map Tiles API → Enable* (billing must be enabled on the project).
2. *Credentials → your key → API restrictions*: if the key is restricted, add **Map Tiles API** next to Maps JavaScript API.

A local preview (`http://127.0.0.1…`) works too, as long as the key's website restrictions allow it (or the key has none while you test).

| Run | Result |
|---|---|
| `pytest tests/browser/test_web_viewer_streetview.py` | 6 passed (new: a refusal from the Map Tiles API is shown with its reason; a tap still opens the panorama) |
