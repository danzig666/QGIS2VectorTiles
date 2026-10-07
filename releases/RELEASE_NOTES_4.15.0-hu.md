# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.15.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.15.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.15.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.15.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Újdonságok (generikus 4.15.0)
- **Nyilak pontosan úgy, ahogy a QGIS rajzolja.** A nyíl szimbólumréteg (pl. „pointing arrow”) a QGIS
  saját nyílszerkesztését követi: egyenes és ívelt nyilak, egyszeres, fordított és kettős fej, fél nyíl,
  és az ismételt/ívelt nyilak csúcspárosítása. A teszteken pixelre egyezik a QGIS-szel. A nyíl saját
  kitöltő szimbólumával töltődik ki, körvonallal együtt, és a vetett árnyék a korábbi nyilakra esik, mint
  a QGIS-ben. A nyilas vonalak minden töréspontja megmarad.
- **Belső árnyék és belső ragyogás vonalakon** (pl. „effect emboss”, „effect neon”): a vonal kb. egy
  pixel széles sávokból áll, amelyek színe a QGIS saját rajzolásából jön a vonal képernyőirányához.
  A vonalvégek és az éles törések is árnyalva vannak.
- **Simább ragyogás és vetett árnyék** vonalakon: a sok rövid szakaszú vonalaknál látszó szőrszerű
  tüskék és sötét foltok megszűntek.
- **Eltolt vonalak éles sarkoknál** (milliméterben vagy pixelben, pl. „topo steps”): ahol a böngésző
  saját eltolása hurkot vetne, ott a QGIS eltolt vonala kerül a csempékbe (zoomszint nyolcadonként).
- A kitöltés (Simple fill) milliméteres/pixeles eltolása mostantól érvényesül.

### Ismert eltérések
- A QGIS a nyilak előtt a nézetre vágja a vonalat, ezért a nézet szélén lévő nyilai mozgatáskor
  változnak; a webtérkép mindig a teljes vonalból számol.
- A belső effekteket egyenes vonalra számoljuk: egymást átfedő vagy majdnem érintő vonalakat a QGIS
  egy alakzatként árnyal, a webtérkép vonalanként.

A belső effektes, a képernyő-méretű nyilas és a képernyő-méretű eltolású (éles sarkú) rétegek
zoomszintenként vagy nyolcad zoomszintenként külön adathalmazt kapnak, így az exportjuk tovább tart.
