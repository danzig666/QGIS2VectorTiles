<br>

<div align="center">

<img width="70" alt="icon" src="https://github.com/user-attachments/assets/e1f0e64b-6850-4ae5-b3c0-2ce2fca5580e" />

# QGIS2VectorTiles

[![🐞 Issues](https://img.shields.io/badge/Issues-🐞-98b023?style=for-the-badge)](https://github.com/danzig666/QGIS2VectorTiles/issues)
[![📦 Releases](https://img.shields.io/badge/Releases-📦-black?style=for-the-badge)](https://github.com/danzig666/QGIS2VectorTiles/releases)
[![🌐 Website](https://img.shields.io/badge/Website-🌐-black?style=for-the-badge)](https://gallpeters.github.io/QGIS2VectorTiles/)
[![📜 License](https://img.shields.io/badge/License-📜-98b023?style=for-the-badge)](https://www.gnu.org/licenses/old-licenses/gpl-2.0-standalone.html)

**Turn QGIS projects into fast, lightweight, client-rendered web maps in a single run.**

> **This is a fork** of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles)
> by Jossef Kanter, focused on making the web map match the QGIS styling. Download fork builds
> from [Releases](https://github.com/danzig666/QGIS2VectorTiles/releases) and report fork issues
> [here](https://github.com/danzig666/QGIS2VectorTiles/issues). It installs as
> **QGIS2VectorTiles (fork)** next to the official plugin.

  _- No internet connection or third-party installation required -_

<kbd>
<img width="670" alt="QGIS2VectorTilesDemo" src="https://github.com/user-attachments/assets/98da33f5-7513-4f84-a8a7-4d0750d7db63" />
</kbd>

</div>

<br>

## Export fidelity (this fork)

Every export now writes `fidelity_report.json` and `fidelity_report.html` next to the
tiles. Each entry names a stable code (`Q2VT_*`), the layer/rule/symbol layer, the export
strategy used and a suggested fix, so missing hatches or unsupported symbols are visible
without reading the log.

The Processing dialog has two new options:

- **Fidelity Mode**: *Vector-first* (default) exports what it can and reports
  approximations; *Strict* fails before anything is published if any component is
  unsupported or approximated.
- **Beyond Maximum Zoom**: keep showing the last generated tiles (overzoom) or hide all
  layers above the export's maximum zoom.

Developer documentation: [`docs/fidelity/`](docs/fidelity/) — baseline and confirmed
defects, how to run the three test levels, the generated symbol compatibility table, and
the status of each item of the fidelity plan.
