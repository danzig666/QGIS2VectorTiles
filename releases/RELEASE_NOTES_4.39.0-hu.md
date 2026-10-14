# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.39.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.39.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.39.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.39.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítva (az eredeti plugin v4.0-jából átvéve)
- **Nincs üres térkép zoomszintek között:** ahol egy réteg egy zoomszinten kezdődik vagy ér véget (léptékfüggő szabályok, zoomonkénti jelelrendezés), a publikált webtérkép nem villan üresre, amíg a következő zoom csempéi betöltődnek; addig a már betöltött csempékből rajzolja a réteget. Csak ahol azokban a csempékben nincs az adata, így semmi sem rajzolódik kétszer. A Rétegek fül továbbra is azokon a zoomokon szürkíti a réteget, ahol nem rajzol.
- **Biztonságosabb export:** a data-defined (adatvezérelt) tulajdonságokat csak a stílusobjektumok azon részein keresztül olvassa, amelyekben ilyenek lehetnek (szimbólumrétegek, alszimbólumok, felirat szövegformátum és kiemelővonal), és nem hívja meg minden stílusobjektum minden metódusát (az egyik szimbólum-előnézetet rajzol, ami az eredeti pluginban összeomlasztotta az exportot). A talált tulajdonságok ugyanazok (minden QGIS szimbólumréteg- és kiemelővonal-típuson ellenőrizve).
