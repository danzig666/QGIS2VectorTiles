# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.48.0)](https://github.com/danzig666/QWebMap/releases/tag/v4.48.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.48.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.48.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Változott (a generikus 4.48.0-ból)
- **Lighter tiles when zoomed out** (Publish ablak → *Output*, alapból bekapcsolva): a fél pixelnél kisebb kitöltések és vékony vonalak kimaradnak abból a zoomszintből, ahol ilyen kicsik, és a rétegek utolsó zoomszintje alatti csempék negyed pixelig egyszerűsödnek. Minden réteg utolsó zoomszintje minden elemet pontos alakkal tartalmaz.
- **Kevesebb stílusréteg**: a térképi egységben méretezett jelek nem kapnak külön stílusréteget minden zoomszintre, ha csak a képük különbözik (egy városi térkép: 809 → 473 réteg).
- **Low memory web map** (alapból bekapcsolva) ezen felül:
  - legfeljebb 2-es pixelaránnyal rajzolja a térképet;
  - elengedi a nem látható csempéket, ha a fül rejtett, vagy egy percig nem mozdult a térkép;
  - az áttekintő térkép csak egyszerű kitöltéseket és vonalakat rajzol.
- A poligon látható részére helyezett feliratok poligonjait a böngésző már nem háromszögeli fölöslegesen.
- A nézegető nem másolja le a teljes stílust minden mozgatásnál, rétegváltásnál és feliratfrissítésnél.
