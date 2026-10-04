# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.8.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.8.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.8.0 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.8.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Webes alaptérképek XYZ címmel (generikus 4.8.0)
- **Beállítás:** Publish ablak → Basemap fül → **Web basemaps (XYZ)**. Soronként egy alaptérkép:
  cím, XYZ csempecím, forrásmegjelölés, min./max. zoom.
- **A cím formája:** `{z}`, `{x}`, `{y}`; `{-y}` TMS sorrend, `{s}` a/b/c szerverek; csak
  `https://`. Például: `https://tile.openstreetmap.org/{z}/{x}/{y}.png`.
- **From QGIS XYZ connections…** a QGIS-ben már elmentett XYZ kapcsolatokból kínál.
- **A webtérképen:** az **Alaptérkép** menüben jelennek meg, a terv rétegei alatt, a forrásukkal
  a sarokban. Kezdő alaptérkép is lehet közülük („Shown at start” → „Web: …”).
- **Betöltés:** a látogató böngészője böngészés közben tölti a csempéket az adott szerverről; a
  kiadásba nem kerül semmi. Az oldal pontosan ezeket a szervereket engedélyezi.
- **Hol tárolódnak:**
  - a projektben;
  - az exportált beállításfájlban, importáláskor vissza is kerülnek;
  - **QGIS XYZ kapcsolatként** is (Böngésző → XYZ Tiles), ugyanazzal a névvel, így más
    projektben is kéznél vannak.
- Csak olyan címet használjon, amelyre jogosult, a szolgáltató által kért forrásmegjelöléssel.
