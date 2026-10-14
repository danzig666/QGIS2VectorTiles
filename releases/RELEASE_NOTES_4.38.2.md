**QWebMap 4.38.2**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Web map
- **Even thinner hairlines**: with *Thin hairlines* on (Publish window, *Output*, on by default), solid zero-width lines are now 0.35 device pixel wide (0.6 when zoomed far in) at 70 % of their opacity. Measured against QGIS's hairline, that is about a third of its ink (4.38.1: a bit over half), on standard and on 4K screens alike. They stay visible as a light grey line. Republish for it to take effect.
