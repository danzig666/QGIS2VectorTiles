# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.35.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.35.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.35.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.35.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítás: a vonalkázás a poligonon belül marad (generikus 4.35.1)
- **Rajzoláskor vágott vonalkitöltés** (a QGIS alapbeállítása, „Clip during render only”): a QGIS a poligon szélén levágja a megrajzolt vonalakat. A webes térkép a vonalakat a középvonaluk mentén vágta, és a végükkel együtt rajzolta, így nagyításkor a vonalkázás kilógott a poligonból, leginkább ott, ahol egy vonal egy keskeny sáv mentén fut. A térképi egységben megadott vonalkázás most a QGIS által festett sávokként kerül ki, a poligonra vágva, attól a nagyítástól, ahol 2 px szélesek; alatta vonalvég nélküli vonalakként.
- **Rajzolás előtt vágott vagy vágatlan vonalkázás:** ezeket a QGIS vonalanként rajzolja az egyes részek befoglaló téglalapjától; eddig az alapmintához igazodtak, akár egy teljes vonaltávolsággal elcsúszva. Most egyeznek a QGIS-szel.
