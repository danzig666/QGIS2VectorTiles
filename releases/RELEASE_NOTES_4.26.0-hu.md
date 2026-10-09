# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.26.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.26.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.26.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.26.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Csak ebben a változatban: a HÉSZ teljes szövege övezetenként
A [`docs/hu/HESZ_SZOVEG_PROMPT.md`](https://github.com/danzig666/QGIS2VectorTiles/blob/hu-hesz/docs/hu/HESZ_SZOVEG_PROMPT.md)
két promptot ad egy nyelvi modellhez:
- **1. prompt:** a HÉSZ-ből övezetenként összegyűjt **minden rendelkezést, amelyet az övezetben alkalmazni kell**, szó szerint, egyszerű HTML-ben. Ide tartoznak:
  - az övezet saját előírásai;
  - a területfelhasználási egység közös előírásai;
  - az általános előírások;
  - a feltételesen alkalmazandók (pl. műemléki környezetben), a feltétel megnevezésével;
  - az övezeti táblázat sora és az érintett fogalmak.
- **Az 1. prompt munkamenete:**
  - előbb bekezdésenként „szerkezeti térképet” készít arról, mi kire vonatkozik;
  - utána kötelezően ellenőrzi:
    - minden bekezdés hozzá van-e rendelve;
    - övezetenként teljes-e a szöveg, szó szerint egyezik-e;
    - követte-e a belső hivatkozásokat, és jó-e a forma.
- **2. prompt:** egy új beszélgetésben függetlenül újraellenőriz.
- **A modell válasza egyetlen HTML fájl:** a több övezetre vonatkozó részek egyszer szerepelnek (közös blokkok), az övezetek beillesztik őket. A fájl böngészőben átolvasható.
- **QGIS-szkript a Python konzolhoz** (a leírásban):
  - a fájlból táblát készít („HÉSZ övezeti előírások szövege”: `szab_ov`, `eloiras_html`) egy GeoPackage-ben, és hozzáadja a projekthez;
  - jelzi a hibákat: ismeretlen vagy fel nem használt blokk, hiányzó övezet.
- **Előbeállítás:** felismeri a táblát.
- **Webtérkép:** a telekinformációban minden övezet alatt lenyitható „A(z) Lke-1 övezet teljes előírásai”. Az övezetre kattintva a felugró ablakban is ott van.

A kész szöveget egy embernek ellenőriznie kell; a hatályos rendelet az irányadó.

### A generikus 4.26.0 változásai
- A telekinformáció egy opcionális táblából megmutatja az övezet teljes előírás-szövegét (egyszerű
  HTML). A szöveg csak lenyitáskor töltődik le. Exportkor és a böngészőben is kitisztítja a szöveget:
  nem maradhat benne link, kép, szkript vagy formázás.

| Futtatás | Eredmény |
|---|---|
| Teljes tesztkészlet a HU változaton: egység + QGIS tesztek | 553 sikeres, 6 kihagyva |
| Teljes tesztkészlet a HU változaton: böngésző tesztek | 126 sikeres (egy generikus teszt nem számolt a változat saját kiegészítőjével; javítva) |
| HU tesztek: átalakító szkript a mintával, előbeállítás, felugró ablak teljes szöveggel | 12 sikeres, 1 kihagyva (valós terv nélkül) |
