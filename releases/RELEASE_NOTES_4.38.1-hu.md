# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.38.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.38.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.38.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.38.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Webtérkép (generikus 4.38.1)
- **Vékony hajszálvonalak nagy felbontású képernyőn:** a 4.38.0-ban a vékony hajszálvonal 0,5 CSS px széles volt. 4K képernyőn (150–200 %-os Windows-nagyítás) ez egy teljes fizikai képpont, ugyanannyi, mint a QGIS hajszálvonala, ezért ott nem látszott vékonyabbnak. A viewer most elosztja a szélességet a képernyő képpontarányával: a hajszálvonal minden képernyőn fél fizikai képpont (erős nagyításnál 0,8). Egy képpontnál vékonyabb vonal nem rajzolható, ezért halványabb lesz, így a sűrű telekhatárok 4K képernyőn sem tűnnek kitöltöttnek. Ha az ablak más képpontarányú képernyőre kerül, követi. Ez a viewer változása: a 4.38.0-val publikált térképek újrapublikálás után kapják meg.
