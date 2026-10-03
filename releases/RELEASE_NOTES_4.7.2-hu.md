# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.7.2)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.7.2)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.7.2 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.7.2 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Rövid webcím (generikus 4.7.2)
- **A rövid cím működik:** Cloudflare R2-n eddig csak a `…/maps/arlo/index.html` cím működött,
  a `…/maps/arlo` nem, mert az objektumtárnak nincs mappaindexe. Mostantól minden publikálás
  feltölt két kis objektumot is:
  - `maps/arlo/`: a stabil belépő oldal;
  - `maps/arlo`: átirányít a `maps/arlo/` címre.

  Így a `https://<domain>/maps/arlo` cím is megnyitja az aktuális térképet, Cloudflare szabály
  vagy Worker nélkül. A Publish ablak publikálás után ezt a rövid linket mutatja.
- **A címsor a rövid címet mutatja:** megnyitás után a hosszú `…/releases/r-…/index.html` helyett
  az a cím látszik, amelyen a térképet megnyitották (a térkép helyzetével). Így az újratöltés és
  a megosztott link mindig az aktuális változatot nyitja meg. A „ez a változat” link továbbra is
  a pontos változatra mutat.
- **Ha a tárhely nem tudja:** ahol ilyen kulcs nem tárolható (egyes S3-kompatibilis szerverek,
  pl. MinIO), a publikálás így is sikeres, a link pedig `…/index.html` marad.
- **Teendő:** az első 4.7.2-es publikálás létrehozza a két objektumot, mást nem kell tenni.
