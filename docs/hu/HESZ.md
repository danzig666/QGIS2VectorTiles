# Magyar HÉSZ változat (`hu-hesz` ág)

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

Nem korlátozás: feliratok, szintvonal, házszám, épületek, alrészletek, közigazgatási határ,
települések, belterülethatár, kerékpárút, a szabályozás övezeti rétegei.

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
igazítani kell. A HÉSZ szöveges előírásainak (Word) importja egy későbbi lépés.

## Ellenőrzés

`tests/integration/test_preset_hu_hesz.py` szintetikus tervvel teszteli. Valós tervvel:
`Q2VT_HESZ_PROJECT=/út/terv.qgs pytest -s tests/integration/test_preset_hu_hesz.py`
(a minta-tervben mind a 23 korlátozó réteget, a két vágóvonalat és a színes övezeti réteget
ismerte fel).
