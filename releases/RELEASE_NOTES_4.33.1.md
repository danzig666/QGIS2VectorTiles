**QWebMap 4.33.1**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)

### Fixes: line labels
- **Curved line labels follow the road.** A label with QGIS's *Curved* placement (shown once per line) was a straight label turned to the line at its middle, so on a bending street it ran off the street. The web map now lays it along the line under it, letter by letter, as QGIS does, at every zoom, when zoomed in past the last tile zoom too. It goes round a bend as far as QGIS's curved angle limit allows, and moves along the line off a sharper corner.
- **Word spacing.** Labels with QGIS word spacing (the *Spacing → Word* setting) had normal spaces on the web, so the words were set tight. The spaces are now about as wide as in QGIS.
- **Line labels above or below their line** (*Above line* / *Below line*) sat across the line in the web map. They now stay on their side.

| Run | Result |
|---|---|
| Viewer, label, end-to-end and browser comparison tests | all passed |
| A street layer with curved, word-spaced labels, QGIS vs web at zooms 17.5–21.5 | labels bend with the streets, with QGIS's word gaps |
