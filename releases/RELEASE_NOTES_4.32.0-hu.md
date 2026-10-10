# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QWebMap 4.32.0)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.32.0)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---
**QWebMap HÉSZ 4.32.0**

QWebMap (formerly QGIS2VectorTiles (fork)) started as a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter. It is not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.32.0 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Újdonság: rétegek keresése több száz rétegű projektekben (generikus 4.32.0)
- **Map fül:** a rétegfa fölötti szűrőmező gépelés közben mutatja azokat a rétegeket, amelyek nevében a szöveg szerepel (kis- vagy nagybetűvel, ékezet nélkül is), a csoportjaikkal együtt; ha egy csoport neve egyezik, az egész csoport látszik. Az **„Only published layers”** elrejti a nem közzétett rétegeket. A kiszűrt sorok beállításai megmaradnak.
- **Interaction fül:** a rétegek név szerint rendezve, 10-nél több rétegnél keresőmezővel.
- **Minden rétegválasztóba lehet gépelni** (extent, házszámok, telekinfó táblák, domborzat, WMS): a nevükben a szöveget tartalmazó rétegek listája jelenik meg.
- A **„Zoomed-out load…”** ablakban is van keresőmező.

### Újdonság: a „Zoomed-out load…” zoomjai szerkeszthetők
- Minden rétegnek van saját réteg- és feliratzoomja, nem csak a javasoltaknak; a javaslatról (bekapcsolva) vagy a jelenlegi zoomról (kikapcsolva) indulnak. A zoom átírása bekapcsolja a jelölőt; a legkisebb zoom megszünteti a korlátot.

### Javítások
- **Térképi egységű mintázatos kitöltés csak körvonalként látszott** a 0-s zoomtól exportált térképeken (a Publish ablak alapbeállítása), pl. sraffozott építési hely: a zoomonkénti mintázatképek a legkisebb zoomokra fogytak el, ahol a minta egy képpontnál is kisebb. Mostantól a legnagyobb zoomok kapják őket.
- **A vonalnál hosszabb méretezési feliratok** nem jelentek meg. „Allow degraded placement” mellett a QGIS az ilyen feliratot vízszintesen, bal alsó sarkával a vonalra teszi; mostantól a webes térkép is.
- **A vonalfeliratok átfedhetik egymást, ha a QGIS engedi** („Allow overlap if required”): eddig a szabad hely nélküli felirat kimaradt.
- **A feliratkeret a felirattal együtt fordul** (*Sync with label*, *Offset of label*), a sarkaival együtt.
- **A szabad elhelyezésű sokszögfeliratok a hosszú sávok közepére kerülnek**, a sokszög súlypontjához legközelebb, mint a QGIS-ben.
- A bezárt Publish ablak a rétegválasztóival együtt felszabadul (eddig minden projektváltozásra reagáltak).

| Futtatás | Eredmény |
|---|---|
| Teljes generikus tesztcsomag (fájlonként külön folyamatban) | 902 sikeres, 5 kihagyva |
| Nézegető-, felirat- és böngészős összehasonlító tesztek az utolsó módosítás után | mind sikeres |
| HU tesztek a hu-hesz ágon | 13 sikeres, 1 kihagyva (HU tesztek, plugin-csomag) |
