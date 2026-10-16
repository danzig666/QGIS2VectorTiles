# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.40.3)](https://github.com/danzig666/QWebMap/releases/tag/v4.40.3)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.40.3**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.40.3 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítva
- **A nem támogatott megjelenítők bekerülnek a jelentésbe:** ha egy réteg megjelenítője (renderer) nem rajzolható ki a webes térképen (2.5D, KML/DWG és hasonló fájlokba ágyazott szimbólumok, bővítmények megjelenítői), a réteg eddig szó nélkül kimaradt. Most figyelmeztetés jelzi a hűségjelentésben (`Q2VT_UNSUPPORTED_RENDERER`) a réteg nevével, így a *Strict* mód is megáll rajta.
- README: új szakasz arról, milyen stílusok nem működnek (még).
