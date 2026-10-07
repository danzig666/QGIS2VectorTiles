# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.20.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.20.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.20.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.20.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Raszterfelbontás: látható és illeszthető (generikus 4.20.0)
Egy raszterréteg élessége a webtérképen csak a **Maximum zoom** beállításától függ: minden nagyítási
szint felezi a pixel méretét a terepen (é. sz. 48°-on: 16-os szint = 1,6 m, 17 = 0,8 m, 18 = 0,4 m). A
maximum fölött a böngésző csak felnagyítja az utolsó képeket. Ez eddig nehezen volt látható, ezért a
Publish ablak raszterbeállításai most kiírják:

- **a kép saját felbontását** és **azt, amit a maximum zoom kiad**, pl. *„Image: 0.40 m per pixel.
  Published: 1.60 m per pixel at zoom 16 – 4× coarser than the image; zoom 18 would show all of it.”*
  A képnél finomabb maximum zoomot is jelzi (nagyobb export, több részlet nélkül);
- új beállítás: **Match the image's resolution** – a maximum zoom az a legkisebb szint lesz, amely olyan
  éles, mint a kép (eggyel kisebb a *Sharp on high-resolution screens* opcióval, mert annak csempéiben
  kétszer annyi pixel van). Az online szolgáltatásoknak (WMS, XYZ…) nincs saját felbontásuk, ott kézzel
  kell beállítani.

A felbontást a terepen mérjük, így bármely vetületben (EOV, Web Mercator, fok) helyes.
