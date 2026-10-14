# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.38.5)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.38.5)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.38.5**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.38.5 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### HÉSZ előbeállítás (csak a HU változatban)
- **Másféle Lizmap-projektek is (pl. övezetkódonkénti HTML-fájlok):** ha a kifejezésmező az övezetkódból képez fájlnevet (pl. `'media/html/' + szab_ov + '.html'` → `Lke-1.html`, benne az övezeti paraméterekkel), az előbeállítás ezt is átveszi, a Lizmap felugró sablonjának sorrendjében (övezetkód szerinti szöveg, övezettípus, általános előírások). A sablonban nem szereplő HTML-mezőket kihagyja.
  - Ha egy övezetkódhoz nincs fájl, a többi szöveg (övezettípus, általános) megmarad – ahogy a Lizmapben is –, és a hiányzó fájlokat felsorolja.
- **Jobb övezeti réteg választás:** több övezeti jellegű réteg közül a kirajzolt, színezett szabályozási/övezeti réteget választja (nem pl. a „Parti sáv”-ot), azon belül a legtöbb elemet tartalmazót.
