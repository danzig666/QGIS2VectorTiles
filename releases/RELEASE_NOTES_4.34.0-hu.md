# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.34.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.34.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.34.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.34.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Exportok, amelyek hibával álltak le vagy nem értek véget (generikus 4.34.0)
- **Aposztróf a réteg nevében** (`Regioni d'Italia`): a név idézőjelezés nélkül került egy QGIS-kifejezésbe, a réteg minden szabálya hibára futott, és nem maradt mit közzétenni. Most idézőjelezve van (aposztróf és fordított perjel is).
- **A durva adatok nagy exportja befejeződik.** Egy kontinens országhatárai 16-os nagyításig több tízmillió kitöltött csempét jelentettek: az export órákig a vége felé látszott elakadni. Ha túl sok csempe lenne (200 000 fölött), a csempék annál a nagyításnál érnek véget, amely az adat minden részletét tartalmazza, a webes térkép pedig tovább nagyít rájuk – ugyanúgy néz ki. A kis és részletes térképek (egy település telkei, épületei) minden nagyítást megtartanak. Az exportnapló és a hűségjelentés (fidelity report) jelzi, hol érnek véget a csempék.
- **Nincs mit közzétenni:** az export most rögtön leáll, és megnevezi az okot (a hibás szabályokat), a végén megjelenő érthetetlen „No MBTiles file” hiba helyett. Az egyébként sikeres exportban hibára futott szabályokat a Közzététel ablak felsorolja.

### A térképnézettől függő kifejezések
- Az **`@map_extent`, `@map_extent_center`, `@map_extent_width`, `@map_extent_height`, `@map_crs`, `@map_rotation`** és hasonló változók az exportban üresek voltak, így a térkép közepe szerint szűrt szabályok, a célkereszt és a belőle generált feliratok hiányoztak. Most a webes térkép kezdőnézetét (az export kiterjedését) tartalmazzák. A QGIS mozgatáskor frissíti őket, a webes térkép a kezdőnézet értékeit tartja meg; ezt a hűségjelentés jelzi.

### Szimbólumok és feliratok
- A **színátmenetes és shapeburst kitöltések adatvezérelt színe** (véletlen vagy attribútumonkénti szín) elemenként érvényesül (eddig a statikus szín jelent meg).
- A **geometriagenerátoros feliratok sokszögrétegen** a generált geometrián jelennek meg (eddig a sokszög közepére kerültek).

| Futtatás | Eredmény |
|---|---|
| Két felhasználói projekt, amely hibára futott vagy nem ért véget (térképnézet-kifejezések aposztrófos rétegnévvel; országhatárok és 2143 földrengés Délkelet-Ázsiában) | mindkettő exportálódik: az első másodpercek alatt, a második kb. 2 perc alatt órák helyett |
