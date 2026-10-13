# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.36.5)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.36.5)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.36.5**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.36.5 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítások (generikus 4.36.5)
- **„Loading…” állapotban ragadt térkép:** a 4.36.4 javította a léptéktartománnyal korlátozott pontminta-kitöltések exportálási hibáját, de ezután a mintához olyan stílusréteg is kerülhetett, amely 24-es nagyítás fölött kezdődik. A MapLibre az ilyen réteget tartalmazó teljes stílust elutasítja, ezért a webtérkép nem töltődött be. A mintát már nem bontja fel a térkép utolsó nagyítási szintje fölött. Kérjük, tegye közzé újra.
- **Ellenőrzés közzététel előtt:** minden export ellenőrzi a kész stílust, és kihagyja azt a réteget, amelyet a böngésző elutasítana (0–24-en kívüli nagyítás, a maximumnál nem kisebb minimális nagyítás, nem szám érték), figyelmeztetéssel az exportjelentésben — így egy hibás réteg nem akadályozhatja meg az egész térkép betöltését.
- **Hibaüzenet végtelen töltés helyett:** ha egy térképstílus mégsem töltődik be, a megjelenítő „The map style is invalid” hibát mutat a technikai részletekkel, ahelyett hogy örökké „Loading…” állapotban maradna.
