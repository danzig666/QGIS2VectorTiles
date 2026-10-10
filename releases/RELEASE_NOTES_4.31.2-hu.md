# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.31.2)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.31.2)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.31.2**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.31.2 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítás (generikus 4.31.2)
- **A szabad (Free, angled) elhelyezésű sokszögfeliratok a hosszú sávok közepére kerülnek.** Egy hosszú, egyenletes szélességű sokszögön (utca, telekcsík) a webes térkép oda tette az elforgatott feliratot, ahol a „legtöbb hely” keresése véget ért, gyakran a sáv egyik vége felé. A QGIS-hez hasonlóan mostantól a sokszög (látható részének) súlypontjához legközelebbi helyet választja.

| Futtatás | Eredmény |
|---|---|
| Teljes generikus tesztcsomag (fájlonként külön folyamatban, a 4.31.1 kódján) | 893 sikeres, 5 kihagyva |
| Nézegető-, felirat- és böngészős összehasonlító tesztek a módosítás után | mind sikeres |
| HU tesztek a hu-hesz ágon | 13 sikeres, 1 kihagyva (HU tesztek, plugin-csomag) |
