# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.30.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.30.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.30.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.30.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítások (generikus 4.30.1)
- **Leállt az export régebbi projektek feliratainál** ezzel a hibával: *ValueError: -1 is not a valid Qgis.LabelMultiLineAlignment*. A régebbi QGIS-ben készült feliratoknál a többsoros igazítás „nincs beállítva” értéket tárolhat, ezt a PyQGIS nem tudja kiolvasni. Az ilyen felirat mostantól balra igazított, ahogy a QGIS is rajzolja. (Egy 64 rétegű településrendezési terv, a Püspökladány-projekt mind a 149 feliratbeállításán ellenőrizve: a teljes export és a nézegető hibátlan.)
- **Shapeburst (árnyalt) kitöltés** milliméteres távolsággal a weben egyszínű volt: a távolságot a valódinál jóval kisebb zoomon számolta át. Mostantól a QGIS-hez hasonlóan árnyal a széltől befelé.
- **Zoomonként elhelyezett szimbólumok rétegeinek feliratai** (pl. nyilak egy patak mentén vagy egyirányú utakon) csak a legnagyobb zoomon jelentek meg. Mostantól minden zoomon látszanak, ahol a QGIS-ben.
- **A feliratok betűköze** átkerül a webre (a ritkított nevek eddig a weben keskenyebbek voltak).

| Futtatás | Eredmény |
|---|---|
| Teljes generikus tesztcsomag (fájlonként külön folyamatban) | 707 sikeres, 5 kihagyva |
| HU tesztek a hu-hesz ágon | 20 sikeres, 1 kihagyva (HU tesztek, plugin-csomag, telekinfó a böngészőben) |
