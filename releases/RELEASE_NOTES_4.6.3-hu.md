# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.6.3)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.6.3)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.6.3 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.6.3 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítás (generikus 4.6.3)
- **Az export hibával leállt a bővítmény frissítése után** (`… has no attribute 'part_hash'`), ha a
  QGIS-t nem indították újra: a QGIS a később betöltött modulokat (pl. az export gyorsítótárat)
  a régi változatukban tartotta meg. Mostantól a bővítmény minden betöltéskor frissen tölti be az
  összes modulját. Ha még a 4.6.2 fut, egyszer indítsa újra a QGIS-t (vagy telepítse a 4.6.3-at).
- **Jelmagyarázat oszlop** a Publish ablak rétegjegyzékében (Map fül): rétegenként vagy több
  sorra / csoportra egyszerre (*Selected layers → Show in / Hide from the legend*) beállítható,
  hogy a réteg megjelenjen-e a jelmagyarázatban.
