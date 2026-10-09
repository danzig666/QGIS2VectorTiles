# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.28.2)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.28.2)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.28.2**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.28.2 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Gyorsabb csomagolás (generikus 4.28.2)
Egy város térképénél sokáig tartott a „PMTiles: validating...” és a „Validating the web release...” lépés. Mindkét ellenőrzés megmarad: hibás archívum vagy jóvá nem hagyott mező nem kerülhet ki. Viszont már nem olvassák végig újra az egész várost. Egy Debrecen-méretű tesztterképen (85 ezer földrészlet, 47 ezer épület, 0–16. zoom) a csomagolás 107 s helyett 32 s.

- **Az archívum ellenőrzése** a teljes szerkezetet továbbra is megnézi: minden csempe helyét és méretét, a csempék számát, a zoomszinteket. A mintából az 512 KB-nál kisebb csempéket elemenként bontja ki, mint eddig. A nagy, kicsinyített csempéket (egy-egy ilyen a teljes várost tartalmazza, több MB) a tömörítésük és a rétegeik alapján ellenőrzi. 41 s → 8 s.
- **A webes kiadás ellenőrzése** ugyanazt az archívumot nem bontja ki másodszor: az SHA-256 ellenőrzőösszeg egyezése után csak a szerkezetét nézi. Azt, hogy jóvá nem hagyott mező nincs a csempékben, az archívum írásakor összegyűjtött mezőlistából ellenőrzi; máshol készült archívumot továbbra is csempénként olvas. 50 s → 2,5 s.
- A sérült csempét a hiba nevén nevezi („not MVT”), nem nyers kitömörítési hibával áll le.

| Futtatás | Eredmény |
|---|---|
| Generikus 4.28.2: publikálási tesztcsomagok (17 fájl) és a review-javítások | 192 sikeres, 5 kihagyva |
| HU tesztek és a PMTiles-tesztek a hu-hesz ágon | 32 sikeres, 2 kihagyva |
