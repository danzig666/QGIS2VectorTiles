# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.45.1)](https://github.com/danzig666/QWebMap/releases/tag/v4.45.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.45.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.45.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítva (a generikus 4.45.1-ből)
- **Hamis figyelmeztetés minden webes térképen** („A poligonok látható részére helyezett feliratok ebben a böngészőben nem érhetők el”): a viewer a MapLibre 6.14.0-ra frissítés után is még a 6.11.2-t várta. Maguk a feliratok működtek.
- A **„Hide labels smaller than”** (Publish ablak, Output) most az adatfüggő méretű, térképi egységben megadott feliratokat is elrejti (pl. `if("fekves" = 2, 6, 4)`), mindegyiket a saját mérete szerint. Eddig minden méretben látszottak.
- A **„Label every feature”** a nem férő feliratot legfeljebb a „Hide labels smaller than” méretéig kicsinyíti (eddig a felére, olvashatatlanul kicsire); inkább kilóg a poligonból.
- **Export cache**: egy réteg hozzáadása az exporthoz az összes többi réteget újracsinálta („export settings changed (view)”). Most csak a stílusokban ténylegesen használt térképnézet-változók számítanak. A frissítés utáni első export egyszer mindent újracsinál.
