# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.45.0)](https://github.com/danzig666/QWebMap/releases/tag/v4.45.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.45.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.45.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítva (a generikus 4.45.0-ból)
- A **levágott vonalak** (egyszerű vonal *Trim lines* kezdő/záró távolsággal) levágva rajzolódnak; a webes térkép eddig a teljes vonalat rajzolta.
- A **töréspontonként számolt értékek** (`@geometry_point_num`, `@geometry_point_count`) a töréspontokra tett jeleknél minden töréspontban külön számolódnak, ahogy a QGIS-ben: egy tájfutópálya pontszámai eddig mind egyformák voltak. Az ilyen, adatfüggő, térképi egységben vagy méterben megadott eltolás elmozdítja a jelet.
- A **Geometry generator** al-szimbólumának értékei minden részen úgy számolódnak, mint a QGIS-ben (`@geometry_part_num` részenként, `$currentfeature` a forrás elem): a minden csomópontba írt hossz eddig 0-t mutatott.
- A **vonalak ragyogása és árnyéka** (*Outer glow*, *Drop shadow*) olyan szélesen és erősen halványul el, mint a QGIS elmosása, és pixelenként egyszer festődik, ahogy a QGIS a hatást festi: éles töréseknél eddig sötét ék jelent meg.
- A **nyíl (*Arrow*) árnyéka** lágy, mint a QGIS-ben (eddig éles szürke másolat volt).
- Az **egyéni szaggatású (*Custom dash pattern*) vonalkázás** akkor is szaggatott, ha a vonalstílusa folytonos, ahogy a QGIS rajzolja.
- Az **elmosott *Shapeburst* kitöltés** úgy halványul, mint a QGIS-ben (eddig éles széllel).
- A **térképi egységben megadott pont- és mintakitöltések** egész zoomszintek között is élesek maradnak: a webes térkép eddig az alatta lévő zoom mintáját legfeljebb kétszeresére nyújtotta, és a pöttyök szürkévé mosódtak.
