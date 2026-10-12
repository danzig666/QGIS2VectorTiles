# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.33.1)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.33.1)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.33.1**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.33.1 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Javítások: vonalfeliratok (generikus 4.33.1)
- **Az íves (Curved) vonalfeliratok követik az utat.** A vonalanként egyszer kiírt, QGIS-ben *Íves* elhelyezésű felirat a webes térképen egyenes volt, a vonal közepén a vonal irányába forgatva, így kanyargó utcán lelógott az utcáról. Mostantól a felirat betűről betűre a vonalra simul, mint a QGIS-ben, minden nagyításon, az utolsó csempeszint fölött is. A kanyarokat a QGIS íves szöghatáráig követi, élesebb sarok elől a vonal mentén odébb kerül.
- **Szóköz (szótávolság).** A QGIS szótávolság-beállítása (*Térköz → Szó*) elveszett, a szavak szorosan álltak. A szóközök most nagyjából olyan szélesek, mint a QGIS-ben.
- **A vonal fölé vagy alá helyezett feliratok** a webes térképen a vonalon ültek; most a helyükön maradnak.

| Futtatás | Eredmény |
|---|---|
| Nézegető-, felirat-, végponttól végpontig és böngészős összehasonlító tesztek | mind sikeres |
| Íves, szótávolságos feliratú utcaréteg, QGIS és web, 17,5–21,5 nagyítás | a feliratok követik az utcákat, a QGIS szóközeivel |
