# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.43.0)](https://github.com/danzig666/QWebMap/releases/tag/v4.43.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.43.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.43.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

A QGIS-stílusok a weben jobban hasonlítanak a QGIS-ben látottakhoz. A QGIS Hub 853 stílusát
a QGIS és a webes nézet is megrajzolta: az átlagos eltérés 5,5 %-ról 3,4 %-ra csökkent, a 20 %-nál
jobban eltérő stílusok száma 81-ről 42-re.

### Javítva (a generikus 4.43.0-ból)
- **Jelekből álló vonalkázás** (*Line pattern fill*: nyilak, betűjelek, szaggatott vagy többrétegű vonalak) úgy rajzolódik, ahogy a QGIS kitölti, a QGIS saját, egész pixelre kerekített sűrűségével; a véletlenszerű pöttyökből álló „ceruzás” vonalkázás a QGIS pontos sűrűségét tartja. Eddig sűrű, sötét háló lett belőlük.
- A **nulla vastagságú vonalkázás** a QGIS egypixeles hajszálvonala (eddig nem látszott).
- **Sűrű jelölővonalak** (a jelek a méretüknél sűrűbben: moha, „vázlatos” vonal) minden jelet mutatnak; a webes térkép eddig nagyjából minden ötödiket.
- A **derengő (elmosott) vonalak** olyan messzire halványulnak, mint a QGIS elmosása.
- A **beágyazott képpel rajzolt raszteres vonalak** a képet mutatják (eddig halvány előnézet volt).
- Az **aggregáló függvényeket használó geometriagenerátorok** (`collect`, `sum`...) megrajzolják a geometriát; eddig üres volt.
- A **`@symbol_color`** egymásba ágyazott szimbólumokban és geometriagenerátorokban az őt rajzoló szimbólum színét adja.
- A nyilakból vagy más jelekből álló **Hashed line** rétegek azzal a jellel rajzolódnak.
- A szöggel elforgatott **Point pattern fill** a rácsot forgatja el, a jelek állva maradnak, mint a QGIS-ben.
- A **több rétegből álló, adatvezérelt szöggel forgatott jelek** minden rétege megtartja a saját szögét (a mérővonal nyílhegyei eddig keresztben álltak).
- A **vonalból számolt jelértékek** (`$length`...) a teljes vonalat veszik.
- A **nyilak vetett árnyéka**, a kitöltés effektje a körvonalán, és az első/utolsó töréspont jelének eltolása a vonal mentén úgy működik, mint a QGIS-ben.
- A **kis körök** már nem halványabbak, mint a QGIS-ben.
- A **„meters at scale” mértékegységben megadott méretű szimbólumok** eddig üresek voltak.
- Megrajzolódnak a **forgatott, horgonypontos SVG-jelek**, amelyeket a QGIS túl nagynak jelent.
- A csak világosító keverési módokkal (*Screen*, *Addition*, *Lighten*, *Dodge*) rajzolt rétegek kimaradnak, és a jelentés jelzi ezt: fehér térképen a QGIS-ben sem látszanak.

### Javítva: eddig hibával leálló exportok
- A véletlenszerű körvonalat simító geometriagenerátorok (több millió töréspont) kifogytak a memóriából.
- Egy mintás kitöltés, amelynek jele túl nagy a sprite-hoz, leállította az exportot; most a jelentés jelzi, és kimarad.
- Egy felület geometriagenerátora, amely képernyő-mértékegységű eltolással rajzolt vonalat, leállította az exportot.

### Változott
- A webes nézet **MapLibre GL JS 6.14.0**-t használ (eddig 6.11.2): többek között a `line-offset` a töréspontoknál is megtartja a vonal vastagságát, a térkép akkor is betöltődik, ha egy csempe hibát ad, a kitöltések körvonala látszik a 3D nézetben, és egyes iPadeken nem marad üres a térkép. A nézet most két MapLibre-fájlt tölt be (`maplibre-gl.mjs`, `maplibre-gl-worker.mjs`) három helyett.
