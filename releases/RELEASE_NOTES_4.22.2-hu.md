# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.22.2)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.22.2)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.22.2**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.22.2 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Az export-gyorsítótár ismét működik (generikus 4.22.2)
- **GeoPackage rétegek** (Windowson, hálózati meghajtón is): egy réteg akkor használható újra, ha a
  GeoPackage saját módosítási bélyegei szerint nem változott. Ezek kiolvasása Windows-útvonalakon
  hibázhatott, és a réteg ilyenkor szó nélkül minden exportnál újra készült – két egymás utáni
  export sem használt semmit újra. Most ott is kiolvashatók.
- **Memóriarétegek** (pl. a Memory Layer Saver bővítménnyel visszatöltöttek): a QGIS a projekt minden
  megnyitásakor új véletlen azonosítót ad nekik, ezért minden újraindítás után változottnak tűntek.
  Most a tartalmuk dönt.
- **A napló megmondja, miért**: a nem gyorsítótárazható réteg „Redone (not cached: …)” sorban,
  az okkal együtt jelenik meg.

Megjegyzés: a frissítés utáni első export egyszer mindent újra elkészít (változott a bővítmény
kódja); a második exporttól a változatlan rétegek újrahasznosulnak.
