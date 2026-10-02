# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.5.6)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.5.6)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.5.6 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.5.6 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Újdonságok (generikus 4.5.6): Publish ablak
- **Kiterjedés rétegből:** a Kiterjedés beállításnál rétegválasztó lista van. Egy réteget választva
  a publikált terület a réteg kiterjedése (minden exportnál újraszámolva, így követi a réteg
  változásait); a **Map canvas** gomb a QGIS-ben éppen látható területet rögzíti. A terület méretét
  km-ben és földrajzi koordinátákkal mutatja a levágott EPSG:3857 számok helyett.
- **Interaction fül:** a bal oldali rétegjegyzék újra kényelmes; a hosszú réteg­nevek tördelődnek.
- **Settings file…** (a *Save settings* mellett): a beállítások fájlba menthetők (`.q2vt.json`)
  és onnan betölthetők, pl. egy másik projektbe; a rétegeket azonosító, ennek hiányában név
  szerint párosítja. Jelszó vagy kulcs soha nem kerül a fájlba.
