# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.19.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.19.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.19.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.19.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Csatolt mezők, virtuális mezők és mentetlen szerkesztések (generikus 4.19.1)
Az export minden réteget újra beolvasott a fájljából vagy adatbázisából. Ebből a másolatból hiányzik,
ami csak a megnyitott projektben létezik, így – az ideiglenes rétegeken túl (4.19.0) – ezek is elvesztek:

- **csatolt mezők** (Réteg tulajdonságai → Kapcsolatok / Joins), a kézzel elmozgatott feliratok helyzete
  is (a QGIS ezt kiegészítő tárolóban, csatolásként tartja). Az ezekre épülő feliratok, stílusok üresen
  kerültek ki, és az export „No glyphs for font” hibával leállhatott;
- **virtuális mezők** (a mezőkalkulátor „Virtuális mező létrehozása” opciójával);
- a szerkesztés alatt álló réteg **mentetlen módosításai**: új, módosított vagy törölt elemek.

Az ilyen rétegeket mostantól a megnyitott projektből vesszük, ahogy a QGIS mutatja őket.
