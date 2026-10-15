**QWebMap 4.40.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)


### Added
- **Diagrams:** QGIS pie, histogram, stacked bar and text diagrams are drawn by the browser from each feature's category values, with QGIS's sizes (fixed or interpolated, scaled by area, minimum size), colours, outline, start angle and direction, bar width, spacing and orientation, and the diagrams' scale range and *Show* condition. They sit above the features like in QGIS: over points, over polygon centroids (or inside the polygon) and at the middle of lines. Not reproduced (reported): other data-defined diagram settings, the histogram axis and an *around* placement's distance.
- **Animated markers:** GIF, WebP and animated PNG markers play at their QGIS frame rate. Printing shows the first frame, and so does a visitor's system that asks for reduced motion.
- **Selective masking (approximate):** a label's mask becomes its halo in the map's background colour (the mask's size and opacity); a mask marker becomes its shape in the background colour. QGIS cuts only the chosen symbol layers; the web map covers everything under the mask, which is exact over a plain background. Reported as `Q2VT_MASK_APPROXIMATE`.

### Changed
- The plugin metadata names the author, Ábel Kőszeghy, and credits the original QGIS2VectorTiles plugin by Jossef Kanter.
