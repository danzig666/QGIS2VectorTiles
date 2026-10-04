# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.11.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.11.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.11.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.11.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### A térképtémák rétegei összeadódnak (generikus 4.11.0)
A Publish Web Map ablakban egy térképtéma *Publish its layers* gombja mostantól **csak hozzáadja**
a téma rétegeit a publikáltakhoz, és egyetlen réteget sem vesz ki. Több témát egymás után
kiválasztva és a gombot megnyomva mindegyik téma rétegei publikálva lesznek.

### Megosztott stílustárak szimbólumai (generikus 4.11.0)
- **`@symbol_color`:** az ezt használó kifejezések (pl. a vonal színét követő jelölő) miatt az egész
  réteg exportja hibával leállt, és a réteg hiányzott a webtérképről. Ez javítva.
- **Véletlen értékek jelölőnként:** a `rand()`/`randf()` szög, méret vagy szín a mintakitöltés
  jelölőin jelölőnként más értéket kap, mint a QGIS-ben.
- **SVG kitöltések:** eltűntek a csempék közötti fehér vonalak. Az elforgatott SVG kitöltés
  egyben fordul el, ahogy a QGIS-ben, törések nélkül.
- **Jelmagyarázat:** a webes jelmagyarázat a rétegen vagy elemen beállított jelmagyarázat-alakzatot
  (legend patch shape) használja.
