# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.6.2)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.6.2)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.6.2 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.6.2 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítások (generikus 4.6.2)
- **Export gyorsítótár:** a GeoPackage rétegeket minden exportnál újragenerálta, ha a QGIS-ben nyitva
  voltak (a QGIS megnyitáskor a fájl idejét és méretét megváltoztatja). Mostantól csak a valódi
  szerkesztés számít, és a napló megírja, miért készült újra egy réteg. A frissítés utáni első export
  még mindent újragenerál, utána a változatlan rétegek újrahasznosulnak (40 réteg: 242 s → 23 s).
- **Jelmagyarázat:** a vonalak a valódi vastagságukkal jelennek meg; az egy jelű rétegek egyetlen
  sorban (nincs külön félkövér fejléc).
- **A Rétegek fül kikapcsolható** (Publish ablak → Interaction → *Layers tab*): ilyenkor csak a
  Jelmagyarázat marad.
- **Nincs kiemelés egérrel való rámutatáskor.**
- **Kattintásra egy elem nyílik meg:** ha van telekinformáció, a telek (a telekinfó felsorolja az
  övezetet és minden mást azon a helyen), egyébként a legfelső elem. Nincs többé „Itt több elem van
  — válasszon” lista.
- Az újdonságokhoz újra kell exportálni / publikálni.
