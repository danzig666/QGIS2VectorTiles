# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.38.7)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.38.7)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.38.7**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.38.7 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítva
- **Telekinfó nyomtatása:** az első oldalon a térkép, mellette a telek adatai és a telekrészek. A korlátozások a következő oldalon kezdődnek, utána minden kinyitott „teljes előírások” szöveg új oldalon, a teljes lapszélességben, két hasábban (eddig minden az első oldal utáni lapokon is a térkép melletti keskeny oszlopban folyt, a lap nagy része üresen maradt). A címek nem szakadnak el a szövegüktől, a korlátozás-kártyák nem törnek ketté. A becsukott előírás-szövegek nem nyomtatódnak.
