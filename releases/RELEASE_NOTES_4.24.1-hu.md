# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.24.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.24.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.24.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.24.0–4.24.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Csak ebben a változatban: övezeti előírások a webtérképen
Ha a projektben van **övezeti előírás tábla** (pl. „HÉSZ övezeti előírások” `szab_ov` mezővel), az
előbeállítás (Preset… → Magyar településrendezési terv) felismeri:
- a **telekinformációban** minden telekrész alatt ott vannak az övezet előírásai (beépítettség,
  épületmagasság, telekterület, zöldfelület …);
- egy **övezetre kattintva** a felugró ablak is felsorolja őket („A(z) Lke-2 övezet előírásai”);
- a **hivatkozás** mező link: https:// címre (pl. a rendelet a Nemzeti Jogszabálytárban), vagy a
  térképpel publikált dokumentumra (pl. a HÉSZ PDF).

### A generikus 4.24.0 újdonságai
- **Info fül**: kiadó, rendelet, hatályosság és az adatok állapota (a cím alatt és a nyomtatáson), valamint
  **dokumentumok** (PDF, Word …) a térképpel együtt publikálva; a felugró ablakban a nevükre linkelnek.
- **WMS** szolgáltatások alaptérképként (Alaptérkép fül → *WMS hozzáadása…*, pl. ortofotó).
- **Áttekintő térkép** a sarokban.
- **Házszám keresés**: „Fő utca 12” (az utca egy mezőből, vagy a legközelebbi OSM utcából).
- **Rajzolás**: pont, vonal, terület, szöveg — a megosztott linkben marad, GeoJSON/KML-be menthető.
- **3D nézet**: épületek magasság-mezőből, **domborzat** DEM rétegből, **domborzatárnyékolás**,
  mért vonal **magassági metszete**.
- **Térképkivonat nyomtatás**: A4/A3, álló/fekvő, északjel, léptékvonalzó, fejléc (kiadó, rendelet,
  hatályosság, méretarány, nyomtatás dátuma).

| Futtatás | Eredmény |
|---|---|
| Előbeállítás + övezeti előírások (preset és böngésző teszt) | 5 sikeres, 1 kihagyva (valós terv nélkül) |
| Generikus 4.24.x tesztek (böngésző, publikálás, Publish ablak) | lásd a generikus kiadást |
