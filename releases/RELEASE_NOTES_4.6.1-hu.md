# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.6.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.6.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.6.1 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.6.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítás (generikus 4.6.1)
- **Nem villognak a feliratok** a térkép mozgatásakor. A webtérkép által elhelyezett feliratok
  (övezetkódok, helyrajzi számok, utcanevek, szintvonal-magasságok) pontosan ott jelennek meg, ahová
  kerültek; ahol nincs szabad hely, inkább átfednek, mint hogy eltűnjenek.
- Az elforgatott (szabad elhelyezésű) feliratok **teljes egészükben a parcellán belül** maradnak,
  az utca közepén; ha sehol nem férnek el, nem jelennek meg (mint a QGIS-ben).
- A javításhoz újra kell exportálni / publikálni.
