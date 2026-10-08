# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.23.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.23.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.23.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.23.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Az exportot követő folyamatjelző (generikus 4.23.0)
A csempegenerálás általában az export leghosszabb része (nagy projekten 15 perc), a folyamatjelző mégis
85%-ra ugrott az elején, és ott is maradt; a későbbi lépések pedig nulláról indították újra.
- Minden lépésnek saját része van a sávon, és a sáv csak előre halad. A csempegenerálás kapja a legnagyobb részt.
- A sáv a csempegenerálás **közben** is halad. A csempéző eszköz nem jelez előrehaladást, ezért a
  rétegek adatmérete és nagyítási tartománya alapján becsüljük, és a már elkészült rétegek sebessége
  pontosítja. A naplóban: „about N% done”.
- A legnagyobb rétegek indulnak először, így egy nagy réteg sem marad a végén egyedül.

### Export-gyorsítótár napló frissítés után
Bővítményfrissítés után a napló csak ezt írja: „plugin, QGIS or GDAL updated”, és nem sorolja fel a
régebbi verzió beállításait változásként (pl. olyan projektváltozókat, amelyeket már nem tárol).
