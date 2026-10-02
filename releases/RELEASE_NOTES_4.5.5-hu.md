# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.5.5)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.5.5)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.5.5 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.5.5 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítás (generikus 4.5.5): a közös határok pontosan fedik egymást
- Csempézés előtt minden réteg külön-külön 1 m-es tűréssel egyszerűsödött. Így két réteg közös
  határa – pl. a telekhatáron futó **övezethatár** – a két rétegben más-más töréspontokat veszített,
  és akár ~1 m-re elvált egymástól (18-as nagyításnál néhány pixel, jobban nagyítva több).
- Most az egyszerűsítés a csempék saját kerekítésén belül marad a legnagyobb nagyításon (17-es
  szinten ~2 cm), így a közös határok pontosan egymáson vannak; a vonal menti jelek (pl. a
  pontozott övezethatár pöttyei) is a vonalon ülnek.
- Az export kb. 3 %-kal lassabb, a publikált fájlok kb. 1,4 %-kal nagyobbak.
- A pontos határokhoz újra kell exportálni / publikálni.
