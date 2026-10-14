# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.38.2)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.38.2)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.38.2**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.38.2 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Webtérkép (generikus 4.38.2)
- **Még vékonyabb hajszálvonalak:** bekapcsolt *Thin hairlines* mellett (Publish ablak, *Output*, alapból be) a folytonos 0 vastagságú vonalak most 0,35 fizikai képpont szélesek (erős nagyításnál 0,6), 70 %-os átlátszatlansággal. A QGIS hajszálvonalához mérve ez nagyjából a harmada (4.38.1: valamivel több mint a fele), normál és 4K képernyőn egyaránt. Világosszürke vonalként továbbra is látszanak. A térkép újrapublikálása után lép érvénybe.
