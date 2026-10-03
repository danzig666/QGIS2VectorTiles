# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.6.6)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.6.6)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.6.6 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.6.6 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítás (generikus 4.6.6): hiányzó feliratok / kitöltések méretarány-tartomány módosítása után
- **A hiba:** a földrészlet-feliratoknak méretarány-tartományt adtak (1:4000 – 1:100), majd
  ugyanabba a mappába újra exportáltak. A webtérképről eltűntek a helyrajzi számok, és
  a földrészletek átlátszó kitöltésének négy zoomszintje is hiányzott.
- **Az ok:** az export gyorsítótár a változatlan adatot az új (zoomtartományt tartalmazó) néven
  vette újra. A GeoPackage-ben viszont a régi táblanév maradt, így a csempézés nem talált
  adatot. Mostantól a csempézés a fájlban ténylegesen lévő táblát olvassa.
- **Kit érint:** minden olyan réteget, amelynek a szabályai új zoomtartományt kaptak változatlan
  adattal: felirat- vagy jel-méretarány, a *Scales* oszlop, vagy a max. zoom módosítása után.
  Ha egy korábbi exportból így hiányzott réteg, exportáljon újra a 4.6.6-tal.
- A frissítés utáni első export egyszer mindent újragenerál, utána a gyorsítótár a megszokott
  módon működik.
- **A `foldreszletek.qml` stílus ellenőrizve:** a webtérképen is pontosan 1:4000 és 1:100 között
  jelennek meg a feliratok. Átkerül még:
  - a kifejezés (közterületnél „(hrsz) közterületnév”);
  - a `fekves` szerinti térképi egységű betűméret (6 vagy 4 m);
  - az 59%-os átlátszatlanság;
  - a szabad (elforgatott) elhelyezés.

  A „csak 3 px-nél nagyobb feliratok” beállítás nem kerül át, de ebben a tartományban nincs
  hatása (a legkisebb felirat 1:4000-nél kb. 3,8 px).
