# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.10.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.10.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.10.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.10.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

A QGIS beépített szimbólumtárát a QGIS-ben és a böngészőben is kirajzoltuk, és összehasonlítottuk.
Ami a weben még nem egyezett, azt ez a kiadás javítja.

### Színátmenetes és shapeburst kitöltések (generikus 4.10.0)
- **Eddig:** a színátmenetes kitöltés egyszínű volt a weben, a shapeburst kitöltés pedig kimaradt
  (és a jelentés jelezte).
- **Most:** a QWebMap exportkor finom színsávokra bontja őket, objektumonként: legfeljebb 64 sáv,
  két szomszédos sáv között kb. 4 színszint. Ez minden típusra érvényes: lineáris, sugaras és kúpos;
  két szín vagy színskála; pad, reflect és repeat. A színátmenet az objektum befoglaló téglalapjához
  igazodik, ahogy a QGIS rajzolja.
- **Eredmény:** a beépített színátmenetes szimbólumok átlagosan kb. 1 színszintre egyeznek a
  QGIS-szel.
- **Korlátok** (a hűségjelentés jelzi őket):
  - A nézethez (viewport) igazított színátmenet objektumonként jelenik meg.
  - A shapeburst elmosás nincs alkalmazva.

### Ragyogás és árnyék vonalakon (generikus 4.10.0)
Egyszerű vonalon a **külső ragyogás** és a **vetett árnyék** effekt elmosott vonalrétegként
jelenik meg. A jelölők effektjeit a QWebMap eddig is belerajzolta a jelölőképekbe, de tévesen
figyelmeztetett, hogy kimaradnak. Ez a figyelmeztetés megszűnt.

### Nyíl színe és új ikon (generikus 4.10.0)
- A nyíl vonal a legfelső látható kitöltésének színét kapja.
- A QWebMap új, saját ikont kapott.
