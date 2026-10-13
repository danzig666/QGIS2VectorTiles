# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.36.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.36.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.36.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.36.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Sebesség-csúszka a kizoomolt terheléshez (generikus 4.36.0)
- A *Zoomed-out load…* ablakban (Publish ablak, Map lap) csúszka állítható a **Very slow** (nagyon lassú) és a **Very fast** (nagyon gyors) között. Gyorsabb: a kis elemeket és a zsúfolt feliratokat már közelebbi nagyításoktól javasolja elrejteni, így kizoomolva kevesebbet kell betölteni és rajzolni. Lassabb: kizoomolva többet mutat. A középső (Balanced) a korábbi működés.
- A csúszka mozgatása újra javasolja a nagyításokat; bezoomolva minden réteg teljesen látszik. A beállítást megjegyzi.
