# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.48.1)](https://github.com/danzig666/QWebMap/releases/tag/v4.48.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.48.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.48.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítva (a generikus 4.48.1-ből)
- A poligonréteg körvonala már nem rajzol vonalat az exportált terület (extent) szélén, ahol az elvágja a poligonokat (4.47.0 óta).
- A vékony hajszálvonalak az áttekintő térképen (overview) már nem fele olyan vékonyak nagy felbontású képernyőn.
- A térképi egységben méretezett, adatfüggő megjelenésű jelek minden zoomszinten megtartják a saját képüket.
- Ha egy körvonal vastagabb lesz, a gyorsítótárazott csempék újrakészülnek (ettől függ, kimaradhatnak-e a kis elemek).
