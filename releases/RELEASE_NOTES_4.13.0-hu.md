# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.13.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.13.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.13.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.13.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.


### Google utcakép (Street View) a webtérképen (generikus 4.13.0)
A Webtérkép közzététele ablak *Interakció* fülén kapcsolja be a *Street View (Google)* lehetőséget,
és illessze be a Google API-kulcsát. A látogatók a nagyítógombok alatt egy utcakép gombot kapnak:

1. Megnyomva **kék vonalak** mutatják, hol érhető el utcakép, így senki nem kattintgat feleslegesen.
2. **Koppintson arra, amit látni szeretne** (egy házra, egy sarokra): megnyílik a legközelebbi
   utcakép, és **már arra néz**.
3. A képet húzva körbenézhet. A nézőpont és az iránya a térképen látszik; a jelölőt húzva odébb
   léphet, vagy koppintson máshová.

Ha a közelben nincs utcakép, azonnal jelzi. Telefonon az utcakép a képernyő felső felén jelenik
meg, alatta a térkép; teljes képernyőre nagyítható.

**Az API-kulcs** a közzétett oldalon bárki számára látható, ez nem kerülhető el. A Google Cloudban:
- engedélyezze a **Maps JavaScript API**-t és a **Map Tiles API**-t (ez adja a kék vonalakat);
- korlátozza a kulcsot a webhelye címére (**HTTP-hivatkozók**, pl. `https://terkep.pelda.hu/*`)
  és erre a két API-ra.

A havi ingyenes kereten felüli használatot a Google kiszámlázza. A Google kódja csak akkor töltődik
be, amikor a látogató megnyomja a gombot.

| Futtatás | Eredmény |
|---|---|
| `pytest tests/browser/test_web_viewer_streetview.py` (új, a Google helyettesítve) | 5 sikeres: lefedettségi vonalak, a kép a koppintott pont felé néz, a térképi jelölő követi a képet, „itt nincs utcakép” üzenet, bezárás után a telekinfó újra működik, mérés és utcakép kizárja egymást, telefonos elrendezés |
| Közzététel ablak, profil, weboldal-építő, telekinfó és viewer tesztek | 59 sikeres |
