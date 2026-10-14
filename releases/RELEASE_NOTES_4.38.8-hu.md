# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.38.8)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.38.8)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.38.8**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.38.8 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítva
- **Telekinfó nyomtatása:**
  - A kinyitott „teljes előírások” szövegek egymás után, a teljes lapszélességben, három hasábban (álló lapon kettőben) futnak, övezetek között oldaltörés nélkül, így nem marad üres hely a papíron.
  - A több övezetnél ismétlődő rész (vízszintes vonallal elválasztott szakasz, pl. az általános előírások) csak egyszer nyomtatódik; a többi övezetnél egy sor utal rá („… – azonos a(z) K-id-2 övezetnél fent nyomtatottal”).
  - A térkép melletti telekrészek és korlátozások tömör sorok (sávok és kártyák nélkül), így elférnek az első oldalon.
