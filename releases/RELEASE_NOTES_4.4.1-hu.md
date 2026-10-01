# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.4.2)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.4.2)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.4.1 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.4.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. A két változat ugyanabba a plugin-mappába települ, így
az egyik telepítése lecseréli a másikat; a projektbe mentett beállítások azonosak.

### Újdonságok a generikus 4.4.1-hez képest
- **Preset… → Magyar településrendezési terv (HÉSZ, szab_ov)** a Publish Web Map ablakban:
  kitölti a telekinformációt (Földrészletek / `hrsz`, `szab_ov` övezetkód és övezeti értékek,
  Szabályozási vonal és Övezethatár mint vágóvonal), a korlátozó rétegeket (védett területek,
  régészeti lelőhelyek, műemlékek, biztonsági övezetek, védőtávolságok) magyarázattal és
  jogszabályi hivatkozással, valamint magyar tájékoztató szöveget.
- A hivatkozások javaslatok, ellenőrizendők. Leírás: `docs/hu/HESZ.md`.

### Generikus 4.4.1
- Előbeállítások (presets) bővítési pont, változat-kiadások (`variant.json`), automatikus ág-szinkron.
- Üres tájékoztató szöveg esetén a viewer saját, nyelvének megfelelő szövege jelenik meg.
