# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.6.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.6.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.6.0 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.6.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Újdonságok (generikus 4.6.0)
- **Szabad (szögben) feliratelhelyezés:** a QGIS „Free (angled)” elhelyezésű poligonfeliratai úgy
  viselkednek, mint a QGIS-ben: ha a felirat vízszintesen elfér a poligonban, vízszintes marad,
  különben a poligon irányába fordul – az utcanevek (pl. „(878) Kossuth Lajos utca”) az utca
  mentén futnak, a keskeny telkek helyrajzi számai elfordulnak.
- **Látható méretarány rétegenként** (Publish ablak → Map fül → *Scales* oszlop): duplakattintás egy
  cellán, vagy több sor / csoport kijelölése → *Selected layers → Visible scales…*. A webtérképen
  elrejti a réteget egy méretarány fölött (vagy alatt), pl. az épületeket és szintvonalakat
  1:10 000-nél kisebb méretaránynál. Ahol rejtett, ott csempe sem készül: kicsinyítve gyors a
  térkép, és kisebb az export. A QGIS projekt nem változik.
- A **Nézetek** sáv nem jelenik meg, ha csak egy nézet van.
- Az újdonságokhoz újra kell exportálni / publikálni.
