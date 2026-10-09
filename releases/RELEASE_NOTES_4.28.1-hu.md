# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.28.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.28.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.28.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.28.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Amit a debreceni exportnapló megmutatott (generikus 4.28.1)
Debrecen ingatlan-nyilvántartási rétegeinek exportja (7 réteg: a város összes földrészlete és épülete, házszámok) 11 percig tartott. Ebből 4,5 perc ment az adatkészletekre és 6,6 perc a csempékre; közben a folyamatjelző négy percig 45%-on állt. Ugyanezeknek a rétegeknek egy Debrecen-méretű másolatán újramérve (85 ezer földrészlet, 47 ezer épület, 39 ezer házszám; 0–16. zoom, 4 mag):

| | 4.22 | 4.28.0 | 4.28.1 |
|---|---|---|---|
| Adatkészletek | 4,2 perc | 3,5 perc | 1,6 perc |
| Csempék | 8,4 perc | 3,0 perc (háttérben) | 2,8 perc (háttérben) |
| Teljes publikálás | 14,5 perc | 8,3 perc | 6,1 perc |

- **A kikapcsolt beállítások már nem lassítják az exportot.** Egy szimbólum megőrizhet egy kikapcsolt adatvezérelt beállítást: a QGIS eltárolja a kifejezését, de nem használja. Ha ez a kifejezés a méretarányt olvasta (`@map_scale`), a réteg minden zoomszintre külön, minden elemével együtt készült el. A debreceni földrészletek így 17-szer, mert a kitöltésben ott maradt egy kikapcsolt körvonalvastagság (`CASE WHEN @map_scale < 1000 …`). Most csak a bekapcsolt beállítások számítanak. A térkép ugyanaz marad: a legnagyobb zoomon a földrészletek azonosak, alatta a kitöltésük 1/16 képpontnyit egyszerűsödik, mint bármely más kitöltésé.
- **A csempézés folyamatjelzője az ogr2ogr saját haladását követi**, nem becslést, így nem áll meg, amíg a nagy rétegek készülnek. Az „N / M réteg van hátra” rétegeket számol (egy nagy réteg egyszerre több darabban készül).
- **Az exportnapló** jelzi, ha egy réteg csempéi párhuzamos darabokban készültek („tiles 3336 s in 26 parallel pieces”): a darabok másodpercei összeadva többet tesznek ki, mint ameddig a csempék ténylegesen készültek.
- **A hibával leálló csempézési feladat még egyszer lefut**, mielőtt az export hibával leállna. Ezt egyszer láttuk: az ogr2ogr megállt egy adatkészleten, amelyet minden más futásban gond nélkül feldolgozott.

Tipp: a minden méretarányon látszó réteg (nincs *méretarány-függő láthatóság*), például egy város összes földrészlete, a kicsinyített nézet csempéiben is benne van, ahol egyetlen csempe tartalmazza az egészet (több megabájt). Egy méretarány-tartomány a QGIS-ben (pl. földrészletek 1:25 000-től) gyorsabbá teszi az exportot és könnyebbé a webtérképet.

| Futtatás | Eredmény |
|---|---|
| Generikus 4.28.1 kapcsolódó tesztcsomagjai | 200 sikeres, 6,6 perc (20 tesztfájl) |
| HU tesztek a 4.28.1-gyel (előbeállítás, előírás-táblák, teljes szöveg, övezeti felugró ablak, nézegető) | 12 sikeres, 1 kihagyva; a hu-hesz ágon a szabálybontás és a csempézési folyamatjelző tesztjei is: 27 sikeres, 1 kihagyva |
