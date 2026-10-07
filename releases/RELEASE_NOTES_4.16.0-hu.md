# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.16.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.16.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.16.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.16.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### A mintázatok pontosan ott kezdődnek, ahol a QGIS-ben (generikus 4.16.0)
Az **Elemhez igazított** mintázatok („Align pattern to: Feature”, a QGIS alapbeállítása) eddig a
térképhez voltak rögzítve, így a vonalkázás, pontmintázat vagy kép a minta lépésközének egy részével
eltolódhatott. Mostantól pixelre ott kezdődnek, ahol a QGIS-ben:

- a **vonalkázás, pontmintázat és SVG-kitöltés** minden elem befoglaló téglalapjának bal alsó sarkánál;
- a **raszterkép-kitöltés** („Coordinate mode: Object”) minden elemrész bal felső sarkánál; ha a rész
  messze kilóg a nézetből, a QGIS-hez hasonlóan a nézet szélétől (+10 %).

A **Nézethez** igazított mintázatok már eddig is a térképnézet sarkától indultak (4.14.1).

Minden exportált poligon magával viszi a mintázat kezdőpontját (két szám), a webtérkép innen rajzolja a
mintát. Az export és a térkép érezhetően nem lassul, a mintázatos rétegek csempéi kicsit nőnek.

### Ismert eltérések
- Elforgatott vagy döntött térképen a kezdőpont megmarad, de a QGIS pixelre kerekítése és nézetre
  vágása nélkül.
- Az elforgatott SVG-kitöltések csempéit, ha a szögükben nem ismétlődnek hézagmentesen, kissé igazítjuk
  (a fidelity jelentés jelzi), így az elem sarkától távol a minta elcsúszhat a QGIS-étől.
