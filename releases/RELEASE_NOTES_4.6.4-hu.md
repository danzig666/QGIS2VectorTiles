# ⚠️ NOT FOR GENERAL USE

**This is a special edition for Hungarian zoning plans (HÉSZ, `szab_ov` layer conventions), made for one office's own projects. If you are not sure you need it, you don't.**

## 👉 [Download the generic edition instead (QGIS2VectorTiles 4.6.4)](https://github.com/danzig666/QGIS2VectorTiles/releases/tag/v4.6.4)

or always the newest generic version: [latest release](https://github.com/danzig666/QGIS2VectorTiles/releases/latest).

---

**QGIS2VectorTiles 4.6.4 (fork, HU HÉSZ)**

This is a fork of [GallPeters/QGIS2VectorTiles](https://github.com/GallPeters/QGIS2VectorTiles) by Jossef Kanter, not an official release of the original plugin.

Ez a **magyar HÉSZ változat** (`hu-hesz` ág): a generikus 4.6.4 minden funkciója, plusz a magyar
településrendezési terv előbeállítása. Leírás: `docs/hu/HESZ.md`.

### Újdonságok (generikus 4.6.4)
- **A nyomtatás egy A4-es fekvő oldalra fér.** Ez érvényes az Eszközök fül *Nyomtatás* gombjára,
  a Ctrl+P-re és a telekinformáció *Nyomtatás* gombjára is (korábban három oldal lett, a teljes,
  részben halvány jelmagyarázattal).
  - Balra a térkép: nyomtatás előtt a nyomtatási méretre igazodik és újrarajzolódik.
  - Jobbra a cím, a dátum és a forrás, alatta:
    - térkép nyomtatásakor csak a nyomtatott területen látható elemek jelmagyarázata;
    - telek nyomtatásakor a telekinformáció, gombok nélkül.
  - A hosszú jelmagyarázat elemek között törik új oldalra.
  - Nyomtatás után a térkép visszaáll az előző nézetre.
- **A telekinformáció az övezetkódot mutatja, nem az objektumazonosítót.** Ha az övezetkód
  mezőjének a fid / id / elsődleges kulcs volt beállítva (ezért jelent meg a 241, 488, 487),
  az export figyelmeztet, és helyette az övezetkódot tartalmazó mezőt használja:
  - az övezet réteg kategorizált stílusának mezőjét;
  - ha ilyen nincs, a szabályokban vagy feliratokban használt, illetve kódszerű nevű
    (`szab_ov`, `ovezet`, `kod`…) mezőt.

  A Publish ablak ugyanígy választ, amikor az övezet réteget kiválasztják. Minden telekrész
  címe az övezetkód **félkövéren**; az övezet adatai között nem ismétlődik.
