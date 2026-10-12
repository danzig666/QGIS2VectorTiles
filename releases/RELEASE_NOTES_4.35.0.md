**QWebMap 4.35.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Views that fit, a closed map panel
- **Map theme views that do not fit:** with many map themes offered as views, the view buttons of the web map ran off the right edge of the screen. When they do not fit in one row (also after a window resize or on a phone), the web map shows them as a drop-down instead.
- **Map panel closed at start:** a new option on the Interaction tab of the Publish window (off by default). The web map opens with its panel (layers, legend, tools) closed, so more of the map is on screen; the menu button opens it.
- **Publish its layers** on a map theme now also ticks the theme in *Map themes offered as views*.

| Run | Result |
|---|---|
| Publish window tests | 25 passed |
| Web map browser tests (views, basemap and themes, features, extras, smoke) | all passed |
