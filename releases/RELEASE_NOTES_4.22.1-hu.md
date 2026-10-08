# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.22.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.22.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.22.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.22.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Exportálás Windows hálózati meghajtóra (generikus 4.22.1)
Ha a kimeneti mappa hálózati megosztáson volt (csatlakoztatott meghajtó, pl. `Z:\`, vagy
`\\szerver\megosztás` útvonal), az export a vektorcsempék csomagolásánál leállt ezzel a hibával:
`Q2VT_PUB_MBTILES_INVALID: invalid uri authority: <szervernév>`. A csempeadatbázist a bővítmény most
hálózati megosztáson is meg tudja nyitni.
