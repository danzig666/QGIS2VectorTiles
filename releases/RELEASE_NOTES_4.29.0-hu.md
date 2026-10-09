# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.29.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.29.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.29.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.29.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Néhány száz fájl több tízezer helyett (generikus 4.29.0)
Debrecen térképe több mint 40 ezer fájl volt, ezért bárhová lassú volt feltölteni. A fájlok szinte mind a kereső apró darabjai voltak (az egész város utca- és házszámkeresője), illetve a felugró ablakok és hivatkozások rekordjai. Mostantól ezek az indexek egyetlen fájlt alkotnak (`.pack`). A nézegető tartománykéréssel csak azt a részt olvassa ki, amire egy kereséshez vagy kattintáshoz szükség van, ugyanúgy, ahogy a térképarchívumot. Egy Debrecen-méretű teszten (2012 utca, 37 838 házszám, 110 ezer helyrajzi szám):

| | 4.28 | 4.29 |
|---|---|---|
| Keresőindex és rekordok | 47 959 fájl, 45,8 MB | 2 fájl, 4,8 MB |
| A kereső indulásakor letöltött manifest | kb. 7 MB | 224 KB (tömörítve 64 KB) |

Egy utcanévre keresve a nézegető kb. 40 KB-ot tölt le. A telekinfó rekordjai is egy fájlba kerültek. Ami a térképarchívumot ki tudja szolgálni, az ezeket is ugyanúgy szolgálja ki (tartománykérés, külön tömörítés nélkül). A webszervernek továbbra is támogatnia kell a tartománykérést (HTTP `Range`): enélkül a térképarchívum nem olvasható, és a nézegető a térkép helyett hibaüzenetet mutat.

### Az extent kézzel is berajzolható
*Map* fül → *Extent* → **Draw…**: az ablak félreáll, a QGIS térképen egy téglalapot húzva kijelölhető a publikált terület. Esc: mégse.

### Közvetlenül a Web menüben
**Web → QWebMap: Publish Web Map…**, QWebMap almenü nélkül (a Web eszköztáron továbbra is ott van).

| Futtatás | Eredmény |
|---|---|
| Debrecen-méretű keresőindex | 47 959 fájl / 45,8 MB → 2 fájl / 4,8 MB |
| Generikus 4.29.0 tesztek | 244 sikeres, 5 kihagyva (publikálás, indexek, böngészős tesztek, plugin-csomag, Publish ablak) |
| HU tesztek a hu-hesz ágon | 18 sikeres, 1 kihagyva (HU tesztek, plugin-csomag, telekinfó a böngészőben) |
