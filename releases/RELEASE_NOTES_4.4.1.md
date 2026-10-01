**QGIS2VectorTiles 4.4.1 (fork)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin. It installs as **QGIS2VectorTiles (fork)** next to the official plugin.

This is the **generic** edition. A Hungarian zoning-plan edition (HÉSZ, `szab_ov`) is released from the `hu-hesz` branch as `QGIS2VectorTilesFork-4.4.1-hu.zip`; see `docs/BRANCHES.md`.

### Changes
- **Presets**: editions can ship presets that fill the settings from known layer and field conventions. A *Preset…* button in the Publish window lists them. The generic edition ships none, so the button is hidden.
- **Parcel report notice**: an empty notice now shows the viewer's standard text in the viewer's language (English or Hungarian) instead of a fixed Hungarian sentence.
- Releases and builds support editions (`variant.json`). A workflow merges every change of the generic branch into the edition branches automatically.
