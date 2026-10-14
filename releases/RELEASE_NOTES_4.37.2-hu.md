# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.37.2)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.37.2)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.37.2**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.37.2 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Publish ablak (generikus 4.37.2)
- **A Viewer opciók kihasználják az ablak szélességét:** az Interaction fül Viewer jelölőnégyzetei eddig mindig 3 oszlopban voltak, így széles ablakban is 6–7 sort foglaltak. Most annyi oszlopba rendeződnek, amennyi elfér (minden oszlop a leghosszabb opciójához igazodik), a Google Maps API kulcs mezője az utolsó után jön: széles ablakban ez 4 sor, így több hely marad az alatta lévő réteglistának és mezőtáblának. Keskenyebb ablakban kevesebb az oszlop, az ablak továbbra is szabadon átméretezhető.
