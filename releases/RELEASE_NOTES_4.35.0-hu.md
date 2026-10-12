# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.35.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.35.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.35.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.35.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Kiférő nézetek, zárt térképpanel (generikus 4.35.0)
- **Ki nem férő térképtéma-nézetek:** sok, nézetként felkínált térképtéma esetén a webes térkép nézetgombjai kicsúsztak a képernyő jobb szélén. Ha nem férnek el egy sorban (ablakátméretezés után vagy telefonon is), a webes térkép legördülő listában kínálja fel őket.
- **Alapból zárt térképpanel:** új opció a Közzététel ablak Interakció lapján (alapból kikapcsolva). A webes térkép zárt panellel (rétegek, jelmagyarázat, eszközök) nyílik, így több látszik a térképből; a menügomb nyitja ki.
- **Rétegeinek közzététele** egy térképtémánál most a témát is bepipálja a *Nézetként felkínált térképtémák* listában.

| Futtatás | Eredmény |
|---|---|
| Közzététel ablak tesztjei | 25 sikeres |
| Webes térkép böngészőtesztjei (nézetek, alaptérkép és témák, objektumok, kiegészítők, füstteszt) | mind sikeres |
