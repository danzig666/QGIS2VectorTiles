# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.42.3)](https://github.com/danzig666/QWebMap/releases/tag/v4.42.3)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.42.3**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.42.3 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítva (a generikus 4.42.3-ból)
- **Online csempés raszterrétegek elmosódtak nagyításkor:** egy XYZ vagy WMTS rétegnek nincs saját képfelbontása, ezért az alapértelmezett *Sharpest detail* a vektorcsempék legnagyobb zoomjára (pl. 16) esett vissza, és fölötte csak nagyította a képet. Most a szolgáltatás legfinomabb csempeszintjéig készülnek a képcsempék (XYZ rétegnél a `zmax`, pl. 18); a Publish ablakban ez a *Like the service's sharpest tiles (recommended)* választás. Sima WMS rétegek (bármilyen felbontás) a térkép zoomjait kapják; a helyi raszterképeket nem érintette.
