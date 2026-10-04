# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.9.3)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.9.3)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QWebMap HÉSZ 4.9.3**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.9.3 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Domborzatárnyékolás szorzás (Multiply) módban a DEM fölött (generikus 4.9.3)
A böngésző nem tudja keverni a térképrétegeket. Eddig ezért a **Szorzás** keverési módú szürke
domborzatárnyékolás a weben átlátszatlanul jelent meg, és eltakarta a DEM színeit.
- **Most:** a QWebMap exportkor átalakítja a réteget. A szürke szorzás ugyanaz, mint a fekete
  (1 − világosság) átlátszósággal; a **Screen** ugyanaz, mint a fehér (világosság)
  átlátszósággal. Mindkettő pontos.
- **Színes szorzás réteg, amely alatt nincs semmi** (fehér háttéren): normálisan rajzolva
  ugyanaz az eredmény.
- **Más keverési módok és vektorrétegek keverési módjai:** a weben nincs megfelelőjük. Normálisan
  jelennek meg, de az export most figyelmeztet rájuk.

### OSM alaptérkép-export régebbi Open Sans betűvel (generikus 4.9.3)
A régi Open Sans kiadásban a „Open Sans Semibold” külön betűcsalád. Ha ez telepítve volt,
a beépített OpenStreetMap alaptérkép exportja hibával leállt. Ez javítva.
