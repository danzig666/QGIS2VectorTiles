# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.42.2)](https://github.com/danzig666/QWebMap/releases/tag/v4.42.2)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.42.2**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.42.2 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Változott (a generikus 4.42.2-ből)
- **Mérés:** jobb egérgomb befejezi a mérést, mint a QGIS-ben (jobb gombos húzás továbbra is forgatja a térképet); a **Backspace** visszavonja az utolsó pontot. A dupla kattintás, az Enter és az Escape a régi módon működik.
- **Telekinformáció nyomtatása:** minden övezet teljes előírásszövege rákerül a nyomtatásra, akkor is, ha a panelen nincs lenyitva (nyomtatás előtt betöltődik). A több övezetnél közös részek továbbra is egyszer nyomtatódnak.
