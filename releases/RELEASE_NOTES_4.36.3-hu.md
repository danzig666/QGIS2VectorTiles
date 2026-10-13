# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.36.3)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.36.3)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.36.3**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.36.3 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Feliratok (generikus 4.36.3)
- **Olvashatatlanul kicsi feliratok:** a térképi egységben (méterben) megadott méretű feliratok kicsinyítéskor olvashatatlan pöttyökké zsugorodtak. Most elrejti őket ott, ahol a szövegük 6 px-nél kisebb a képernyőn. Publish ablak, Output fül: „Hide labels smaller than [6] px”, alapból bekapcsolva; kikapcsolva minden méretben megjelennek. A pontban, milliméterben vagy pixelben megadott és az adatvezérelt méretű feliratokat nem érinti.
- **Pixelméret szerinti láthatóság:** a QGIS felirat Rendering fülén beállított „Pixel size-based visibility” (minimum és maximum px) mostantól érvényesül a térképi egységben megadott méretű feliratoknál.

### Vonalak (generikus 4.36.3)
- **Szaggatás kicsinyítve:** a térképi egységben megadott, méret-léptéktartománnyal („Min/max size”) korlátozott szaggatás messze kicsinyítve folytonos vonallá vált. A szaggatás most ugyanazzal a léptéktartománnyal korlátozódik, mint a vonalvastagság, így a minta megtartja az alakját.
