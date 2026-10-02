# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.5.2)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.5.2)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.5.2 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.5.2 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítás (generikus 4.5.2): feliratok elhelyezése
- A **vízszintes** (Horizontal) elhelyezésű poligonfeliratok (pl. az övezetkódok) oda kerülnek,
  ahol a poligonban a legtöbb hely van – az övezet közepére –, ahogy a QGIS is teszi.
- Mozgatás után a felirat visszakerül a látható rész közepére, nem marad a szélén.
- Nem jelenik meg felirat, ha a poligonnak csak egy keskeny sávja látszik, vagy ha a felirat nem
  férne ki a képernyőre.
- Az egymással versengő feliratok (pl. övezetkód és ugyanannak a teleknek a helyrajzi száma)
  kitérnek egymásnak; a nagyobb QGIS prioritású / z-indexű kapja a közepét.
- Az új feliratokhoz újra kell exportálni / publikálni.
