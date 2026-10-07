# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.19.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.19.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.19.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.19.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Az ideiglenes rétegek is kikerülnek (generikus 4.19.0)
Az ideiglenes (memória-) rétegek – például amelyeket a Memory Layer Saver bővítmény tart meg a
projekttel – üresen kerültek ki, és a webtérkép azt írta rájuk: „Ezen a nagyításon nem látszik”. Az
export minden réteget újra beolvasott a forrásából, egy ideiglenes rétegnek pedig nincs ilyen: üresen
jött vissza. Mostantól az elemeiket a megnyitott projektből vesszük. A változatlan ideiglenes réteget az
export-gyorsítótár is újrahasznosítja, mint bármely más réteget.

### A webtérkép a terület felett marad
Új beállítás a Publish ablakban, a **Extent** (terület) alatt: **Keep the web map on the extent**
(alapból bekapcsolva). A látogatók nem tudják elhúzni a térképet a publikált területről, és csak kicsivel
tudnak kijjebb nagyítani, mint a teljes terület nézete. A megosztott hivatkozások is ezen belül
nyílnak meg. Kikapcsolva a térkép szabadon mozog, mint eddig.
