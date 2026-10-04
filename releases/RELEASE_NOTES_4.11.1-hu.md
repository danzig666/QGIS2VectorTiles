# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.11.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.11.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.11.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.11.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Szabad (szögben elforgatott) feliratok úgy fordulnak, mint a QGIS-ben (generikus 4.11.1)
- **Vízszintes feliratok:** a *Szabad (szögben)* elhelyezésű és *Centroid: teljes poligon*
  beállítású poligonfeliratok a weben vízszintesen jelentek meg. Mostantól a webes megjelenítő
  helyezi el őket, a többi poligonfelirathoz hasonlóan.
- **A QGIS szabálya:** a megjelenítő a QGIS feliratozó motorjának szabályát követi. A poligon
  körülíró téglalapja alapján a felirat:
  - **vízszintes**, ha a felirat kétszeres méretben is belefér a téglalap közepére;
  - különben **a vízszinteshez legközelebbi oldal mentén** fordul, ha mindkét oldal hosszabb
    másfél feliratszélességnél;
  - egyébként **a hosszabb oldal mentén** fordul.

  Egy ferde telekben így a felirat a telket követi, mint a QGIS-ben.
- **Feliratméret:** a megjelenítő a felirat szélességét mostantól a betűtípus mért átlagos
  karakterszélességével becsüli. Eddig egy általános érték miatt a keskeny betűtípusokat túl
  szélesnek vette.
