# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.28.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.28.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.28.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.28.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Sokkal gyorsabb export (generikus 4.28.0)
Ugyanaz a 88 rétegű településrendezési terv, mint a 4.27-nél, cache nélkül, 4 magos gépen:

| Export | 4.27 | 4.28 |
|---|---|---|
| 88 réteg, 0–16. zoom | 6,9 perc | 54 s (segédfolyamatok nélkül 1,8 perc) |
| ugyanez *gyors jelölővonalakkal* (új beállítás, lásd lent) | – | 27 s |
| 11 réteg (a terv publikált része) | 8,7 s | 7,5 s |

A csempék ugyanazok: minden csempét dekódolva, elemenként vetettük össze a 4.26-tal és a 4.27-tel (a feliratokat elemhalmazként, minden mást rajzolási sorrendben is), és az illeszkedési jelentés is ugyanazokat a tételeket sorolja.

Mi változott:
- **A jelölők helye közvetlen számítással.** A vonal mentén ismétlődő jelölőket (pl. betűk egy határvonal mentén) eddig egy QGIS-kifejezés helyezte el, amely minden egyes jelölőnél végigjárta az egész vonalat. Most ugyanazzal a számítással, egy menetben készülnek — pontosan ugyanazok a számok, több ezer véletlen vonalon ellenőrizve.
- **Több QGIS-folyamat egyszerre.** Nagy exportnál (kb. 150 adatkészlettől) a bővítmény segédfolyamatokat indít (a QGIS ablak nélküli példányait, annyit, amennyit a *CPU-korlát* enged), amelyek párhuzamosan olvassák a fájlrétegeket és készítik az adatokat. Az adatbázis-, webes és virtuális rétegeket, valamint a projekt más rétegeit lekérdező szabályokat továbbra is maga a QGIS végzi; amit egy segédfolyamat nem tud, azt is.
- **Csempék párhuzamosan és a háttérben.** Minden réteg csempéit saját `ogr2ogr` vágja, egyszerre többet (egy nagy réteget zoomsávokban), miközben a rekordok, a jelmagyarázat, a raszterek és az alaptérkép készülnek.
- Kisebb nyereségek: a több ezer kész adatkészletet SQLite-tal olvassa (nem nyitja meg mindegyiket QGIS-rétegként), gyorsabban olvassa a szimbólumbeállításokat, az azonos mintázatképeket egyszer készíti el.

### Gyors jelölővonalak (választható, alapból ki)
*Kimenet → Gyors jelölővonalak*: azok a jelölővonalak, amelyek térköze képernyőegységben (pont, milliméter, pixel) van megadva, minden zoomon ugyanakkora térközt tartanak a képernyőn, ezért a jelölőket alapesetben minden zoomszintre külön kiszámítja. Ezzel a beállítással a böngésző helyezi el őket a vonalak mentén: sokkal gyorsabb export, de a jelölők nem pontosan ott vannak, ahol a QGIS rajzolja őket (az illeszkedési jelentés jelzi). A pontos térképhez hagyd kikapcsolva.

### Hálózati kimeneti mappa
Ha a kimeneti mappa hálózati meghajtón vagy megosztáson van, a sok munkafájl és az export-cache ezen a gépen marad (a rendszer ideiglenes mappájában); a hálózati mappába csak a kész térkép kerül, az exportnapló és az illeszkedési jelentés másolatával.

### Exportnapló
Az exportnapló megnevezi a leglassabb rétegeket (az adatkészletek és a csempék másodpercei): lassú exportnál itt érdemes először nézni.

| Futtatás | Eredmény |
|---|---|
| Generikus 4.28.0 teljes tesztkészlet | 688 sikeres, 5 kihagyva (lásd a generikus kiadást) |
| HU tesztek a 4.28-cal (előbeállítás, előírás-táblák, teljes szöveg, övezeti felugró ablak, nézegető) és az új gyorsítási tesztek ezen az ágon | 44 sikeres, 1 kihagyva (valós terv nélkül) |
