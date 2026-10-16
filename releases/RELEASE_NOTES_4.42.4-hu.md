# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.42.4)](https://github.com/danzig666/QWebMap/releases/tag/v4.42.4)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.42.4**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.42.4 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítva (a generikus 4.42.4-ből)
- **Az áttekintő térkép nem követte a fő térképet:** ha egy réteget ki- vagy bekapcsoltak, témát vagy másik alaptérképet választottak, miközben az áttekintő még csempéket töltött (jellemzően közvetlenül mozgatás után), az a változás kimaradt belőle. Most mindig követi.

### Változott
- Az **áttekintő térkép** nagyobb területet mutat: 6 zoomszinttel távolabbról, mint a fő térkép (eddig 4).
- **Alaptérkép-választó:** minden webes alaptérkép (XYZ/WMS) az aktuális hely valódi képét mutatja az egyforma helyőrző helyett; a képek csak a menü megnyitásakor töltődnek be, így addig az oldal továbbra sem fordul egyetlen alaptérkép-szerverhez sem. A *nincs alaptérkép* választás a projekt saját háttérszínét mutatja.
