# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.44.0)](https://github.com/danzig666/QWebMap/releases/tag/v4.44.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.44.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.44.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Változott (a generikus 4.44.0-ból)
- A **vonal mentén képernyő-mértékegységben (mm, pont) elhelyezett jelek** (*Marker line*, *Interval* elhelyezéssel) minden zoomszinten ±2,2 %-on belül tartják a QGIS-ben beállított távolságot (eddig ±4,4 %). A QGIS ezt a távolságot zoomoláskor is pontosan tartja; a webes térkép minden zoomszint tizenhatodára külön számolja a helyüket, és a kis eltérés hosszú vonalakon összeadódik.
- A **jelekkel együtt rajzolt szaggatott vonal** (a szaggatás hézagaiba tett betűk vagy keresztek, pl. határvonalak keresztekkel, kóddal feliratozott körvonalak) ugyanezekben a lépésekben készül, így a jelek a hézagokban maradnak. Eddig a szaggatás minden csempehatáron újrakezdődött, a jelek pedig rácsúsztak.
- Ezek a vonalak több helyet foglalnak a csempékben (az ilyen jel- és szaggatott vonalak nagyjából kétszer annyit); a többi vonal nem változik.

### Javítva
- Az **eltolt részekből álló jelek** (több, egymástól távolabb tett jelből összerakott szimbólum, pl. két körből álló kerékpár) **vagy széles betűjelek** szokásos sűrűségben ismétlődő mintaként rajzolódtak, amely a sarkoknál meghajlította őket, és a csempehatárokon újrakezdődött. Most ismét egyenként kerülnek a helyükre; mintaként csak a nagyjából 1 mm-nél sűrűbb jelek rajzolódnak.
