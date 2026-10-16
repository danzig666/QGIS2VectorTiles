**QWebMap 4.41.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)


### Added
- **Label shadows:** polygon and line labels with a QGIS shadow get one on the web map: a soft copy of the text (or of its buffer, when the shadow is under the buffer) in the shadow's colour and opacity, at its offset and angle. The viewer places the shadow together with its label, so it always lands right under it. The blur is capped at about a pixel (a wider browser halo turns into a box), and a shadow's scale and an offset that turns with the label are not reproduced.
- Point labels' shadows and shadows of background shapes are not drawn; they are now listed in the fidelity report (`Q2VT_LABEL_SHADOW`) instead of being left out silently.
