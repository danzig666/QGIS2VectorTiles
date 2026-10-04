# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.13.2)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.13.2)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.13.2**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.13.2 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.


### A kulcshibák azonnal kiderülnek (generikus 4.13.2)
A *Layer "…": key not unique* hiba eddig csak a teljes csempe-export után jelent meg, gyakran
percekkel később. Mostantól a kulcsokat az export első másodperceiben ellenőrzi, még a csempék
előtt. Az üzenet megnevezi a kulcsmező(ke)t, és megmondja, hol lehet módosítani (*Interakció*
fül, a telekrétegnél a *Telekinfó* fül). A telekinfó hiányzó rétegeit és mezőit is azonnal jelzi.

### Export-gyorsítótár: közös GeoPackage
Ha több réteg ugyanabban a GeoPackage-ben volt, bármelyik szerkesztése (vagy a fájlba mentett
stílus) miatt a fájl összes rétege megváltozottnak látszott, és mind újra elkészült. Mostantól
minden réteg csak a saját tábláját figyeli. Ha egy réteg kulcsa (egyedi azonosítója) változik,
csak az a réteg készül újra, és a napló ezt írja: "feature key (unique id) changed".

A frissítés utáni első export teljes lesz, mert az exportáló kód változott. A további exportok
újra felhasználják a változatlan rétegeket. Az export naplója felsorolja, melyik réteg készült
újra és miért (*Redone (…)* sorok).

### Méretarány oszlop
A webes méretarány-korlát nélküli rétegeknél a Térkép fül *Méretarány* oszlopa eddig csak
"(QGIS)"-t mutatott. Mostantól a réteg QGIS-beli tartományát írja ki, pl. "(QGIS 1:2 000 –)".

| Futtatás | Eredmény |
|---|---|
| Folyamat tesztek | 5 sikeres (ismétlődő kulcsnál az export a csempék előtt leáll) |
| Export-gyorsítótár tesztek | 4 sikeres (új: két réteg egy GeoPackage-ben, mindkettő csak a saját változása miatt készül újra; a javítás nélkül hibázik) |
| Közzététel ablak rétegtesztjei | 4 sikeres (új: a Méretarány oszlop a QGIS-tartományt mutatja) |
| Folyamat, telekinfó, közzététel ablak, raszter és gyorsítótár tesztek együtt | 31 sikeres |
