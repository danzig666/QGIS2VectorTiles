# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.47.0)](https://github.com/danzig666/QWebMap/releases/tag/v4.47.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.47.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.47.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Változott (a generikus 4.47.0-ból)
- **Kevesebb memória a böngészőben.** Egy részletes városi térkép (50 000 földrészlet) több mint 1,5 GB-ot foglalt a böngészőben.
  - **A kitöltés és a körvonala a poligonok egyetlen példányán osztozik** a csempékben (a körvonal eddig vonalként még egyszer bekerült): az ilyen rétegek nagyjából fele annyi csempeadatot és memóriát igényelnek. Az átlátszó kitöltés a körvonal alatt így újra ingyenes — maradjon ott, ahol a látogatók a poligonon belül kattintanak (felugró ablak, telekinformáció).
  - **Low memory web map** (Publish ablak → *Output*, alapból bekapcsolva): a böngésző a képernyőn lévőkön kívül nagyjából egy zoomszintnyi csempét tart meg (eddig ötöt); a visszatérésnél újra betölti őket, többnyire a böngésző gyorsítótárából.
