# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.8.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.8.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.8.1 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.8.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Webes alaptérképek közvetlenül a QGIS XYZ kapcsolataiból (generikus 4.8.1)
- **A táblázat:** Publish ablak → Basemap fül → *Web basemaps*. Most **minden QGIS-ben mentett
  XYZ kapcsolatot** felsorol (Böngésző → XYZ Tiles):
  - **Use:** pipával választja ki, melyiket kínálja a webtérkép;
  - **Name:** a QGIS kapcsolat neve; meglévő kapcsolatnál nem szerkeszthető, így nem lesz
    belőle duplikátum;
  - **forrásmegjelölés és zoom** szerkeszthető, és visszamentődik a QGIS kapcsolatba.
- **Csak https:** a sima `http://` címet a lista mutatja, de nem jelölhető ki, mert weboldal csak
  https csempéket tölthet be.
- **Add new…** új kapcsolatot vesz fel (a QGIS-be is); **Reload from QGIS** újraolvassa a
  listát.
- **Kezdő alaptérkép:** a fül tetejére került („Basemap shown at start”), és a beépített
  stílusokra és a webes alaptérképekre is vonatkozik. A fül görgethető.
