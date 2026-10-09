# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.27.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.27.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.27.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.27.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Gyorsabb export (generikus 4.27.0)
Egy 88 rétegű településrendezési terven mértük: nem maga a csempézés volt lassú (17 s), hanem az előtte futó adatkészítés. Ez szimbólumszabályonként és zoomonként előállítja az adatokat, például a jelölővonalak pontjait.

- **Ez 27 701 apró QGIS Processing-lépés volt:** mindegyik fájlba írt és onnan olvasott vissza, és a paraméterek ellenőrzéséhez még egyszer megnyitotta a bemenetét.
- **Most a lépések memóriában adják át egymásnak az eredményt.** Lemezre csak a kész adatkészlet kerül, így egy lépés kb. 1 ms a korábbi 15–75 ms helyett.
- **Egyszer futó közös lépések:** a már elvégzett lépés nem fut újra. Például egy szabály szűrése vagy egy övezethatár vonallá alakítása minden zoomhoz és szimbólumréteghez közös.
- **Nagy rétegek és hibák:** a nagyon nagy rétegek (50 000 elem fölött) lépései továbbra is fájlt használnak, így a memóriaigény korlátozott. Ha egy szabály memóriában hibát dob, automatikusan újrafut a régi módon.

| Terv | Eddig | Most |
|---|---|---|
| 88 réteg, 0–16. zoom, cache nélkül | 21,5 perc | 6,9 perc |
| 11 réteg (a terv publikált része) | 15,5 s | 8,7 s |

A csempék tartalma ugyanaz, mint eddig: minden csempét dekódolva, elemenként vetettük össze.
- A feliratrétegeket elemhalmazként hasonlítottuk össze, mert azok sorrendje a csempén belül eddig is exportonként változott.
- Minden más réteg a rajzolási sorrendben is egyezik.

| Futtatás | Eredmény |
|---|---|
| Generikus 4.27.0 teljes tesztkészlet | 669 sikeres, 5 kihagyva (lásd a generikus kiadást) |
| HU tesztek a 4.27-tel (előbeállítás, előírás-táblák, teljes szöveg, övezeti felugró ablak, memóriás láncok, nézegető) | 25 sikeres, 1 kihagyva (valós terv nélkül) |
