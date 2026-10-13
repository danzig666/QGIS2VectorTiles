# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.36.4)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.36.4)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.36.4**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.36.4 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítások (generikus 4.36.4)
- **Exportálási hiba (RecursionError):** az a pontminta-kitöltés (Point pattern fill), amelynek sortávolsága térképi egységben van, méret-léptéktartománnyal („Min/max size”), az Y-eltolása pedig 0, „maximum recursion depth exceeded” hibával leállította az exportot. A QGIS az ilyen mintát az Y-eltolás léptéktartományával rajzolja; az export most ezt is kezeli, amikor a mintát nagyítási szintek szerint felosztja.
- **Levágott jelek nagy nagyításon:** a térképi egységben megadott, kitöltés-körvonallal rajzolt nagy jel (pl. 5 m-es kör 0,8 m-es gyűrűvel) kb. 20-as nagyítástól levágva jelent meg az oldalain, mert a körvonal túlnyúlik a jel méretén. A jel képe most addig nő, amíg az egész jel belefér.
