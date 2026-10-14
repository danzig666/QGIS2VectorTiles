# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.37.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.37.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.37.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.37.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítás (generikus 4.37.1)
- **Eltűnt a Web menü a QGIS menüsorából:** a 4.29 óta a „QWebMap: Publish Web Map…” parancs közvetlenül a Web menüben van. A QGIS a Web menüt csak akkor teszi ki a menüsorra, amikor egy bővítmény almenüt tesz bele, és az az egyetlen eleme. Ha tehát a QGIS a QWebMap-et a MetaSearch (vagy más, Web almenüt használó bővítmény) előtt töltötte be, az egész Web menü hiányzott, mindkét bővítménnyel együtt. A QWebMap most maga teszi ki a Web menüt a menüsorra, oda, ahová a QGIS is tenné (a Database után, ha az nincs, a Raster után). Az eszköztár gombját ez nem érintette.
