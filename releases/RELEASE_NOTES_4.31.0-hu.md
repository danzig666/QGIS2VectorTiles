# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.31.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.31.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.31.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.31.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Újdonságok (generikus 4.31.0)
- **Közzététel saját szerverre SSH / SFTP-n** (*Destination* fül → *SSH / SFTP server*). A térkép közvetlenül a megadott mappába kerül, verziózás nélkül (nincsenek kiadásmappák, nincs visszaállítás). Csak az új és megváltozott fájlok töltődnek fel, ideiglenes néven, majd átnevezéssel a régi helyére, az `index.html` utoljára, így a látogató sosem lát félkész térképet. Az azonos nevű fájlokat lecseréli, a mappában lévő többi fájl megmarad, és csak azt törli, amit korábban a QWebMap töltött fel. Belépés kulcsfájllal, ssh-agenttel vagy **jelszóval** (kulcs jelmondata is lehet), csak erre a munkamenetre megadva vagy titkosítva a QGIS-be mentve (*Save in QGIS…*). A **Test connection** belép, létrehozza a mappát, ha kell, és ír, majd töröl egy próbafájlt. A gép OpenSSH kliensét használja (Windows 10/11, macOS és Linux része; jelszóhoz OpenSSH 8.4 vagy újabb kell). A szervermezőbe beilleszthető a `felhasználó@szerver:/mappa` alak is.

- **Az extent rajzolása kattintással** (*Map* fül → *Extent* → *Draw…*): egy kattintás az egyik sarokra, egy a szemköztire, mint a QGIS saját téglalaprajzolóinál; nem kell nyomva tartani az egérgombot (a húzás is működik). Bekapcsolt QGIS-illesztésnél a sarkok illesztődnek, az állapotsor mutatja, melyik sarok következik, jobb gomb vagy Esc megszakítja.

### Javítások: hiányzó elemek, szabályok és feliratok
- **Ha egy szabály kifejezése egyetlen elemen hibát ad** (pl. szöveg egy szám mezőben), a szabály többé nem marad ki a webtérképről; a QGIS-hez hasonlóan csak azt az értéket hagyja ki az export.
- **Egymásba ágyazott szabályalapú megjelenítés** (szabályon belüli szabályok): az azonosítók többé nem ütköznek; ez eddig az egész webtérkép betöltését megakasztotta. Ismétlődő stílusréteg-azonosítót mindig átnevez és jelez.
- **Az adatvezérelt érték, amely nem szám** (szöveg a felirat elforgatási mezőjében), eddig az elemet a feliratával együtt kiejtette. Az érték mostantól üres, a felirat elforgatás nélkül jelenik meg, mint a QGIS-ben.
- **A nem szám adatvezérelt X/Y pozíciójú feliratok** elvesztek. Mostantól a szokásos módon kerülnek a helyükre, mint a QGIS-ben.
- **Üres (NULL) adatvezérelt értékeknél** (feliratméret, szín, szöveg, kapcsolók) a statikus beállítás érvényes, mint a QGIS-ben (a NULL méret eddig eltüntette a feliratot).
- **Csupa nagybetűsre állított feliratok** kisbetűs adatból: eddig betűk hiányoztak a weben.
- **„Kerület mentén” (Using perimeter) elhelyezett sokszögfeliratok** eddig nem jelentek meg; most a körvonalat követik.
- **Kifejezésnek látszó nevű mezőre (pl. `a/b`) kategorizált réteg** egyetlen kategóriája sem illeszkedett.
- **Két megjelenítési szabály által rajzolt elem** ismét egy feliratot kap (eddig szabályonként egyet).
- **Üres szövegű feliratok** háttere többé nem jelenik meg.

### Javítások: rajzolás
- **Rajzolási sorrend:** a különböző kategóriájú vagy szabályú, egymást átfedő vonalak és sokszögek a QGIS elemsorrendjét követik, ott is, ahol különböző szabályú vonalak végei találkoznak (rétegenként legfeljebb 5000 átrendezett elem és 8 átfedési szint, legfeljebb 100 000 elemes rétegekben; ezen túl a réteg a szabályok sorrendjét tartja, és ezt a hűségjelentés jelzi). Az egymást fedő pontjelek is elemsorrendben kerülnek egymásra.
- **Qt ecsetmintás kitöltések** (sraffozott, rácsos, sűrű minták) eddig tömör színként takarták az alattuk lévőt; most a mintájukat rajzolják.
- **A réteg átlátszósága** (*Layer Rendering*) átkerül a webre (eddig figyelmen kívül maradt).
- **Milliméteres sokszög-körvonalsávok** a helyes oldalon vannak, és a QGIS-hez hasonló pufferelt gyűrűk (a sarkokon nincs sötét ék). **A kitöltés képernyő-eltolása** a körvonalat is elmozdítja.
- **A milliméteres shapeburst-távolság** megtartja a képernyőn mért szélességét; **kis elemek színátmenetes kitöltése** megtartja a külső színeit.
- **Térképi egységben megadott széles vonalak** végén nincs többé lépcső a csempék szélén.
- **Zárt vonalak** (gyűrűk) a Qt-hez hasonlóan az első töréspontjukon zárnak: a vastag, áttetsző körvonal kezdőpontján nincs sötétebb négyzet.
- **0 hosszú elemet tartalmazó egyéni szaggatás** többé nem hagy pöttyöket a hézagokban.
- **Nyíl kitöltés:** a körvonal a QGIS szerinti vastagságú (eddig egy képpont volt).
- **Vonal menti jelek** ismét a vonal irányába mutatnak; **hajszálvékony jelkörvonal** egy képpont széles; a pontmintás kitöltés pöttyei élesek.
- **Betűjelek** térképi egységű eltolással és méretarány-korláttal ismét eltolva jelennek meg (eddig a vonalon ültek).

### Javítások: feliratok
- **Feliratkeretek** a QGIS-hez hasonlóan fogják körbe a szöveget (magasság a betűtípus felső és alsó nyúlványából, a keret vonala a keret szélén középre igazítva); a térképi egységű keretvonal QGIS szerinti vastagságú, adatvezérelt feliratméretnél is.
- **„Pont körül” feliratok** a QGIS szerinti távolságra kerülnek a ponttól, a QGIS sorrendjében próbálják a pozíciókat, kikerülik a többi feliratot, és a kis sokszögek is megtartják a feliratukat.
- **A vonal fölé vagy alá engedett vonalfeliratok** a vonal mellé kerülnek, nem rá.
- **Vízszintes és Szabad sokszögfeliratok** kikerülik a webtérkép többi feliratát; a nézegető minden karakter saját szélességével számol, így a QGIS-ben elférő feliratok nem maradnak ki.
- **DemiBold, Medium és Light betűtípusok** a QGIS által rajzolt betűváltozatot használják; **a térképi egységű ismétlési távolság** a térképpel együtt nő; a hosszú vonalak mentén ismételt **ritkított íves feliratok** zoomonként, a QGIS-hez hasonlóan vannak elrendezve.

### Nézegető
- **A keresés felugró ablakok nélkül is működik:** a talált elemre ráközelít és megjelöli.
- **A megosztott hivatkozás** azt mutatja, amit a küldője látott (eddig keveredett a látogató megjegyzett beállításaival, és a már megnyitott térképfülbe illesztett hivatkozást figyelmen kívül hagyta).
- A *Labels switch* beállítás (*Interaction → Viewer*) érvényesül.
- A felugró ablakok végéről eltűnt a „E réteg elemhivatkozásai csak a térkép ezen változatában működnek” szöveg.

| Futtatás | Eredmény |
|---|---|
| Teljes generikus tesztcsomag (fájlonként külön folyamatban) | 812 sikeres, 5 kihagyva |
| Publish ablak, közzétételi és SSH tesztek az új funkciók után (SSH végig egy helyi OpenSSH szerveren) | mind sikeres |
| SSH jelszavas belépés kézzel, helyi OpenSSH szerveren (jelszó és PAM; szóköz, idézőjel, & és ékezet a jelszóban) | feltöltés, újrafeltöltés, rossz jelszó: rendben |
| HU tesztek a hu-hesz ágon | 13 sikeres, 1 kihagyva (HU tesztek, plugin-csomag) |
