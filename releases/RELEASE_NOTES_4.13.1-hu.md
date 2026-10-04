# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.13.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.13.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.13.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.13.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.


### Hibátlan bővítményfrissítés (generikus 4.13.1)
A bővítmény frissítésekor vagy újratöltésekor ez a hiba jelent meg: *Error while unloading plugin
QWebMap — RuntimeError: wrapped C/C++ object of type QGIS2VectorTilesPorvider has been deleted*.
A bővítmény kétszer regisztrálta a Processing-szolgáltatóját, a QGIS pedig törölte a második
példányt, így a régi példány a régi kóddal betöltve maradt. Mostantól egyszer regisztrálja és
tisztán eltávolítja, frissítéskor pedig lecseréli a korábbi verzió bent maradt szolgáltatóját.

A hiba **egyszer** még megjelenhet, amikor *erre* a verzióra frissít, mert ekkor a QGIS még a régi
verzió kódjával távolítja el a bővítményt. Ilyenkor indítsa újra a QGIS-t; a későbbi frissítéseknél
már nem jelenik meg.

| Futtatás | Eredmény |
|---|---|
| `pytest tests/integration/test_plugin_package.py` | 1 sikeres (betöltés a QGIS sorrendjében, eltávolítás, frissítés bent maradt szolgáltató fölé; a javítás nélkül hibázik) |
| `pytest tests/integration/test_publish_dialog.py` | 4 sikeres |
