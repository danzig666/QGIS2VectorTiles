# Magyar HÉSZ változat (`hu-hesz` ág)

> **Not for general use.** This edition is for Hungarian zoning plans only; everyone else should
> use the generic plugin from the `main` branch / the "Latest" release.

Ez a változat a generikus pluginból (`main`) és egy **előbeállításból** (preset) áll, amely a
magyar településrendezési tervek szokásos réteg- és mezőneveiből kitölti a telekinformáció
beállításait. A változat csak új fájlokat tartalmaz (lásd `docs/BRANCHES.md`), ezért a
generikus fejlesztések automatikusan átkerülnek ide.

## Használat

1. Nyissa meg a tervet QGIS-ben, majd a **Publish Web Map** ablakot.
2. **Preset… → Magyar településrendezési terv (HÉSZ, szab_ov)**.
3. Az ablak felsorolja, mit talált. Ellenőrizze a **Telekinformáció** fület (különösen a
   jogszabályi hivatkozásokat), majd mentse a beállításokat a projektbe.

Az előbeállítás csak a beállításokat tölti ki; adatot nem módosít és nem publikál.
Újbóli futtatáskor a már kézzel kitöltött korlátozás-szövegek megmaradnak.

## Mit ismer fel

| Beállítás | Felismerés |
|---|---|
| Telkek | polygon réteg `hrsz` mezővel, neve „földrészlet” / „telek” (ékezet nélkül is) |
| Telekadatok | `kozter_nev` (Közterület), `kivett` (Megnevezés), `muv_ag` (Művelési ág), `fekves` (Fekvés) |
| Övezetek | polygon réteg `szab_ov` mezővel; a színekkel rajzolt réteg az elsődleges (az ő jele kerül a telekinfóba) |
| Övezeti értékek | `p_beepmod`, `p_beepszaz`, `p_beepmag`, `p_terulet`, `p_zold`, `p_szabkieg` |
| Vágóvonalak | „Szabályozási vonal”, „Övezethatár” (ha nincsenek: a `lszerk_ov`/`rszerk_ov` mezős vonalrétegek) |
| Korlátozások | a lenti katalógus szerinti nevű rétegek, megnevezés-mező: `nev`, `name`, `megnevezes`, `vedettOrokErtekNev`, `azon`, `tipus`, `kategoria` |
| Előírások teljes szövege | tábla „HÉSZ … szöveg” / „html” névvel, övezetkód mezővel (`szab_ov` …) és szövegmezővel (`eloiras_html`, `html`, `szoveg` …); a telekinformációban és az övezet felugró ablakában lenyitható |
| Övezeti előírások | tábla (geometria nélkül is) „előírás” / „HÉSZ” / „szabályzat” / „övezeti” névvel és övezetkód mezővel (`szab_ov`, `ovezet`, `jel`, `kod`); minden más mezője megjelenik — ismert nevek magyar címmel (`beep_szaz`, `max_mag`, `min_ter`, `min_zold` …), a hivatkozás mező (`hivatkozas`, `paragrafus`, `szakasz`, `link` …) linkként |

Nem korlátozás: feliratok, szintvonal, házszám, épületek, alrészletek, közigazgatási határ,
települések, belterülethatár, kerékpárút, a szabályozás övezeti rétegei.

## Övezeti előírások a webtérképen

Ha a projektben van övezeti előírás tábla (pl. „HÉSZ övezeti előírások”: `szab_ov`,
`beep_szaz`, `max_mag`, `min_ter`, `hivatkozas`), az előbeállítás bekapcsolja:

- a **telekinformációban** minden telekrész alatt megjelennek az övezete előírásai;
- egy **övezetre kattintva** (pl. út, telken kívüli terület) a felugró ablak alján is
  („A(z) Lke-2 övezet előírásai”);
- a **hivatkozás** mező linkként jelenik meg: ha https:// cím (pl. a Nemzeti Jogszabálytár
  rendeletére), oda vezet; ha egy, a térképpel publikált dokumentum (Info fül → Dokumentumok,
  pl. a HÉSZ PDF) fájlneve vagy címe, akkor arra.

**A tábla elkészítése a HÉSZ szövegéből:** a [HESZ_ELOIRAS_PROMPT.md](HESZ_ELOIRAS_PROMPT.md)
két promptot ad egy nyelvi modellhez (ChatGPT, Claude, Gemini …): az első a rendelet és a
térkép övezeti jelei alapján elkészíti a CSV táblát (forráshelyekkel és kötelező
önellenőrzéssel), a második egy új beszélgetésben függetlenül újraellenőrzi. Leírja a tábla
oszlopait (mind magyar címmel jelenik meg; a QGIS-ben megadott mezőálnév felülírja a címet),
és a betöltést a QGIS-be.

**Az előírások teljes szövege övezetenként:** a [HESZ_SZOVEG_PROMPT.md](HESZ_SZOVEG_PROMPT.md)
prompttal a nyelvi modell övezetenként összegyűjti a HÉSZ minden ott alkalmazandó rendelkezését (az
általános és a feltételes előírásokat is), szó szerint, egyszerű HTML-ben. A mellékelt QGIS-szkript
ebből egy második táblát készít („HÉSZ övezeti előírások szövege”: `szab_ov`, `eloiras_html`). A
webtérképen minden övezet alatt lenyitható: „A(z) Lke-1 övezet teljes előírásai”.

Az övezeti réteg felugró ablakába az előbeállítás felveszi az övezetkódot (enélkül nem
találná meg az előírásokat). A webes megjelenítés egy változat-kiegészítő
(`resources/web_viewer/addons/zone_rules.mjs`), a generikus pluginban nincs benne.

## Korlátozás-katalógus (javaslat)

| Kulcsszó a rétegnévben | Hivatkozás (ellenőrizendő) |
|---|---|
| műemléki környezet | 2001. évi LXIV. tv. (Kötv.); 68/2018. (IV. 9.) Korm. r. |
| műemlék | 2001. évi LXIV. tv. (Kötv.) |
| régészeti | 2001. évi LXIV. tv. (Kötv.); 68/2018. (IV. 9.) Korm. r. |
| Natura 2000 | 275/2004. (X. 8.) Korm. r. |
| ex lege, láp, barlang, védett természeti | 1996. évi LIII. tv. (Tvt.) |
| ökológiai hálózat | 2018. évi CXXXIX. tv. (MaTrT) |
| tájképvédelmi, vízminőség | MaTrT; 9/2019. (VI. 14.) MvM r. |
| egyedi tájérték (pont: 10 m) | Tvt. |
| helyi védett | 2016. évi LXXIV. tv.; településképi rendelet |
| vízbázis, védőidom, vízműkút védő… | 123/1997. (VII. 18.) Korm. r. |
| nyersanyag, alábányászott | MaTrT; 1993. évi XLVIII. tv. (Bt.) |
| felszínmozgás, csúszásveszély | — |
| elektromos, villamos, kV | 2007. évi LXXXVI. tv. (VET); 2/2013. (I. 22.) NGM r. |
| földgáz, szállítóvezeték | 2008. évi XL. tv. (GET) |
| utak védőtávolsága | 1988. évi I. tv. (Kkt.) 42/A. § |

A hivatkozások általános javaslatok; a település tervéhez és a hatályos jogszabályokhoz
igazítani kell. Az övezeti előírások táblája a HÉSZ szövegéből nyelvi modellel készíthető el
(lásd fent).

## Ellenőrzés

`tests/integration/test_preset_hu_hesz.py` szintetikus tervvel teszteli. Valós tervvel:
`Q2VT_HESZ_PROJECT=/út/terv.qgs pytest -s tests/integration/test_preset_hu_hesz.py`
(a minta-tervben mind a 23 korlátozó réteget, a két vágóvonalat és a színes övezeti réteget
ismerte fel).
