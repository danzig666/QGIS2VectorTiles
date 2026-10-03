# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.7.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.7.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.7.1 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.7.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Minden helyrajzi szám kiíródik
- **A hiba:** a keskeny szalagtelkeken (pl. hrsz 1119, 118 × 5 m) nem jelent meg a helyrajzi szám.
  A 4 m magas felirat nem fér el az 5 m széles telken, lelógni pedig nem szabad.
- **Az új rétegbeállítás:** Interaction fül → réteg → *Label every feature (smaller where the
  label does not fit)*. A **HÉSZ előbeállítás ezt a földrészlet rétegen bekapcsolja**, és
  a `hrsz` mezőt keresővé teszi.
- **Ha a felirat nem fér el:** a telken belül kisebb méretben jelenik meg (80%, 65%, 50%), szükség
  esetén a telek irányába forgatva.
- **Ha fél méretben sem fér el:** fél méretben kerül a telek legtágasabb pontjára. Így minden
  helyrajzi szám kiíródik.
- **Gyorsan:** ezeknek a kis telkeknek a felirathelyét már az export kiszámolja (a legtágasabb
  pont, a telek iránya), így a böngészőben nem kell keresgélni. A feliratok elhelyezése alig
  lassabb, mint a beállítás nélkül.
- **Meglévő projektben:** kapcsolja be a beállítást a földrészlet rétegen az Interaction fülön,
  vagy futtassa újra a HÉSZ előbeállítást.
