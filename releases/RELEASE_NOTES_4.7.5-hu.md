# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.7.5)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.7.5)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.7.5 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.7.5 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Rétegek ki-be kapcsolása a jelmagyarázatból (generikus 4.7.5)
- **A gond:** ha a Rétegek fül ki volt kapcsolva, a látogató nem tudta kikapcsolni például az
  ortofotót vagy más háttérréteget.
- **Kapcsolók a jelmagyarázatban:** ilyenkor a **Jelmagyarázat** minden kapcsolható rétege mellé
  ki-be kapcsoló kerül: több elemből álló rétegnél a címe mellé, egyetlen elemnél a sorába.
- **A kikapcsolt réteg a listában marad** egy halvány sorként, így vissza is kapcsolható; a „csak
  a nézetben látható elemek” módban is.
- **Nem kapcsolható rétegek** (a Publish ablakban „Toggleable” kikapcsolva) nem kapnak kapcsolót.
- **Linkek és mentett nézetek:** a kapcsolók ugyanazt az állapotot használják, mint a Rétegek
  fül, így ezek megtartják a beállítást.
- **A mellékelt OpenStreetMap alaptérkép** saját gombja a térképen („Alaptérkép”) továbbra is
  megvan.
