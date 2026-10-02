# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.5.4)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.5.4)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.5.4 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.5.4 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítás (generikus 4.5.4): minden szintvonal-felirat látszik
- A 4.5.3 már a vonal látható részére tette a szintvonal-feliratokat, de a MapLibre a legtöbbet
  elrejtette (az ütközésvizsgálata törtszámú nagyításnál akár kétszer akkorának veszi a feliratot).
- Most a feliratok a valódi méretükkel kerülnek más feliratok (övezetkódok, helyrajzi számok,
  házszámok, nevek) mellé, ahogy a QGIS-ben, és mindig megjelennek, ahová kerültek.
- Ha a vonal látható részén nincs szabad hely, nincs felirat (mint a QGIS-ben).
- Az új nézethez újra kell exportálni / publikálni.
