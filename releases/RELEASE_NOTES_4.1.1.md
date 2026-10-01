**QGIS2VectorTiles 4.1.1 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

Install it in QGIS with *Plugins → Manage and Install Plugins → Install from ZIP* and choose `QGIS2VectorTilesFork-4.1.1.zip`.

### Fixes since 4.1
- Dash patterns with a zero-length dash (for example `6;4;6;4;6;4;6;4;0;20`) are exported as their dashes again. Text markers placed in the gaps no longer overlap the dashes, as in *Felszín alatti vízbázis védőidom*. QGIS draws nothing for a zero-length dash, so it is merged into the gap before it.
