# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.13.3)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.13.3)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.13.3**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.13.3 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.


### Utcakép: miért hiányoznak a kék vonalak (generikus 4.13.3)
A kék utcakép-vonalakat a Google **Map Tiles API** adja, a fényképeket a **Maps JavaScript API**.
Ha a kulcs csak az utóbbit használhatja, a képek működtek, de a vonalak szó nélkül elmaradtak.
Mostantól a térkép ezt kiírja („A kék utcakép-vonalak nem jeleníthetők meg…”) az okkal együtt,
és koppintásra a legközelebbi utcakép így is megnyílik.

A vonalakhoz a Google Cloudban (a kulcs projektjében):
1. *APIs & Services → Library → Map Tiles API → Enable* (a projekten legyen számlázás).
2. *Credentials → a kulcs → API restrictions*: ha a kulcs korlátozott, vegye fel a **Map Tiles API**-t
   a Maps JavaScript API mellé.

A helyi előnézet (`http://127.0.0.1…`) is működik, ha a kulcs webhely-korlátozása megengedi
(vagy teszteléskor nincs ilyen korlátozás).

| Futtatás | Eredmény |
|---|---|
| `pytest tests/browser/test_web_viewer_streetview.py` | 6 sikeres (új: a Map Tiles API elutasítását az okkal együtt kiírja; koppintásra a kép így is megnyílik) |
