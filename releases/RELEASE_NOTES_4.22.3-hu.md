# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.22.3)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.22.3)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.22.3**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.22.3 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Más bővítmények már nem nullázzák az export-gyorsítótárat (generikus 4.22.3)
A gyorsítótár minden projektváltozót export-beállításnak tekintett. Egy bővítmény, amely a saját
projektváltozóit folyamatosan frissíti (pl. időmérő: `@time_tracker_total_minutes`), így minden exportnál
mindent újra elkészíttetett („Redone (export settings changed (@time_tracker_…))”). Most csak azok a
változók számítanak, amelyeket a stílusok és feliratok ténylegesen használnak; ezek változása továbbra
is újra elkészíti az őket használó rétegeket.

### Verziószám az ablak címében
A Publish ablak címe mutatja a bővítmény verzióját: „Publish Web Map — QWebMap 4.22.3”.

Megjegyzés: a frissítés utáni első export egyszer mindent újra elkészít; a második exporttól a
változatlan rétegek újrahasznosulnak.
