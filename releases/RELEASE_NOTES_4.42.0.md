**QWebMap 4.42.0**

QWebMap started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin, and it installs next to it.

This is the **generic** edition — the one to download. (A special Hungarian zoning-plan edition, not for general use, is released separately with a `-hu` tag.)


### Added
- **Point label shadows:** point labels with a QGIS shadow now get one too. MapLibre places point labels itself, so the viewer switches each shadow on only where its label is actually shown (a label hidden because it collides with another leaves no shadow behind); the shadow appears a moment after its label.

### Changed
- **Local exports without versions (new default):** *Export locally* writes the web map straight into `<output folder>/<name>/` as one site with its own `index.html`, ready to copy to any web server. Each export replaces the previous one; only files an earlier export wrote (listed in `.q2vt-files.json`) are removed, so your own files in the folder stay.
- To keep each export as a version in `releases/` with rollback, as before, tick **Keep earlier versions in the local folder** (*Output* tab). Publishing to R2/S3 and SSH is unchanged.
- An output folder that already has `releases/` from earlier versioned exports keeps them; the log says so, and the folder can be deleted if those versions are not needed.
