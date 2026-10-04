<br>

<div align="center">

<img width="70" alt="icon" src="https://github.com/user-attachments/assets/e1f0e64b-6850-4ae5-b3c0-2ce2fca5580e" />

# QWebMap

[![🐞 Issues](https://img.shields.io/badge/Issues-🐞-98b023?style=for-the-badge)](https://github.com/danzig666/QGIS2VectorTiles/issues)
[![📦 Releases](https://img.shields.io/badge/Releases-📦-black?style=for-the-badge)](https://github.com/danzig666/QGIS2VectorTiles/releases)
[![🌐 Upstream](https://img.shields.io/badge/Upstream-🌐-black?style=for-the-badge)](https://github.com/GallPeters/QGIS2VectorTiles)
[![📜 License](https://img.shields.io/badge/License-📜-98b023?style=for-the-badge)](https://www.gnu.org/licenses/old-licenses/gpl-2.0-standalone.html)

**Publish your QGIS project to the web in one click: a fast web map that looks like it does in QGIS.**

> **QWebMap** started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles)
> by Jossef Kanter and grew into a complete web map publisher. Download builds from
> [Releases](https://github.com/danzig666/QGIS2VectorTiles/releases) and report issues
> [here](https://github.com/danzig666/QGIS2VectorTiles/issues). It installs as **QWebMap**
> next to the official QGIS2VectorTiles plugin.
>
> **Updating from 4.8.1 or older** (then called *QGIS2VectorTiles (fork)*): install QWebMap, then
> uninstall *QGIS2VectorTiles (fork)* in *Plugins → Manage and Install Plugins*. Settings saved
> in your projects, the window position and saved keys carry over.

  _- No internet connection or third-party installation required -_

<kbd>
<img width="670" alt="QGIS2VectorTilesDemo" src="https://github.com/user-attachments/assets/98da33f5-7513-4f84-a8a7-4d0750d7db63" />
</kbd>

</div>

<br>

## Export fidelity

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

## Publish Web Map

*Web → QWebMap → Publish Web Map…* (also on the Web toolbar) opens a
window that turns the project into a public, self-contained web map:

- The map stays **vector tiles** (MVT). They are packed into one **PMTiles** archive that
  the browser reads with HTTP byte ranges, so you need no tile server, database or Docker.
- The viewer has a layer tree (including hidden layers you chose to publish), opacity,
  legend with QGIS-rendered symbols, label switch, filters, popups, search, links to
  features, permalinks, measuring (with EOV coordinates) and a phone layout. It is
  available in Hungarian and English.
- Every setting is **saved in the project**, but credentials are not. Keys stay in the
  QGIS authentication database.
- *Export locally* and *Preview* work offline. *Publish* uploads an immutable release to
  **Cloudflare R2** (or other S3-compatible storage; a step-by-step R2 guide is built into the
  window) and checks it through your public domain. Only then does it switch the stable link, so the previous map stays online if
  anything fails. *History* rolls back to an earlier release or removes old ones.
- Only the fields you approve become public; every tile is checked before upload.
- **Raster layers** (orthophotos, scanned plans, rendered DEMs) are drawn by QGIS into their own
  image tile archive (PNG, WebP or JPEG); vector layers always stay vector tiles.
- An optional **OpenStreetMap vector basemap** (Protomaps) is bundled into the release in
  several styles (light, dark, grayscale…); visitors switch it or turn it off.
- **QGIS map themes** can select the published layers and become one-click views in the web
  map; layers and groups can be marked as *always shown*; many rows can be changed at once.
- **Parcel report**: clicking a parcel shows its area, its parts cut by the zoning and the
  regulation lines, its zones and every restriction touching it, with legend graphics
  (computed in QGIS from the exact geometry when exporting).
- A modern viewer: floating panels on desktop, a bottom sheet on phones, light and dark
  appearance, your accent colour.
- **Fast re-exports**: unchanged layers are reused from earlier exports, and unchanged files are
  copied inside the bucket instead of uploaded again (a 3-minute plan re-exports in ~15 s).

The Processing algorithm is unchanged; it gained an optional *Tile archive format*
(MBTiles, PMTiles or both).

Documentation: [`docs/publishing/`](docs/publishing/), covering
[hosting and R2 setup](docs/publishing/HOSTING.md),
[security](docs/publishing/SECURITY.md),
[architecture](docs/publishing/ARCHITECTURE.md),
[tests](docs/publishing/TESTING.md) and
[implementation status](docs/publishing/IMPLEMENTATION_STATUS.md).
