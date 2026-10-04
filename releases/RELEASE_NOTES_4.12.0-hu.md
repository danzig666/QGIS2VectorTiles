# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.12.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.12.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.12.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.12.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### A telekinfó nyomtatása a telekre nagyít (generikus 4.12.0)
Eddig a telekinfó *Nyomtatás* gombja a képernyő nagyítását használta. Mostantól a telekre nagyít:
a teljes telek kitölti a nyomtatott térképet, és kis szegély mutatja a szomszédos telkek szélét.
A legközelebbi szabványos méretarányt választja, amelyen még az egész telek látszik. Új
méretarányok: 1:100 és 1:200. Az *Eszközök* fülön választott nyomtatási méretarány továbbra is
elsőbbséget kap.

### Raszteres rétegek az export-gyorsítótárból (generikus 4.12.0)
A raszteres rétegek (ortofotó, szkennelt tervek, DEM) kirajzolása volt az export leglassabb része,
és minden exportkor újra lefutott. Mostantól a réteget a gyorsítótárból használja újra, ha ezek
nem változtak:
- a raszterfájl és kísérőfájljai (`.aux.xml`, piramisok, georeferáló fájlok);
- a réteg stílusa a QGIS-ben;
- a kiterjedés és a nagyítási szintek;
- a képbeállítások.

Ha ezek közül bármelyik változik, a réteg újra elkészül. A webszolgáltatásból vagy adatbázisból
származó rasztereket mindig újra kirajzolja.

### Raszteres csempék több processzormagon (generikus 4.12.0)
A raszteres csempéket mostantól több processzormag rajzolja és tömöríti egyszerre: 4 magon
2,4-szer gyorsabb. A csempék bájtra pontosan ugyanazok, mint eddig.
