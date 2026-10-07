# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.18.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.18.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.18.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.18.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### A raszterrétegek alapformátuma a WebP (generikus 4.18.0)
A publikációhoz adott raszterrétegek mostantól **WebP** képekként kerülnek ki: jóval kisebbek a PNG-nél,
és az átlátszóság is megmarad. A már beállított rétegek megtartják a mentett formátumukat; rétegenként a
Publish ablakban (**Image format**) változtatható. Ha a QGIS nem tud WebP képet írni, a réteg PNG-ként
kerül ki, és az export jelzi ezt (eddig hibával leállt).

### A látható rétegek publikálása
A Publish ablak térkép lapján új gomb van: **Publish the visible layers**. Pontosan azokat a rétegeket
publikálja, amelyek a QGIS Rétegek paneljén most láthatók (a webtérkép megnyitásakor is látszanak), a
rejtetteket pedig kiveszi a publikálásból. A kikapcsolt csoportban lévő réteg rejtettnek számít. A
térképtéma **Publish its layers** gombja továbbra is csak hozzáad.
