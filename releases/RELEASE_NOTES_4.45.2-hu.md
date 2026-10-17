# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.45.2)](https://github.com/danzig666/QWebMap/releases/tag/v4.45.2)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.45.2**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.45.2 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

A MapLibre GL JS 6.14-re frissítés teljes átvizsgálása (viewer, patchek, a 6.11.2-vel összevetett megjelenítés) más, a frissítés okozta hibát nem talált; az alábbiak közben kerültek elő.

### Javítva (a generikus 4.45.2-ből)
- A **„Hide labels smaller than” által elrejtett, adatfüggő méretű, térképi egységben megadott feliratok** (4.45.1) zoomonként egy-egy másolatban kerültek a csempékbe; most a méreteikhez tartozó pontos méretaránynál vágódnak (kisebb csempék, pontos zoomok).
- A **nagy, térképi egységű jelek** (kb. 128 px fölött alakzatként rajzolva) eltűntek az utolsó csempezoom és az alakzattá váló zoom között.
- Az első / utolsó törésponti jelek **„Offset along line”** értéke a projekt vetületében mérődik: EOV-ban 100 m eddig kb. 68 m-rel tolta el őket.
- A **háttérkeretes és árnyékos feliratok** a kerettel együtt mért méretük szerint kerülnek a helyükre; eddig egymásra lógtak és kilógtak a poligonjukból.
- A **diagramok** azonnal megjelennek, amint a térképnek szüksége van rájuk (nincs konzol-figyelmeztetés, nincs késés).
- **Keresés**: animált jelekkel a térképen a találat és a felugró ablaka 10 másodperc múlva jelent meg.
