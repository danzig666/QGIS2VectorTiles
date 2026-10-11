# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.33.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.33.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.33.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.33.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

Ez a kiadás a QGIS-jelkulcsok és -feliratok átalakításának átvizsgálásakor talált hibákat javítja. A Publish ablak a 0–16-os zoomtartományt exportálja, a webes térkép pedig 22-ig nagyít, a korábbi tesztek viszont többnyire szűk tartományt (pl. 14–19) használtak. A hibák többsége egyszer, a 0-s vagy az utolsó csempezoomon kiszámolt érték, térképi egységet és millimétert keverő méret, illetve a QGIS saját szabályaitól eltérő viselkedés volt.

### Nagyítás az utolsó csempezoom fölé (17–22)
- **A milliméteres jelek megtartják a méretüket:** a nyilak, pontmintázatok, shapeburst sávok, ragyogás és árnyék, eltolt jelek és vektormező-vonalak zoomonként a duplájukra nőttek (vagy eltűnésig ritkultak). A finom, térképi egységű szaggatás itt is rajzolt szaggatásra vált.
- **A csak az utolsó csempezoom fölött látszó szabályok, rétegek és feliratok** (pl. egy 1:1000-től látszó részletszabály) kimaradtak a webes térképről; most megjelennek.
- **A `@map_scale` kifejezések** (szabályszűrők, adatvezérelt méretek és kapcsolók) az utolsó csempezoom fölött is változnak, a feliratszűrők nem ragadnak le az első zoom értékénél, a `@zoom_level` működik, és a pontcsoportok nagyításkor újracsoportosulnak.
- **A térképi egységű mintázatos kitöltések** tovább nőnek a térképpel (eddig 2–16-szor kisebbek maradtak), és élesek maradnak.
- Az ehhez szükséges többletrajzolás csempénként korlátozott. A sok ilyen jelet tartalmazó térképek utolsó zoomjának csempéi nagyobbak lesznek: egy 64 rétegű településrendezési tervnél a csempék kb. harmadával nőttek, az export kb. ötöd–harmad résszel tovább tartott.

### Térképi egység és milliméter
- **Vegyes egységű feliratok:** a térképi egységű, milliméteres eltolású vagy távolságú felirat eltűnt, a milliméteres, térképi egységű távolságú felirat pedig a pontra, vonalra vagy a sokszög közepére került. Az eltolás, a pont körüli és a vonaltól mért távolság most követi a zoomot; a térképi egységű szöveg kerete nagyításkor nem nő óriásira; a képernyőméretű szöveg térképi egységű kerete nem 1 képpontos.
- **A térképi egységű egyéni szaggatás** (és a milliméteres szaggatás térképi egységű vonalon) 0-s zoomtól folytonos vonal volt.
- **A térképi egységű térközű vagy eltolású vonalmintázatos kitöltés** és a nyíl térképi egységű árnyékeltolása a 0-s zoom méretén ragadt.
- **A min./max. méret- és méretarány-korlátok** (map unit scale) kétszer érvényesültek vagy kimaradtak a jeleknél, mintázatoknál, térközöknél és a szétválasztott körvonalaknál.
- **A fokban (EPSG:4326) vagy lábban mért projektek**, a megjelenítő **referenciaméretaránya**, a **„Meters at scale”** Web Mercator projektekben (1/cos(szélesség)-szer túl nagy volt) és a **geometriagenerátor mértékegysége** a QGIS szerint alakul át.
- **A térképi egységű sűrűségű véletlen jeles kitöltés** miatt a Publish export kifutott a memóriából; a nagyon sűrű kitöltés most egyszínű tónus.

### Szabályok és feliratok
- **Az ELSE / „all other values”** szabály olyan elemeket is rajzolt, amelyeket a QGIS elrejt (kikapcsolt vagy méretarányon kívüli testvérszabály).
- Egy jelréteg adatvezérelt **bekapcsolása** már nem rejti el a feliratokat; a felirat saját **méretarány-tartománya** akkor is érvényes, ha a szabálynak is van ilyen.
- **A sokszögfeliratok** kikerülik egymást és betartják a *Never overlap* beállítást; a nézegetőben működik a **Label every part**, az **Outside polygons** elhelyezés, a kis sokszögek és a HTML-formázott feliratok.
- **Vonalfeliratok:** a *Horizontal* vonalfelirat a vonal közepére kerül (nem az első töréspontjára); a fejjel lefelé álló feliratok megfordulnak; a csempehatáron átnyúló hosszú feliratok, az ismételt íves feliratok és a *Merge connected lines* (a QGIS-hez hasonló összevonással) működik.

### Jelek
- **A térképi egységű sraffozás** eltűnt, ha egy sraffvonal a sokszög sarkát érintette; a nagyon nagy sokszögek sraffozása figyelmeztetés nélkül kimaradt.
- **Az áttetsző shapeburst kitöltés** sávjai hibás színné adódtak össze; a jel **átlátszósága** megmarad a megrajzolt részein (jelvonalak, sraffozás, pontrácsok), a geometriagenerátor saját jele pedig a saját átlátszóságát tartja, mint a QGIS-ben.
- **A geometriagenerátoros kitöltés** megtartja a körvonalát; a pontrétegen lévő generátor már nem osztozik az adatain az egyszerű jellel.
- Átalakul a **térképi egységű kitöltéseltolás**, az **adatvezérelt jel-átlátszóság**, a pontmintázatok **százalékos eltolása**, a **„Draw line only inside polygon”**, a **vonalkázott vonal** adatvezérelt térköze/szöge/hossza, a **vonal kezdete előtti jelek** (negatív eltolás) és az **ismétlődő első töréspont utáni töréspontjelek**.
- **A milliméteres vonaleltolás** nem rajzolódik vékony, elkeskenyedő csíkként; a sűrű, kis zoomú csempék nem hagynak ki elemeket szó nélkül; a hűségjelentést nem árasztja el a 0-s minimális zoom.

| Futtatás | Eredmény |
|---|---|
| Teljes generikus tesztcsomag (fájlonként külön folyamatban) | 1342 sikeres, 5 kihagyva |
| A QGIS beépített stílustára (118 stílus), QGIS és web, 4.32.0-hoz képest | 109 képpontra azonos, a többi szemre azonos vagy közelebb van a QGIS-hez (az út közben talált egy feliratos visszalépést javítottuk) |
| 57 feliratbeállítás, QGIS és web, 4.32.0-hoz képest | 20 jobb, 36 változatlan, egy sem rosszabb |
| Négy valódi projekt Publish alapbeállítással, 4.32.0-hoz képest | hiba nélkül; háromnál 2 %-on belüli csempeméret, egynél (64 réteg) +36 % csempe (a jelek mérete az utolsó csempezoom fölött is megmarad) |
| HU tesztek a hu-hesz ágon | 13 sikeres, 1 kihagyva (HU tesztek, plugin-csomag) |
