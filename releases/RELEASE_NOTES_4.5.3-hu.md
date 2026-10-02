# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.5.3)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.5.3)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.5.3 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.5.3 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítás (generikus 4.5.3): szintvonal-feliratok
- A vonalanként egyszer kiírt vonalfeliratok (pl. a **szintvonalak** magasságai) a vonal
  **látható részén** jelennek meg: a képernyőn látszó leghosszabb szakasz közepén, a vonal
  irányába forgatva – ahogy a QGIS is teszi. Eddig a teljes vonal közepén voltak, ami hosszú
  szintvonalaknál gyakran a képen kívül esett, így nem látszott felirat.
- A felirat csak ott jelenik meg, ahol elfér a vonal mentén és a képernyőn; a vonal mentén
  elcsúszik, hogy ne takarjon más feliratot.
- Az új feliratokhoz újra kell exportálni / publikálni.
