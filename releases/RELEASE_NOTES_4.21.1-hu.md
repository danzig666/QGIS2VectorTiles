# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.21.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.21.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.21.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.21.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Gyorsabb újraexportálás méretarány-tartomány változása után (generikus 4.21.1)
A 4.15 óta ha megváltozott, mikor látszik egy réteg vagy a feliratai (méretarány-tartomány, vagy a
webtérkép saját látható méretarányai), a következő export a réteg összes adatát újra elkészítette, pedig
maga az adat ugyanaz – csak a neve változik. Az export-gyorsítótár ismét újrahasznosítja. A színátmenetes
kitöltések továbbra is újra készülnek, mert a színsávjaik az első nagyítási szinttől függenek.
