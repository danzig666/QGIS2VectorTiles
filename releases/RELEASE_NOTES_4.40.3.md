**QWebMap 4.40.3**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)


### Fixed
- **Unsupported renderers are reported:** a layer whose renderer cannot be drawn on the web map (2.5D, embedded symbols from KML/DWG and similar files, plugin renderers) used to be left out without a word. It is now a warning in the fidelity report (`Q2VT_UNSUPPORTED_RENDERER`), with the layer's name, so *Strict* mode stops on it too.
- README: a section on the styling that does not work (yet).
