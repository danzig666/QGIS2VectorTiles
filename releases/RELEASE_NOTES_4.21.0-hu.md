# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.21.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.21.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.21.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.21.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Raszterrészletesség: egyetlen választás, méterben (generikus 4.21.0)
A raszterbeállításokban három elem szólt egyetlen kérdésről – milyen éles a kép a webtérképen: *Maximum
zoom*, *Match the image's resolution* és *Sharp on high-resolution screens*. Most egyetlen választás
lett belőlük: **Sharpest detail** (legfinomabb részlet), egy képpont terepi méreteként:

- **Like the image** (ajánlott, új raszterrétegeknél ez az alap): olyan éles, mint maga a kép,
  pl. *0,40 m pixelenként*;
- **Like the map**: a publikáció legnagyobb csempe-nagyítása;
- **egy pixelméret**: pl. *0,80 m*, *1,6 m*, *3,2 m* – a durvább kisebb exportot ad. A képnél egy
  lépéssel finomabb is választható, „no more detail” (több részlet nélkül) jelöléssel.

Alatta a becslés összeveti a választást a képpel (pl. *„4× coarser than the image”*), és megadja a
csempék számát. A *Minimum zoom* neve mostantól *Shown from zoom*.

A korábban beállított rétegek megtartják a részletességüket: amelyiknél be volt kapcsolva a *Sharp on
high-resolution screens*, ugyanazt a részletességet egy nagyítási szinttel feljebb kapja (az opció
kétszer szélesebb képeket készített, ami egy szinttel több nagyításnak felel meg).
