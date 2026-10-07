# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.17.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.17.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.17.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.17.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Egész jelek a pontmintázatokban, mint a QGIS-ben (generikus 4.17.0)
Ha egy pontmintázatos kitöltés jelvágása nem „Clip to shape” („Marker centroid within shape”,
„Marker completely within shape”, „No clipping”; pl. a „Cross-Stitch” stílus), a QGIS egész jeleket
rajzol: a szélen egyetlen jelet sem vág el, a körvonalat a jelek adják, nem a poligon. A webtérkép eddig
a szélen elvágott textúrával töltötte ki az ilyen poligonokat. Mostantól:

- a jelek egészek, a QGIS rácsán: az elem bal felső sarkától, a felső élre eső sorral együtt;
- az egymásra lógó jelek úgy fedik egymást, ahogy a QGIS rajzolja őket (oszloponként balról, az
  oszlopon belül felülről), a nagy poligonok export közbeni darabolásánál sincs varrat;
- a tört pixeles helyzetű jelek simán, sávosodás nélkül jelennek meg, mint a QGIS-ben.

A jelek távolsága nagyítási szintenként nyolc lépésben követi a nagyítást (±4,5 %-on belül), így ezek a
rétegek szintenként nyolc adatkészletet exportálnak.

### A „Clip to shape” pontmintázatok a QGIS saját textúrájával
A QGIS ezeket a poligonokat egy két jel széles, egész pixelre vágott textúrával tölti ki, a jeleket a
saját sorrendjében egymásra rajzolva. A webtérkép most pontosan ezt a textúrát használja, így az egymásra
lógó jelek ugyanúgy néznek ki (a tesztben a színek 1,4 %-ban térnek el, korábban 19 %-ban).

### Javítás
- A pixelben megadott méretű jelek nagy felbontású (retina) képernyőn fele akkorák voltak.

### Ismert eltérések
- „Align pattern to: Viewport” (nézethez igazítás) esetén a QGIS a térképablak sarkától indítja a
  jelrácsot, így a széleken lévő jelek minden mozgatáskor változnak. A webtérkép a térkép origójától
  indítja: a jelek egészek és ugyanúgy néznek ki, de a széleken egy sorral vagy oszloppal eltérhetnek a
  QGIS-étől. Elemhez („Feature”) igazítva a rács megegyezik a QGIS-ével.
