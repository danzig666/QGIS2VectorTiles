# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.9.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.9.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QWebMap HÉSZ 4.9.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.9.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Új név: QWebMap HÉSZ (generikus 4.9.0)
A plugin neve **QGIS2VectorTiles (fork, HU HÉSZ)** helyett mostantól **QWebMap HÉSZ**.
- **Menü:** *Web → QWebMap → Publish Web Map…*
- **Processing:** a szolgáltató neve *QWebMap*, az eszközé *Export vector tile package*.

**Frissítés 4.8.1-ről vagy régebbiről.** A QWebMap **új pluginként** települ (`QWebMap` mappa):
1. Telepítsd a `QWebMap-4.9.0-hu.zip` fájlt (*Bővítmények → Bővítmények kezelése és telepítése →
   Telepítés ZIP-ből*).
2. Ugyanott távolítsd el a **QGIS2VectorTiles (fork, HU HÉSZ)** bővítményt. Amíg fent van, a
   QWebMap figyelmeztet, mert a kettő ugyanazt a menüt és Processing eszközt tenné fel.

**Minden megmarad:**
- a projektekbe mentett közzétételi beállítások;
- az ablak mérete és helye;
- a mentett tárhely-kulcsok (a régiek a régi nevükön maradnak, az újak neve „… (QWebMap)”);
- a Processing eszköz azonosítója, így a modellek is működnek.

A már közzétett térképek változatlanul működnek.
