# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.38.4)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.38.4)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.38.4**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.38.4 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### HÉSZ előbeállítás (csak a HU változatban)
- **Lizmap-pel publikált tervek előírásai:** ha a projekt mellett van `media/html` mappa, és egy rétegnek olyan kifejezésmezői vannak, amelyek ide mutatnak (pl. `'media/html/' + szerk_ov + '.html'`), az előbeállítás átveszi a szövegeket. A kifejezéseket az övezeti réteg minden elemén kiértékeli, a fájlokat a Lizmap felugró sablonjának sorrendjében (`<projekt>.qgs.cfg`) fűzi össze: övezetfajta (vagy `csak_szab`), sajátos jel, általános és lakóterületi általános előírások. Az eredmény a „HÉSZ övezeti előírások szövege (Lizmap)” tábla (`hesz_eloirasok_szovege_lizmap.gpkg` a projekt mellett), és telekre kattintva minden telekrész alatt lenyitható: „A(z) … övezet teljes előírásai”.
  - Minden futtatáskor újra elkészül, így a HTML-fájlok módosításai is átkerülnek. A prompttal készült saját szövegtábla előnyt élvez.
  - Ha a QGIS a kifejezésmezőt nem tölti be (az adatforrásnak is van ilyen nevű mezője), a kifejezést a projektfájlból veszi.
  - Csak a `media` mappán belüli fájlokat olvassa; a hiányzó fájlokat felsorolja.
  - A projektet utána menteni kell.
- **Övezetkód és övezeti értékek más mezőnevekkel:** az övezetkód `szab_kod` mezőből is jöhet. Az övezeti értékek, ha nincsenek `p_…` mezők, a `beepmod`, `beepszaz`, `magassag`, `m2`, `zold`, `kozmu` mezőkből jönnek.
