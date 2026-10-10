# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.31.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.31.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.31.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.31.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítás (generikus 4.31.1)
- **A feliratkeret a felirattal együtt fordul.** A felirattal együtt forduló háttér (*Sync with label*, a QGIS alapbeállítása, vagy *Offset of label*) a weben vízszintes maradt: például a keskeny sokszögek mentén *Free (angled)* módon elhelyezett övezetkódoknál a szöveg el volt fordulva, a keret nem. Mostantól a keret a szöveggel együtt fordul, mint a QGIS-ben, és a sarkai is a helyükön maradnak (a MapLibre az elforgatott keret sarkait nem forgatta el, ezért kiálltak).

| Futtatás | Eredmény |
|---|---|
| Teljes generikus tesztcsomag (fájlonként külön folyamatban) | 893 sikeres, 5 kihagyva; az utolsó módosítás után a böngészős, felirat- és nézegető-tesztek újra: mind sikeres |
| HU tesztek a hu-hesz ágon | 13 sikeres, 1 kihagyva (HU tesztek, plugin-csomag) |
