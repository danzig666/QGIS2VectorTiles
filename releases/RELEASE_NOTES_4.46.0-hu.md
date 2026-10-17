# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.46.0)](https://github.com/danzig666/QWebMap/releases/tag/v4.46.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.46.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.46.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Változott (a generikus 4.46.0-ból)
- **A közzétett terület (Publish ablak → *Map* fül → *Extent*) kifejezetten választható**:
  - *Current map view (at each export)* — az alapértelmezés: amit a QGIS térkép az exportáláskor mutat. Ilyen export előtt az ablak ezt jelzi a terület méretével és a csempék számával, körberajzolja a területet a térképen, és az export megszakítható; a *Don't ask again for this project* kikapcsolja a kérdést.
  - *Extent of a layer* — egy réteg kiterjedése; követi a réteget, ha az adatai változnak.
  - *Fixed area (drawn or frozen)* — a térképre rajzolt (*Draw…*) vagy a pillanatnyi nézetből rögzített (*Freeze view*, eddig *Map canvas*) terület.
- A **Show on map** minden módban körberajzolja a QGIS térképen a közzéteendő területet.

### Javítva
- Az ablak az első megnyitáskori térképnézetet rögzített kiterjedésként megtartotta: hetekkel későbbi exportok is azt a régi nézetet tették közzé a mostani helyett. A korábban mentett, kiterjedés-réteg nélküli projektek mostantól az aktuális térképnézetet használják (ha volt beállított területed, rajzold meg vagy rögzítsd újra).
