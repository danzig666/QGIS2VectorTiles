# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.4.3)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.4.3)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.4.3 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.4.3 minden funkciója, plusz a magyar
településrendezési terv előbeállítása (Preset… → Magyar településrendezési terv (HÉSZ, szab_ov)).
A két változat ugyanabba a plugin-mappába települ, így az egyik telepítése lecseréli a másikat.
Leírás: `docs/hu/HESZ.md`.

### Javítás (generikus 4.4.3)
- A látható poligonrészre tett feliratok **húzás és nagyítás közben a térképhez tapadnak**
  (a 4.4.2 a képernyő szélén lévőket húzás közben újra és újra áthelyezte); a térkép megállása
  után egyszer igazodnak a poligon látható részéhez. Új poligonok felirata továbbra is azonnal
  megjelenik. Az új webes nézethez újra kell exportálni / publikálni.
