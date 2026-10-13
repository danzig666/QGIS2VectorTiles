# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.36.6)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.36.6)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.36.6**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.36.6 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítások (generikus 4.36.6)
- **Téves „The map style is invalid” hiba:** a 4.36.5 ezt a hibát mutatta olyan térképnél, amelynek egyik rétege üres az export területén. A MapLibre az ilyen réteg hiányzó forrásrétegét hibaként jelzi, de a stílust elfogadja és a térképet megrajzolja. Most csak az a hiba állítja le a térképet, amely azelőtt történik, hogy a MapLibre elfogadta a stílust; minden későbbi csak figyelmeztetés, mint korábban. A 4.36.5-tel közzétett térképek a 4.36.6-tal újra közzétéve ismét működnek.
