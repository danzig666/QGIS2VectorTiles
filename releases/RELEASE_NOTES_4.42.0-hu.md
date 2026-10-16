# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.42.0)](https://github.com/danzig666/QWebMap/releases/tag/v4.42.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.42.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.42.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Új
- **Pontcímkék árnyéka:** a pontcímkék QGIS-árnyéka is megjelenik. A pontcímkéket a MapLibre helyezi el, ezért a néző csak ott kapcsolja be az árnyékot, ahol a címke ténylegesen látszik. Ha egy címke ütközés miatt rejtve marad, árnyék sem marad utána. Az árnyék egy pillanattal a címke után jelenik meg.

### Változott
- **Helyi exportálás verziók nélkül (új alapértelmezés):** az *Export locally* a webtérképet egyetlen webhelyként, saját `index.html`-lel közvetlenül a `<kimeneti mappa>/<név>/` mappába írja, bármely webszerverre átmásolható formában. Minden exportálás lecseréli az előzőt. Csak az előző exportálás által írt fájlok törlődnek (ezeket a `.q2vt-files.json` sorolja fel), így a mappában lévő saját fájlok megmaradnak.
- Ha minden exportálást külön verzióként szeretnél megtartani a `releases/` mappában, visszaállítási lehetőséggel (a korábbi működés), kapcsold be az *Output* fülön a **Keep earlier versions in the local folder** opciót. Az R2/S3 és az SSH közzététel nem változott.
- Ha a kimeneti mappában korábbi, verziózott exportálásokból már van `releases/` mappa, az megmarad. Ezt a napló jelzi, és a mappa törölhető, ha ezekre a verziókra nincs szükség.
