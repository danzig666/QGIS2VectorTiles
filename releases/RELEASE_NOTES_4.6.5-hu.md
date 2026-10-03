# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.6.5)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.6.5)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.6.5 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.6.5 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Újdonság (generikus 4.6.5): nyomtatás méretarányban
- **A méretarány választása:** Eszközök fül → Nyomtatás → **Méretarány**.
  - *A képernyő szerint (kerekítve)* az alapértelmezés: az aktuális nézet méretarányát a legközelebbi
    szabványos méretarányra kerekíti.
  - Vagy rögzített méretarány: 1:250, 1:500, 1:1000, 1:1500, 1:2000, 1:2500, 1:4000, 1:5000,
    1:10 000 … 1:500 000.
  - A választást a böngésző megjegyzi. A Ctrl+P és a telekinformáció nyomtatása is ezt használja.
- **A nyomaton:** a térkép a középpontját megtartva pontosan ebben a méretarányban készül
  (1:500-nál a 186 mm-es térképrész 93 m). A cím alatt félkövéren szerepel: **M 1:500**.
- **Nyomtatási beállítás:** a méretarány 100%-os nyomtatási méretezésnél pontos (ez a böngésző
  alapértelmezése). A „Laphoz igazítás” vagy más százalék megváltoztatja.
- **Nagyítási korlát:** ha a térkép nem nagyítható eléggé, a lapon a ténylegesen nyomtatott
  méretarány szerepel.
- A jelek és feliratok a képernyőn látható pixelméretükben nyomtatódnak, ezért nagy
  méretarányban (1:250, 1:500) a papíron arányaiban nagyobbnak hatnak, mint a QGIS-ben.
