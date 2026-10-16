# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.40.2)](https://github.com/danzig666/QWebMap/releases/tag/v4.40.2)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.40.2**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.40.2 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Változott
- **A böngésző Vissza gombja bezárja azt, ami nyitva van**, és nem lép el a térképről. Ezek: az objektum felugró ablaka, a nagy olvasóablak, a telefonon felcsúszó panel, a Street View, illetve a mérés vagy rajzolás. Mindig a legutóbb megnyitott záródik be először. Ha valamit a saját gombjával vagy Esc-kel zártak be, nem marad utána felesleges lépés, így ha semmi sincs nyitva, a Vissza a megszokott módon elhagyja az oldalt. A csak bezárást végző Vissza megtartja az aktuális nézetet, nem tölti újra a térképet.
