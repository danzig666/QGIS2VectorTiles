# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.5.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.5.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.5.1 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.5.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítás (generikus 4.5.1)
- Telekinformáció: az opcionális **övezeti előírás-tábla** (*Zone regulations table*) kiválasztásakor
  a plugin magától kiválasztja a tábla övezetkód-mezőjét (pl. `szab_ov`). Ha nem talál ilyet,
  érthető üzenetet ad; a tábla nem kötelező, a *Table* mező üresen hagyható.
