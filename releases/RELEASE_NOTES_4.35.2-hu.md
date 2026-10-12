# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.35.2)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.35.2)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.35.2**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.35.2 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítás (generikus 4.35.2)
- **Réteg nélküli jelmagyarázat-elem** — egy réteg, amelyet a QGIS nem tudott létrehozni (pl. hiányzó forrásra épülő virtuális réteg) — leállította az exportot („'NoneType' object has no attribute 'id'”). Az ilyen elemeket most kihagyja.
