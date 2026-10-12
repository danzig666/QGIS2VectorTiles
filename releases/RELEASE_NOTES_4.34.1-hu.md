# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.34.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.34.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.34.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.34.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítások: az exportot elrontó nevek és kifejezések (generikus 4.34.1)
A 4.34.0 `Regioni d'Italia` javítása után átnéztük az exportáló minden olyan pontját, ahol a projekt szövegéből QGIS-kifejezést épít, és további hasonló hibákat találtunk. Ezek miatt egy réteg, szabály vagy felirat csendben hiányozhatott a webes térképről, vagy leállt az export:
- **Idézőjelet tartalmazó mezőnevek** (`my"field`) feliratban, felugró ablakban, szűrőben, 3D magasságként vagy az összes mezőnél, valamint **nagybetűs szűrőmezők** (`Zona`): a mező hiányzott a csempékből, vagy leállt az export.
- **`-- megjegyzéssel` végződő kifejezések** (szabályszűrők, feliratok, adatvezérelt tulajdonságok, geometriagenerátorok, feliratpozíciók): a megjegyzés „elnyelte” az exportáló által utána írt záró zárójelet.
- **Fordított perjel mint feliratsortörő karakter** és **érvénytelen mintájú egész szavas feliratcserék**: most úgy kezeli, mint a QGIS (az érvénytelen cserét kihagyja, a felirat megmarad).
- **`$area`, `$length` és `$geometry` megjegyzés vagy escape-elt idézőjel után** nem a réteg vetületében mért (hibás érték, hibaüzenet nélkül).
- **Szövegmezőből vett hőtérkép-súlyok** számként olvasódnak, mint a QGIS-ben.
- **Szóközt, idézőjelet vagy `$` jelet tartalmazó exportmappák:** az előnézet indítószkriptje és az eredményréteg idézőjelezi az elérési utat.

| Futtatás | Eredmény |
|---|---|
| Új tesztek minden esetre (mindegyik elbukik a javítás nélkül) | mind sikeres |
| Kifejezés-, felirat-, szabálylapító (flattener), végponttól végpontig és közzétételi tesztfájlok az integráció után | 236 sikeres |
