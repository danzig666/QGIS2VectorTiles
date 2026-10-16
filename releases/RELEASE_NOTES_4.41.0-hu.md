# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.41.0)](https://github.com/danzig666/QWebMap/releases/tag/v4.41.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.41.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.41.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Új
- **Címkeárnyék (Shadow):** a felület- és vonalcímkék QGIS-árnyéka megjelenik a webes térképen. A szöveg (vagy, ha az árnyék a puffer alatt van, a puffer) lágy másolata kerül alá, az árnyék színével, átlátszatlanságával, eltolásával és szögével. A néző a címkével együtt helyezi el, így mindig pontosan alatta van. Az elmosás legfeljebb kb. egy pixel (a böngészőben a nagyobb körvonal dobozzá válik); az árnyék méretezése (Scale) és a címkével együtt forduló eltolás nem követett.
- A pontcímkék és a háttéralakzatok (Background) árnyéka nem rajzolódik ki, de mostantól szerepel a hűségjelentésben (`Q2VT_LABEL_SHADOW`), és nem marad ki szó nélkül.
