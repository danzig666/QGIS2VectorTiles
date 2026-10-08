# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.25.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.25.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.25.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.25.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Csak ebben a változatban: az övezeti előírás tábla elkészítése nyelvi modellel
A [`docs/hu/HESZ_ELOIRAS_PROMPT.md`](https://github.com/danzig666/QGIS2VectorTiles/blob/hu-hesz/docs/hu/HESZ_ELOIRAS_PROMPT.md)
két promptot ad egy nyelvi modellhez (ChatGPT, Claude, Gemini …):
- **1. prompt:** a HÉSZ szövegéből és a térkép övezeti jeleiből elkészíti a CSV táblát.
  - Minden értékhez forráshelyet és szó szerinti idézetet ad.
  - Kötelező önellenőrzést végez:
    - teljesség, jelegyezés;
    - visszakeresés a forrásban, oszloponkénti újraolvasás;
    - ésszerűségi vizsgálat, CSV-forma.
- **2. prompt:** egy új beszélgetésben (lehetőleg másik modellel) a HÉSZ-ből újra kiolvas minden értéket, és tételesen összeveti a táblával.
- **A leírás többi része:**
  - a 22 oszlop;
  - a lépések az övezeti jelek kigyűjtésétől a QGIS-be töltésig;
  - egy minta CSV.

  A minta CSV-t egy teszt be is tölti a QGIS-be, és ellenőrzi, hogy az előbeállítás felismeri.

Az előbeállítás minden oszlopot magyar címmel mutat; a QGIS-ben megadott mezőálnév felülírja a címet.
A kész táblát egy embernek szúrópróbával ellenőriznie kell.

### Javítások ebben a változatban
- **Melyik réteg az előírás tábla:** a geometria nélküli, „HÉSZ”/„előírás” nevű tábla az elsődleges. Korábban egy azonos mezőkkel rendelkező övezeti poligonréteg (pl. „Övezeti jelek”) is elvihette.
- **Övezetkód az övezet felugró ablakában:** az előbeállítás akkor is felveszi, ha az övezeti réteget csak később jelölöd ki publikálásra.
- **Jelek egyeztetése:** a `12` és a `12.0`, illetve a szóközzel kezdődő vagy végződő jelek is egyeznek a táblával.

### A generikus 4.25.0 változásai
- Az **áttekintő térkép**, a **3D nézet** és a **rajzolás** alapból ki van kapcsolva (Interaction fül → Viewer).
  - A 3D nézetnek saját kapcsolója van.
  - Ha a 4.24-ben ezek be voltak kapcsolva, kapcsold be újra őket.
- A 4.22–4.24 átnézéséből kb. 40 javítás, például:
  - **Előbeállítás vagy beállításfájl:** a betöltése után megmaradnak a rétegenkénti beállítások.
  - **Domborzat:** a hibás domborzati réteg figyelmeztetés, nem állítja le az exportot.
  - **Rajzolás:** a linkben kapott rajz azonnal látszik.
  - **Ellenőrzés fül:** felsorolja a külső szolgáltatókat.

  A teljes lista a generikus kiadásban van.

| Futtatás | Eredmény |
|---|---|
| Előbeállítás, a prompt minta CSV-je, övezeti előírások (preset, QGIS és böngésző tesztek) | 9 sikeres, 1 kihagyva (valós terv nélkül) |
| Generikus 4.25.0 teljes tesztkészlet | 660 sikeres, 5 kihagyva (lásd a generikus kiadást) |
