**QWebMap 4.22.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Embed the web map in another web page
- **Copy embed code** (Publish window, next to *Copy link*) copies a ready `<iframe>` for the published map. Paste it into the HTML of another page (a municipality's site, a blog, a CMS "HTML block"). It works before the first upload too, from the destination's public address. A local-only destination can't be embedded, and the window says so.
- The embedded map opens in a **compact view**: a slimmer header, the side panel closed (one tap opens it), and an **Open the full map ↗** link that opens the same view in a new tab.
- Scrolling the page doesn't get stuck on the map: zoom with **Ctrl + scroll** (⌘ on Mac) or **two fingers** on a phone. A short hint says so in the map's language.
- The embed code keeps a shared view: copy it after opening a view link and the embedded map starts there. The default size is full width × 600 px; change `height` in the code if you like.

| Run | Result |
|---|---|
| Browser tests (viewer features, parcel, PMTiles transport, smoke, static package, Street View) | 29 passed (map framed in a host page: compact mode, panel closed, plain scrolling leaves the map's zoom unchanged) |
| `pytest tests/integration/test_publish_dialog*.py tests/unit/test_publishing_web_builder.py` | 35 passed |
