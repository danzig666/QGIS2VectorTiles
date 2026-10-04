# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.9.2)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.9.2)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QWebMap HÉSZ 4.9.2**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.9.2 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### „Pont körül” elhelyezésű poligonfeliratok a pont mellett, mint a QGIS-ben (generikus 4.9.2)
A QGIS **Pont körül** (centroid körül) elhelyezésű poligonfeliratai a webtérképen eddig a
poligon közepén, a centroidra tett jel (pl. SVG ikon) tetején jelentek meg.
- **Most:** a felirat a pont mellé kerül, a QGIS-ben beállított **felirattávolságra**.
- **Helyek:** a felirat sorban kipróbálja a pont körüli helyeket: először fölötte, aztán a
  sarkokban és oldalt. Az elsőt választja, amely a képernyőn elfér és nem takar más feliratot.
- **Térképi egységben megadott távolság:** a zoommal együtt nő és csökken.
- **A te projektedben:** az „Alrészlet feliratok” réteg ilyen elhelyezésű (0 távolsággal), ezért
  a feliratai mostantól a centroid mellett jelennek meg, ahogy a QGIS is rajzolja.

### Koordináták a projekt saját vetületében (generikus 4.9.2)
Az Eszközök fül eddig minden térképen EOV koordinátát mutatott, Magyarországon kívül is. Most a
WGS 84 mellett a **projekt saját vetületének** koordinátáit mutatja:
- **EOV projekt:** EOV, a korábbi átszámítással (a HÉSZ projekteknél tehát minden marad).
- **Más vetületű projekt:** a böngészőben számolva, a csomagolt proj4js könyvtárral, amelyet
  a térkép csak ilyenkor tölt le.
- **WGS 84 projekt:** csak WGS 84.
