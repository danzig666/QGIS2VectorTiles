# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.7.3)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.7.3)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.7.3 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.7.3 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Mérés illesztéssel (generikus 4.7.3)
- **Beállítás:** Publish ablak → Interaction fül → réteg → *Measurements snap to this layer
  (corners and edges)*. A **HÉSZ előbeállítás ezt bekapcsolja** a földrészleteken, valamint
  a szabályozási vonal és az övezethatár rétegen.
- **Mérés közben:** a webtérkép távolság- és területmérésénél a pont illeszkedik:
  - a kiválasztott rétegek legközelebbi töréspontjára (kb. 12 px-en belül, érintőképernyőn 22 px);
  - ennek híján a legközelebbi élre, vonalra.
- **Amit látni:**
  - piros kör mutatja, hová kerül a pont;
  - szaggatott vonal mutatja a következő szakaszt;
  - **Alt** lenyomva szabad pont tehető le;
  - az Eszközök fülön ki-be kapcsolható („Illesztés: …”).
- **Mihez illeszt:** csak ahhoz, amit a térkép éppen rajzol, a már letöltött csempékből, extra
  letöltés nélkül.
- **Pontosság:** a legnagyobb nagyításon néhány cm, kisebb nagyításon durvább. Pontos méréshez
  érdemes ránagyítani. A mérés tájékoztató jellegű, nem földmérési.
- **Meglévő projektben:** jelölje be a rétegeket az Interaction fülön, vagy futtassa újra a HÉSZ
  előbeállítást.
