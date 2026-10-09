# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.30.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.30.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.30.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.30.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Mely rétegek lassítják a kizoomolt térképet (generikus 4.30.0)
*Map* fül → **Zoomed-out load…**: a QWebMap minden publikált réteg elemeiből vett minta alapján megbecsüli, mennyire nehéz a réteg, amikor a webtérkép ki van zoomolva. Kizoomolva egyetlen csempe egy egész területet tartalmaz: minden elemét le kell tölteni és ki kell rajzolni, akkor is, ha kisebb egy pixelnél, és minden feliratát el kell helyezni, pedig alig fér ki belőlük néhány.

Az ablak rétegenként megmutatja, mekkora részt tesz ki a réteg a legnehezebb csempéből (elemszám, méret), és javasol:

- egy zoomot, **ahonnan az elemei látszanak**: ahol a jellemző elem már legalább egy-két pixel, azoknál a rétegeknél, amelyek ez alatt nehezek;
- egy zoomot, **ahonnan csak a feliratai látszanak**, az elemek ez alatt is kirajzolódnak: ahol a feliratoknak legalább a fele elfér, azoknál a rétegeknél, amelyek feliratai ott, ahol most kezdődnek, többnyire el sem helyezhetők.

Minden javaslat mellett pipa és egy átírható zoom áll (mellette a méretarány). Az összegzés mutatja az összes réteg együttes legnehezebb csempéjét most és a bepipált határokkal. Az **Apply the checked limits** gomb beírja őket a *Scales* oszlopba. Csak a webtérkép változik, a QGIS projekt nem. Ahol egy réteg vagy a feliratai rejtve vannak, ott csempe sem készül belőlük, így az export is kisebb és gyorsabb.

### Csak a feliratok elrejtése kizoomolva
Egy réteg feliratai elrejthetők egy méretarány alatt, miközben az elemei továbbra is látszanak: *Selected layers → Visible scales…* → **Hide only the labels when zoomed out beyond**. A *Scales* oszlopban így jelenik meg: „labels 1:10 000 –”.

| Futtatás | Eredmény |
|---|---|
| Szintetikus városi projekt (85 320 földrészlet, 47 520 épület, 38 880 házszám, alrészlet-feliratok és -határok) | kb. 10 s alatt elemezve; a legnehezebb csempe kb. 5,0 MB → kb. 1 MB a javaslatokkal (földrészletek z11-től, épületek z15-től, házszám- és alrészlet-feliratok z13-tól) |
| Generikus 4.30.0 tesztek | 84 sikeres (profil, flattener, plugin-csomag, Publish ablak, publikálási folyamat, méretarány-határok) |
| HU tesztek a hu-hesz ágon | 20 sikeres, 1 kihagyva (HU tesztek, plugin-csomag, telekinfó a böngészőben) |
