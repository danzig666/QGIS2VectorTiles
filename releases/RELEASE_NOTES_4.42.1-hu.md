# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.42.1)](https://github.com/danzig666/QWebMap/releases/tag/v4.42.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QWebMap/releases/latest).

---
**QWebMap HÉSZ 4.42.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.42.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítva (a generikus 4.42.1-ből)
- Teljes kódátnézés javításai: a böngésző **Vissza** gombja megbízhatóan zárja a felugró ablakokat és paneleket (több lépésben, Előre gombbal és bemásolt linkkel is); lassú lekérdezés után nem jelenik meg elavult popup vagy telekinformáció.
- Vetett árnyék iránya, címkeárnyékok zoomfüggő eltolása, kisebb stíluskonverziós javítások.
- **Publish** ablak: publikálás csak az aktuális beállítások átnézése (Review) után; a projekt bezárása nyitott ablaknál nem írja a beállításokat a következő projektbe; a helyi előnézet csak localhoston válaszol.
- Biztonságosabb export és feltöltés (megszakítás után takarítás, S3 ütközés utáni ellenőrzés).

### Javítva (HÉSZ előbeállítás)
- A kézzel felvett korlátozó rétegek megmaradnak az előbeállítás újrafuttatásakor (eddig csak a katalógus szerint felismertek maradtak meg).
- A **műemlék** és **helyi védett épület** rétegek („Műemlék épületek”) újra korlátozásnak számítanak; a sima épületréteg továbbra sem.
- Lizmap-szövegek: a számként tárolt övezeti jelek (pl. 12.0) is párosulnak („12”), ahogy a térkép keresi őket.
- Az övezet popupjába akkor is bekerül az övezetkód, ha csak teljes előírásszöveg-tábla van (övezeti előírás tábla nélkül).
- Az előbeállítás minden futáskor újra keresi a szöveg- és előírástáblát: egy azóta eltávolított tábla nem marad a beállításokban.
- `HESZ_SZOVEG_PROMPT.md` szkript: a beillesztett KÖZÖS blokkok `<hr>` közé kerülnek, így nyomtatáskor a több övezetnél ismétlődő rész egyszer jelenik meg; minden hiba egyszer íródik ki.
