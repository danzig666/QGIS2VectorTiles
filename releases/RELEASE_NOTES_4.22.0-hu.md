# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.22.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.22.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.22.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.22.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Webtérkép beágyazása más weboldalba (generikus 4.22.0)
- **Copy embed code** (Publish ablak, a *Copy link* mellett): kész `<iframe>` kódot másol a közzétett
  térképhez. Illessze be egy másik oldal HTML-jébe (pl. az önkormányzat honlapján egy „HTML blokkba”).
  Az első feltöltés előtt is működik a cél nyilvános címéből. Csak helyi célt nem lehet beágyazni, ezt az
  ablak jelzi.
- A beágyazott térkép **kompakt nézetben** nyílik: keskenyebb fejléc, csukott oldalpanel (egy
  koppintással nyílik), és **Teljes térkép ↗** link, amely ugyanazt a nézetet új lapon nyitja meg.
- Az oldal görgetése nem akad el a térképen: nagyítás **Ctrl + görgetéssel** (Macen ⌘), telefonon
  **két ujjal**. Rövid magyar nyelvű tipp jelzi ezt.
- A kód megtartja a megosztott nézetet; az alapméret teljes szélesség × 600 px (a `height` átírható).
