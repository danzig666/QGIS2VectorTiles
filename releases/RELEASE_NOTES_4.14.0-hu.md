# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.14.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.14.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.14.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.14.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Minden beépített QGIS jelkulcstípus a webtérképen (generikus 4.14.0)
Eddig hiányzott vagy csak közelítő volt, most úgy néz ki, mint a QGIS-ben:
- **Lineburst** vonal (színátmenet a vonalon keresztben, kerek végekkel és törésekkel);
- **interpolált vonal** (a vonal mentén változó szín és vastagság);
- **raszterkép-vonal** (a kép a QGIS méretében, kezdetével és irányával ismétlődik);
- **vektormező-jel** (vonal minden pontból a vektora szerint);
- **összevont elemek** és **fordított poligon** megjelenítő;
- **hőtérkép** (a böngésző rajzolja, a QGIS sugarával és színeivel);
- **pontcsoportosítás (cluster)**: a pontok a QGIS-hez hasonlóan csoportosulnak minden nagyításon, a csoport jelével és darabszámával;
- **pont-széthúzás (displacement)**: az egymást takaró pontok gyűrűn, koncentrikus gyűrűkön vagy rácson a középpont körül, a körrel vagy rácsvonalakkal.

### Javítások
- **A mintázatos kitöltések mérete pontos és élesek.** A képernyő-egységben megadott pont-, SVG-, raszter- és vonalkázás-mintázatok a nagyítási szintek között 1,4-szer nagyobbak vagy kisebbek és kissé elmosódottak voltak; most minden nagyításon a QGIS méretében, pixelpontosan élesen jelennek meg. A vonalkázás távolsága is a QGIS-hez hasonlóan egész pixelre kerekedik.
- **A színátmenetes kitöltések simák.** A szivárvány és más színes átmenetek csíkosak voltak; most kb. egy színárnyalatnyi lépésekben változnak, mint a QGIS-ben.
- **Nyilak** (pl. „pointing arrow”): a nyíl teste a kezdő szélességtől a végsőig keskenyedik, a nyílhegy QGIS-méretű, és a nyíl minden kitöltési rétege megjelenik, a fekete árnyékkal együtt.
- **Jelölővonalak** képernyő-egységű ismétlési távolsággal (pl. „cat trail”): a távolság a nagyítások között akár 19%-kal eltért, most 4%-on belül egyezik a QGIS-szel.

A képernyő-egységű jelölővonal-ismétlést, pontcsoportosítást vagy pont-széthúzást használó rétegek exportja hosszabb lett (egy nagyítási szint nyolcadaira külön adatkészlet készül).

### Ismert eltérések
- A vonal képe vagy szaggatása ott újrakezdődik, ahol a vektorcsempe elvágja a vonalat (a böngészős vektorcsempék korlátja).
- A QGIS a nézetből kilógó elem látható részére illeszti a színátmenetet; a webtérkép az egész elemére.
- A mintázatos kitöltések a térképhez rögzülnek; a QGIS az elem bal felső sarkától kezdi őket, így a minta eltolódhat a saját lépésközének egy részével (mérete és kinézete azonos).
