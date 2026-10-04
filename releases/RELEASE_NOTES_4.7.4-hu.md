# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.7.4)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.7.4)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.7.4 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.7.4 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### A beállításfájl az R2 kulcsokat is viszi (generikus 4.7.4)
- **Exportálás:** az *Export settings to a file…* a célhely R2 / S3 kulcsait (Access Key ID,
  Secret Access Key) is beírja a fájlba. Ezeket az ablakba beillesztett kulcsokból vagy a QGIS-ben
  mentett konfigurációból veszi; az utóbbi egyszer kérheti a QGIS mesterjelszót.
- **A fájlban a titkos kulcs olvasható formában van, a tulajdonos kérésére.** Tartsa biztonságban:
  akinél a fájl van, írhat a tárolóba.
- **Importálás:** az *Import settings from a file…* azonnal használhatóvá teszi a kulcsokat ebben
  a munkamenetben, így a publikálás rögtön működik. Ezen felül titkosítva elmenti őket a QGIS-be,
  és kiválasztja azt a konfigurációt, így újraindítás után is megmaradnak. Ehhez a QGIS kérheti
  a mesterjelszót.
- **A projektbe mentett beállításokban** továbbra sincsenek kulcsok.
