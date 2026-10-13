# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.36.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.36.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.36.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.36.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Zoomed-out load (generikus 4.36.1)
- **A feliratok többet nyomnak:** a webes térképen a feliratok rajzolása a leglassabb — egy csempe minden feliratát elhelyezi és ütközésre vizsgálja, a végül elrejtetteket is. Ha egy csempében egy rétegnek több mint 1500 felirata van, az már önmagában nehéznek számít, így a feliratok javasolt nagyítása beljebb kerül akkor is, ha elférnének; a sebesség-csúszka ezt a határt is skálázza (Very fast: 300, Very slow: 6000). Az ilyen sorokat „Many labels: slow to draw” jelzi.
- **Folyamatjelző:** nagy projektnél a becslés rétegenként mutatja, hol tart, és megszakítható.
