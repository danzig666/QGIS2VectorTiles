# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.36.7)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.36.7)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.36.7**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.36.7 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítások (generikus 4.36.7)
- **Nagy, térképi egységben megadott jelek nagyításkor:** a térképi egységben megadott egyszerű jel — például egy poligon középpontjára (Centroid fill) tett 920 m-es szaggatott kör, amely védőtávolságot mutat — egyetlen kép volt: a böngésző a képet csak a középpontját tartalmazó csempéből rajzolja, ezért a kör eltűnt, amint a középpontja kikerült a képernyőről, az utolsó előkészített nagyítás fölött pedig felnagyítva (elmosódva, vastag körvonallal, megnyúlt szaggatással) látszott. Attól a nagyítástól, ahol az ilyen jel 128 px széles, most a valódi alakjaként rajzolódik a pont körül: minden nagyításon pontos, a körvonala megtartja a vastagságát és a szaggatását, és mindenhol látszik, ahol a kör a képernyőn van. Ez alatt a nagyítás alatt kép marad, mint eddig. (A kör szélére nagyítva maga a QGIS nem rajzolja ki, mert az objektum kívül esik a nézeten; a webtérkép továbbra is mutatja.)
