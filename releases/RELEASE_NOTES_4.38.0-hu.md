# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.38.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.38.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.38.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.38.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Webtérkép (generikus 4.38.0)
- **Vékony hajszálvonalak** (Publish ablak, *Output* fül, alapból bekapcsolva): a QGIS a 0 vastagságú vonalat vagy körvonalat (hajszálvonal, pl. telekhatárok) egy képpont szélesnek rajzolja. A böngészőben ez nagy felbontású képernyőn két képpont, és ahol sok ilyen vonal fut sűrűn egymás mellett (egy település telkei kizoomolva), a térkép kitöltöttnek látszik. A webtérkép most a folytonos hajszálvonalakat 0,5 CSS px szélesen rajzolja: nagy felbontású képernyőn pontosan egy képpont, más képernyőn halványabb vonal. A 14-es és 20-as nagyítás között 0,8 px-ig szélesednek, így minden nagyításon látszanak. A szaggatott hajszálvonalak 1 px-esek maradnak. Kikapcsolva a QGIS-hez hasonló. A térkép újrapublikálása után lép érvénybe.

### Javítások (generikus 4.38.0)
- **Hiányzó méretezési nyilak:** a 0 szélességre elvékonyodó törzsű nyílvonal (*Arrow width* 0, pl. kétfejű méretezési vonal az útszélesség feliratával) egyáltalán nem került a webtérképre, csak a felirata látszott. A két fej közötti körvonala önmagát érinti, és ez az alakzat elveszett a csempékhez való tisztításkor. Most a fejek is és a köztük lévő vékony vonal is megjelenik, ahogy a QGIS rajzolja.
- A vonalánál épphogy rövidebb vonalfelirat (pl. „12m” egy 12 m-es méretezési vonalon) most a QGIS-hez hasonlóan a vonal mentén jelenik meg: az illeszkedési próba eddig a térközzel bővített feliratdobozt használta a puszta szöveg helyett.
