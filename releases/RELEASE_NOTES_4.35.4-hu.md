# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.35.4)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.35.4)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.35.4**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.35.4 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítás (generikus 4.35.4)
- **Egy poligon színével elárasztott csempék:** egy, a projekt vetületében (itt EOV) érvényes poligon a Web Mercatorba vetítve önmetszővé válhat, ahol két éle majdnem összeér. A csempeíró ekkor a metsző gyűrűt egész csempékre kiterjedő, a poligon színével kitöltött foltokká vágta — csak egyes nagyítási szinteken. A poligonokat most a vetítés előtt és után is javítja.
