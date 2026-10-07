# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.14.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.14.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.14.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.14.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### A nézethez igazított mintázatok (generikus 4.14.1)
A QGIS-ben a vonalkázás, a pontmintázat és az SVG-kitöltés beállítása: **Minta igazítása: Elem / Nézet**
(Align pattern to: Feature / Viewport; raszterkép-kitöltésnél: Coordinate mode: Object / Viewport).
*Nézet* esetén a minta a térképnézet sarkától indul, és mozgatáskor a helyén marad. A webtérkép most
ugyanígy rajzolja, így ezek a mintázatok eltolásra is pontosan egyeznek a QGIS-szel.

*Elem* (a QGIS alapbeállítása) esetén a QGIS minden elem sarkától indítja a mintát; a webtérkép ezeket
a térképhez rögzíti, így a minta lépésközének egy részével eltolódhat (mérete és kinézete azonos).
