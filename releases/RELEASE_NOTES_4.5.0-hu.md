# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.5.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.5.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.5.0 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.5.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása (Preset… → Magyar településrendezési terv (HÉSZ, szab_ov)).
A két változat ugyanabba a plugin-mappába települ, így az egyik telepítése lecseréli a másikat.
Leírás: `docs/hu/HESZ.md`.

### Újdonságok (generikus 4.5.0)
- **Gyors újraexportálás**: a korábbi exporthoz képest nem változott rétegek adatkészleteit,
  csempéit és a telekinformációt a plugin újrahasznosítja. Egy réteg szerkesztése után csak az
  a réteg készül újra: a 40 rétegű tervnél kb. 170 s helyett 15 s. *Output → Reuse unchanged
  layers* (alapból be), *Clear cache…*.
- **Gyorsabb feltöltés**: a változatlan fájlokat a tárhely magán belül másolja, nem tölti fel újra.
- **Cloudflare R2 lépésről lépésre** a Publish ablakban (Destination → *Step-by-step: set up
  Cloudflare R2…*); a beillesztett Cloudflare címből kitölti az account id-t és a bucketet;
  *Save keys in QGIS…* titkosítva menti a kulcsokat.
