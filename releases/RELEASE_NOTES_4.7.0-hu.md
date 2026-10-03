# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.7.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.7.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.7.0 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.7.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Újdonságok (generikus 4.7.0)
- **Utcanév-keresés (OpenStreetMap).** Publish ablak → Interaction fül → *Search street names
  (OpenStreetMap)*. Ezzel a webtérkép keresője a **kiterjedés-réteg** (Map fül) területén belüli
  OSM utcaneveket is megtalálja; kiterjedés-réteg nélkül az export területén belülieket.
  - **Egy keresőmező:** a helyrajzi számot, a többi keresendő mezőt és az utcákat együtt keresi.
  - **Utca találat:** „Utca (OpenStreetMap)” jelölést kap. Kiválasztva a térkép az utcára ugrik,
    és jelölő kerül az utca közepére.
  - **A nevek forrása:** a mellékelt alaptérkép. Alaptérkép nélkül a Protomaps OSM buildből
    olvassa, ehhez exportáláskor internet kell.
  - **Mi kerül ki:** utcánként csak a név, egy pont és a befoglaló téglalap, geometria nem.
  - **Ugyanaz a név többször:** a szomszédos településrészek azonos nevű utcái (pl. két „Fő utca”)
    külön találatok.
- **Keresés tetszőleges mezőben:** ez már eddig is megvolt. Interaction fül → réteg kiválasztása
  → a *Search* oszlopban jelölje be a keresendő mezőket (pl. a földrészletek `hrsz` mezőjét).
  Ugyanaz az egy keresőmező keres minden rétegben.
- **A Publish ablak megjegyzi a méretét és a helyét** (QGIS beállításokban), a QGIS újraindítása
  után is.
