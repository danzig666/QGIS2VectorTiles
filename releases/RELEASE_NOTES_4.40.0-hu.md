# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.40.0)](https://github.com/danzig666/QWebMap/releases/tag/v4.40.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.40.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.40.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Új
- **Diagramok:** a QGIS kör (Pie), oszlop (Histogram), halmozott oszlop (Stacked bar) és szöveges (Text) diagramjait a böngésző rajzolja meg az objektumok értékeiből, a QGIS méreteivel (fix vagy interpolált, terület szerinti skálázás), színeivel, kezdőszögével és irányával, oszlopszélességgel, tájolással, valamint a méretarány-tartománnyal és a *Show* feltétellel. Nem követett (a jelentésben szerepel): a többi adatvezérelt diagrambeállítás, a hisztogram tengelye és az *Around* elhelyezés távolsága.
- **Animált jelek (Animated marker):** a GIF, WebP és animált PNG jelek a QGIS képkocka-sebességével mozognak a webes térképen. Nyomtatáskor és csökkentett mozgást kérő rendszereken az első képkocka látszik.
- **Szelektív maszkolás (közelítés):** a címke maszkja (Mask) a térkép háttérszínű glóriája lesz (a maszk méretével és átlátszatlanságával), a maszk jel (Mask marker) pedig a saját alakja háttérszínnel. A QGIS csak a kiválasztott szimbólumrétegeket vágja ki, a webes térkép mindent eltakar alatta; egyszínű háttér fölött ez pontos. Jelentésben: `Q2VT_MASK_APPROXIMATE`.

### Változott
- A bővítmény metaadatai a szerzőt (Kőszeghy Ábel) és az eredeti QGIS2VectorTiles bővítmény szerzőjét (Jossef Kanter) is megnevezik.
