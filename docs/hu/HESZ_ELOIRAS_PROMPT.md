# HÉSZ → övezeti előírás tábla nyelvi modellel (LLM prompt)

A *QWebMap HÉSZ* kiadás a telekinformációban és az övezetre kattintva megmutatja az övezet
előírásait (beépítettség, épületmagasság, telekterület …), ha a projektben van egy
**övezeti előírás tábla** (lásd [HESZ.md](HESZ.md) → *Övezeti előírások a webtérképen*).
Ez a leírás két promptot ad, amelyekkel egy nyelvi modell (ChatGPT, Claude, Gemini …) a HÉSZ
szövegéből elkészíti ezt a táblát, majd egy második, független menetben ellenőrzi.

> A modell tévedhet. A kész táblát egy ember, aki ismeri a tervet, **szúrópróbával ellenőrizze**
> (legalább 3–5 övezetet a HÉSZ-szel összevetve), mielőtt a térkép nyilvános lesz. A webtérkép
> tájékoztató jellegű; a hatályos rendelet szövege az irányadó.

## Lépések

1. **Az övezeti jelek a térképről.** A QGIS-ben jelöld ki a `szab_ov` mezős övezeti réteget, és a
   *Python konzolban* (Bővítmények → Python konzol) futtasd:

   ```python
   layer = iface.activeLayer()  # a szab_ov mezős övezeti réteg
   def jel(v):  # a jel úgy, ahogy a plugin összeveti (12.0 -> 12, szóközök nélkül)
       if isinstance(v, float) and v.is_integer():
           v = int(v)
       return str(v).strip()
   codes = sorted({jel(f["szab_ov"]) for f in layer.getFeatures() if f["szab_ov"]})
   print("\n".join(codes))
   ```

   A kiírt listát másold ki. A plugin a táblát a jel **pontos** egyezésével köti az övezetekhez
   (`Lke-2` ≠ `Lke2` ≠ `lke-2`; csak a jel elején és végén lévő szóközöket hagyja figyelmen
   kívül), ezért a modell ezt a listát kapja meg mérvadónak.
2. **A HÉSZ szövege.** Az egységes szerkezetű, hatályos rendelet a mellékleteivel együtt (az
   övezeti táblázatok gyakran mellékletben vannak). Word vagy szöveges PDF a legjobb; szkennelt
   PDF-et előbb szövegfelismerővel (OCR) alakíts szöveggé, és nézd meg, hogy a táblázatok
   számai olvashatók-e. Ha a modell felülete fogad fájlt, csatolhatod is.
3. **Ha a HÉSZ-t a térképpel együtt publikálod** (Publish ablak → Info fül → Dokumentumok), jegyezd
   fel a dokumentum ott megadott **címét** (pl. „Helyi építési szabályzat”). A tábla `rendelet`
   oszlopa erre hivatkozik, így a webtérképen a rendelet linkként nyílik. Ha nem publikálod,
   megadhatod helyette a rendelet webcímét (https://…), vagy hagyd üresen.
4. **1. prompt (tábla készítése):** másold be lent az *1. promptot* egy új beszélgetésbe, és a
   jelölt helyekre tedd be a fenti adatokat. Erős, hosszú szöveget kezelő modellt használj.
5. **2. prompt (független ellenőrzés):** **új** beszélgetésben (lehetőleg másik modellel) add meg a
   HÉSZ-t, az övezeti jeleket és az 1. prompt végleges CSV-jét. Ha eltérést talál, a
   forráshely alapján te döntsd el, melyik a helyes, és javítsd a CSV-t.
6. **Mentés:** a végleges CSV-t mentsd el `hesz_eloirasok.csv` néven **UTF-8** kódolással (pl.
   Jegyzettömbben: Mentés másként → Kódolás: UTF-8). Excelben ne nyisd meg és ne mentsd újra
   (átírja a tizedes pontot és az elválasztót).
7. **Betöltés a QGIS-be:** Réteg → Réteg hozzáadása → *Tagolt szöveges réteg hozzáadása*: fájl a
   CSV, formátum *CSV*, geometria: **Nincs geometria (csak attribútumtábla)**, a *Mezőtípusok
   felismerése* legyen bekapcsolva. A réteg neve legyen **„HÉSZ övezeti előírások”** (a névben
   legyen benne a „HÉSZ” vagy az „előírás” szó, erről ismeri fel az előbeállítás; ha több réteg
   is illik rá, a geometria nélküli tábla az elsődleges). Tartósabb, ha utána a projekt
   GeoPackage-ébe mented (jobb klikk → Exportálás → Elemek mentése másként → GeoPackage), és azt
   a táblát használod.
   - Csupa számból álló övezeti jelek (pl. `12`): a mezőtípus-felismerés számnak veszi őket, ez
     rendben van (a plugin a 12-t és a 12.0-t is „12”-nek veszi). **Kezdő nullás** jeleknél
     (`012`) viszont a nulla elveszne: ilyenkor a CSV mellé tegyél egy `hesz_eloirasok.csvt`
     fájlt, egyetlen sorral, amely minden oszlopot szövegnek jelöl:
     `"String","String",…` (22-szer), vagy kapcsold ki a mezőtípusok felismerését.
   - Ha egy számoszlopban szöveg is van (pl. „kialakult”), a QGIS az egész oszlopot szövegnek
     veszi; a számok ekkor a CSV-beli alakjukban (tizedesponttal) jelennek meg.
8. **Publish ablak → Előbeállítás (HÉSZ):** a jegyzetekben megjelenik: „Övezeti előírások:
   „HÉSZ övezeti előírások” (szab_ov szerint), N mező”. A *Parcel report* (telekinformáció) fülön
   ellenőrizd, és tegyél egy próbapublikálást: kattints néhány telekre (a telekinformációban
   telekrészenként látszanak az előírások) és telken kívüli övezetre, pl. útra (felugró ablak).

## A tábla oszlopai

Az oszlopnevek pontosan ezek legyenek (ékezet nélkül, kisbetűvel), ebben a sorrendben. Az üres
cellák nem jelennek meg; a webtérkép a jobb oldali címmel mutatja az oszlopokat. A QGIS-ben
megadott mezőálnév (alias) felülírja ezt a címet.

| Oszlop | Cím a webtérképen | Tartalom |
|---|---|---|
| `szab_ov` | (az övezet jele) | az övezeti jel **pontosan** úgy, ahogy a térkép listájában van |
| `megnevezes` | Övezet megnevezése | pl. „Kertvárosias lakóterület” |
| `beep_mod` | Beépítési mód | pl. „oldalhatáron álló (O)”, „szabadonálló (SZ)”, „kialakult (K)” |
| `min_ter` | Legkisebb telekterület (m²) | szám |
| `min_szel` | Legkisebb telekszélesség (m) | szám |
| `min_mely` | Legkisebb telekmélység (m) | szám |
| `beep_szaz` | Legnagyobb beépítettség (%) | szám |
| `terepalatti` | Terepszint alatti beépítés legnagyobb mértéke (%) | szám |
| `szintter` | Legnagyobb szintterületi mutató (m²/m²) | szám |
| `min_mag` | Legkisebb épületmagasság (m) | szám |
| `max_mag` | Legnagyobb épületmagasság (m) | szám |
| `max_epitm_mag` | Legnagyobb építménymagasság (m) | szám (régebbi HÉSZ-ekben) |
| `min_zold` | Legkisebb zöldfelület (%) | szám |
| `elokert` | Előkert (m) | szám vagy rövid szöveg |
| `oldalkert` | Oldalkert (m) | szám vagy rövid szöveg |
| `hatsokert` | Hátsókert (m) | szám vagy rövid szöveg |
| `kozmu` | Közművesítettség | pl. „teljes”, „részleges” |
| `rendeltetes` | Elhelyezhető rendeltetések | tömör felsorolás |
| `tiltott` | Nem helyezhető el | tömör felsorolás |
| `egyeb` | Egyéb előírás | lábjegyzetek, kivételek, feltételes értékek tömören |
| `hivatkozas` | HÉSZ hivatkozás | pl. „15. § (3); 2. melléklet” |
| `rendelet` | Rendelet | a térképpel publikált HÉSZ dokumentum címe, **vagy** a rendelet https:// címe, minden sorban ugyanaz |

## 1. prompt: a tábla elkészítése

Másold be az egészet, és töltsd ki a `<<< >>>` jelölt részeket.

````text
Magyar településrendezési szakértőként és pontos adatrögzítőként dolgozol. A feladatod: egy
település helyi építési szabályzatából (HÉSZ) elkészíteni az övezetek előírásainak tábláját CSV
formátumban, majd ezt tételesen ellenőrizni. A tábla egy webes térkép telekinformációjában
jelenik meg; minden hibás szám félrevezeti a telektulajdonosokat, ezért a pontosság mindennél
fontosabb. Ha valamit nem találsz vagy nem egyértelmű, hagyd üresen és jelezd – soha ne
találj ki, ne becsülj és ne pótolj értéket általános tudásból (OTÉK, TÉKA, más települések, szokásos
értékek).

=== BEMENETEK ===

[A] A térkép övezeti jelei (a `szab_ov` mező értékei; ez a lista mérvadó az írásmódra):
<<< ide a QGIS-ből kimásolt lista, soronként egy jel >>>

[B] A `rendelet` oszlop értéke (minden sorba ugyanez kerül; ha üres, az oszlop is üres marad):
<<< a térképpel publikált HÉSZ dokumentum címe, pl. „Helyi építési szabályzat”, VAGY a rendelet
https:// címe, VAGY üres >>>

[C] A HÉSZ teljes, egységes szerkezetű szövege a mellékletekkel (vagy csatolt fájl):
<<< ide a rendelet szövege >>>

=== A KIMENET OSZLOPAI (pontosan ezek, ebben a sorrendben) ===

szab_ov,megnevezes,beep_mod,min_ter,min_szel,min_mely,beep_szaz,terepalatti,szintter,min_mag,max_mag,max_epitm_mag,min_zold,elokert,oldalkert,hatsokert,kozmu,rendeltetes,tiltott,egyeb,hivatkozas,rendelet

- szab_ov: az övezeti jel, karakterre pontosan úgy, ahogy az [A] listában szerepel.
- megnevezes: az övezet / területfelhasználás megnevezése a HÉSZ szerint
  (pl. „Kertvárosias lakóterület”).
- beep_mod: beépítési mód teljes névvel, zárójelben a HÉSZ rövidítésével, pl.
  „oldalhatáron álló (O)”, „szabadonálló (SZ)”, „ikres (IKR)”, „zártsorú (Z)”,
  „kialakult (K)”. Ha a HÉSZ saját rövidítéseket definiál, azok jelentését használd.
- min_ter: a legkisebb kialakítható telekterület, m².
- min_szel, min_mely: a legkisebb telekszélesség és telekmélység, m.
- beep_szaz: a megengedett legnagyobb beépítettség, %.
- terepalatti: a terepszint alatti beépítés legnagyobb mértéke, %.
- szintter: a legnagyobb szintterületi mutató (m²/m²), ha a HÉSZ előírja.
- min_mag, max_mag: a legkisebb és a legnagyobb ÉPÜLETMAGASSÁG, m.
- max_epitm_mag: a legnagyobb ÉPÍTMÉNYMAGASSÁG, m. A régebbi HÉSZ-ek építménymagasságot, az
  újabbak épületmagasságot írnak elő; ez két külön fogalom (más a számítási módjuk), ne alakítsd
  át egymásba. Mindig abba az oszlopba írj, amelyik fogalmat a HÉSZ használja. Ha a HÉSZ
  harmadik magassági fogalmat használ (pl. homlokzatmagasság, gerincmagasság), ne írd a fenti
  oszlopokba, hanem az egyeb oszlopba, a fogalom nevével együtt.
- min_zold: a legkisebb zöldfelületi arány, %.
- elokert, oldalkert, hatsokert: a kert legkisebb mélysége / szélessége m-ben, ha a HÉSZ
  övezetenként előírja; ha szöveges szabály (pl. „kialakult”, „az utcában jellemző”), röviden
  szövegként.
- kozmu: a közművesítettség előírt mértéke (pl. „teljes”, „részleges”).
- rendeltetes: az elhelyezhető épületek / rendeltetések tömör felsorolása pontosvesszővel.
- tiltott: ami kifejezetten nem helyezhető el, tömören.
- egyeb: az övezetre vonatkozó lábjegyzet, kivétel vagy feltételes érték tömören, a HÉSZ
  szóhasználatával (pl. „Saroktelken a beépítettség legfeljebb 35%.”). Legfeljebb ~300
  karakter; ha hosszabb, a lényeg és a hivatkozás.
- hivatkozas: hol van az előírás a HÉSZ-ben, pl. „15. § (3); 2. melléklet”. Minden sorban
  legyen kitöltve.
- rendelet: minden sorban a [B] érték, betűre pontosan.

=== A MUNKA MENETE ===

1. Olvasd végig a teljes HÉSZ-t a mellékletekkel. Keresd meg:
   - az építési övezeteket (beépítésre szánt terület) és az övezeteket (beépítésre nem szánt
     terület: pl. mezőgazdasági, erdő, zöldterület, vízgazdálkodási, közlekedési);
   - az övezeti paramétereket tartalmazó táblázato(ka)t: egy övezet adatai több táblázatban is
     lehetnek (pl. telekalakítási és beépítési táblázat) – fésüld össze őket;
   - a területfelhasználási egységekre közösen vonatkozó szakaszokat (pl. „A kertvárosias
     lakóterület építési övezeteiben …”): ezek az adott egység minden övezetére érvényesek,
     kivéve, ha az övezet saját előírása eltér. Az övezet saját előírása az erősebb.
   Az általános, minden övezetre vonatkozó szabályokat (pl. „valamennyi építési övezetben …”)
   ne másold be minden sorba; csak akkor, ha egy oszlop értékét közvetlenül meghatározzák.
2. Feleltesd meg a HÉSZ övezeteit az [A] lista jeleinek. Az írásmód eltérhet (kötőjel, perjel,
   szóköz, kis- és nagybetű, alsó index, pl. „Lke₂” ↔ „Lke-2”); ilyenkor a szab_ov oszlopba
   az [A] lista alakja kerül, és a megfeleltetést a jelentésben felsorolod. Ha egy
   megfeleltetés nem egyértelmű, ne találgass: hagyd ki a sort, és jelezd.
3. Az értékek rögzítése:
   - Számok tizedesponttal, mértékegység, ezres elválasztó és szóköz nélkül: „30%” → 30;
     „4,5 m” → 4.5; „1 200 m²” → 1200.
   - „K” vagy „kialakult” egy számoszlopban → a szöveg: kialakult. (A beep_mod oszlopban a
     fenti alak: „kialakult (K)”.)
   - „–”, „-”, „nem szabályozott”, üres cella → üres cella. A 0 csak akkor 0, ha a HÉSZ
     tényleg 0-t ír.
   - Tartomány (pl. épületmagasság „3,5–6,0”) → min_mag 3.5 és max_mag 6.0, ha a HÉSZ
     egyértelműen legkisebb–legnagyobb értéket ad; különben szövegként az egyeb oszlopba.
   - Lábjegyzetes vagy feltételes érték (pl. „30*”, „saroktelken 35%”): az alapérték kerül az
     oszlopba, a feltétel tömören az egyeb oszlopba.
   - Ha egy övezeti jel a HÉSZ-ben több változatban szerepel (pl. eltérő értékek
     alövezetenként, de a térképen egy jel van), egy sort írj, az eltéréseket az egyeb
     oszlopba, és jelezd a jelentésben.
   - Ügyelj a szövegfelismerési hibákra (1 ↔ l, 0 ↔ O, 5 ↔ S, tizedesvessző elvesztése);
     ha egy szám gyanús, ne „javítsd ki”, hanem jelezd.
4. Ha a bemenet nem egységes szerkezetű (csak egy módosító rendelet vagy egy részlet), vagy
   hiányzik egy hivatkozott melléklet, ezt az elején jelezd, és csak a meglévő szövegből
   dolgozz.

=== KÉTSZERES ELLENŐRZÉS (kötelező, a végleges CSV előtt) ===

Az első változat elkészítése után végezd el az alábbi ellenőrzéseket. Minden hibát javíts, és
a javítást írd le.

E1 Teljesség: az [A] lista minden jeléhez van sor, vagy a jelentésben szerepel „nincs a
   HÉSZ-ben” megjegyzéssel. A HÉSZ minden övezetéhez, amely az [A] listában szerepel, van sor.
E2 Jelegyezés: minden szab_ov érték betűre pontosan előfordul az [A] listában.
E3 Visszakeresés: minden nem üres cellát keress vissza a HÉSZ-ben. Számjegyenként hasonlítsd
   össze. Nézd meg, hogy jó oszlopba került-e (legkisebb ↔ legnagyobb, % ↔ m ↔ m²,
   beépítettség ↔ zöldfelület, épületmagasság ↔ építménymagasság).
E4 Oszloponkénti újraolvasás: az övezeti táblázatot olvasd végig még egyszer, de most
   OSZLOPONKÉNT (egy paraméter minden övezetre), és vesd össze a CSV-vel. Ez kiszűri az
   elcsúszott sorokat és oszlopokat.
E5 Ésszerűségi vizsgálat (csak jelzés, nem javítás forrás nélkül):
   - beep_szaz, terepalatti, min_zold 0 és 100 között;
   - beep_szaz + min_zold legfeljebb 100;
   - magasságok 0 és 100 m között, min_mag ≤ max_mag;
   - min_ter > 0, szintter 0 és 10 között;
   - ugyanazon területfelhasználási egység övezetei között kiugró érték (pl. 300 m helyett
     30 m).
   Ha valami gyanús, nézd meg újra a forrást. Ha a forrás valóban ezt írja, hagyd meg, és
   jelezd a jelentésben.
E6 Kitalált adat: töröld azt az értéket, amelyhez nem tudsz HÉSZ-beli helyet (§, bekezdés,
   melléklet, táblázatsor) mondani.
E7 CSV-forma:
   - a fejléc pontosan a fenti;
   - minden sorban 22 mező;
   - vesszővel elválasztva;
   - a szöveges cellák kettős idézőjelben, a cellán belüli idézőjel megduplázva ("");
   - a számok idézőjel nélkül;
   - cellán belül nincs sortörés;
   - a rendelet oszlop minden sorban azonos.

=== A VÁLASZ FORMÁJA ===

Négy rész, ebben a sorrendben:

## 1. Munkatábla
Övezetenként egy kis táblázat: oszlop | érték | forrás (§ / melléklet / táblázatsor) |
szó szerinti idézet (legfeljebb 20 szó). Csak a kitöltött oszlopokat sorold fel.

## 2. Ellenőrzés
E1–E7 egyenként: mit vizsgáltál, mit találtál, mit javítottál. Ha nem volt hiba, írd oda:
„rendben”.

## 3. Végleges CSV
Egyetlen ```csv kódblokk, benne csak a fejléc és az adatsorok, semmi más.

## 4. Jelentés az ember számára
- az [A] lista jelei, amelyekhez a HÉSZ-ben nincs előírás;
- a HÉSZ övezetei, amelyek nincsenek a térképen;
- a jel-megfeleltetések, ahol az írásmód eltért;
- a bizonytalan vagy gyanús értékek, forráshellyel – ezeket egy embernek kell ellenőriznie;
- ha a bemenet hiányos volt (nem egységes szerkezetű, hiányzó melléklet, olvashatatlan
  táblázat).
````

## 2. prompt: független ellenőrzés

Új beszélgetésben (lehetőleg más modellel) futtasd, hogy az első menet hibái ne ismétlődjenek.

````text
Egy település helyi építési szabályzatából (HÉSZ) készült CSV táblát kell ellenőrizned, amely
egy webes térkép telekinformációjában jelenik meg. A táblát egy másik szerző készítette, és
hibás lehet. Ne a CSV-ből indulj ki: minden értéket a HÉSZ szövegéből olvass ki újra, és
utána hasonlítsd össze a CSV-vel. Soha ne pótolj értéket általános tudásból.

[A] A térkép övezeti jelei (mérvadó írásmód):
<<< ugyanaz a lista, mint az 1. promptban >>>

[B] A HÉSZ teljes szövege a mellékletekkel (vagy csatolt fájl):
<<< a rendelet szövege >>>

[C] Az ellenőrzendő CSV:
<<< az 1. prompt végleges CSV-je >>>

Az oszlopok jelentése:
- szab_ov: övezeti jel;
- megnevezes: megnevezés;
- beep_mod: beépítési mód;
- min_ter: legkisebb telekterület, m²;
- min_szel: legkisebb telekszélesség, m;
- min_mely: legkisebb telekmélység, m;
- beep_szaz: legnagyobb beépítettség, %;
- terepalatti: terepszint alatti beépítés, %;
- szintter: legnagyobb szintterületi mutató;
- min_mag és max_mag: legkisebb és legnagyobb ÉPÜLETMAGASSÁG, m;
- max_epitm_mag: legnagyobb ÉPÍTMÉNYMAGASSÁG, m;
- min_zold: legkisebb zöldfelület, %;
- elokert, oldalkert, hatsokert: a kertek mérete, m;
- kozmu: közművesítettség;
- rendeltetes: elhelyezhető rendeltetések;
- tiltott: ami nem helyezhető el;
- egyeb: egyéb előírás;
- hivatkozas: HÉSZ hely;
- rendelet: a rendelet dokumentumának címe vagy webcíme.

A számok tizedesponttal, mértékegység nélkül szerepelnek. A „kialakult” szöveg a HÉSZ „K” /
„kialakult” értéke. Az üres cella azt jelenti: a HÉSZ nem szabályozza.

Feladat:
1. Minden sorhoz és minden oszlophoz keresd meg a HÉSZ-ben az értéket (§, bekezdés, melléklet,
   táblázatsor), és vesd össze a CSV cellájával: számjegyenként, a mértékegység és a
   legkisebb ↔ legnagyobb értelmezés szerint, épületmagasság ↔ építménymagasság szerint.
2. Nézd meg az üres cellákat is: van-e a HÉSZ-ben érték, amely kimaradt?
3. Nézd meg, hogy az [A] lista minden jele szerepel-e, és a szab_ov értékek betűre pontosan
   egyeznek-e.
4. Nézd meg a CSV formáját:
   - pontosan 22 mező minden sorban;
   - vessző az elválasztó;
   - a szöveg idézőjelben, a szám idézőjel nélkül;
   - nincs sortörés cellán belül.

A válaszod:
## Eltérések
Táblázat: szab_ov | oszlop | CSV-ben | helyesen | forrás (hely + szó szerinti idézet,
legfeljebb 20 szó) | biztos / bizonytalan. Ha nincs eltérés, írd: „Nincs eltérés.”
## Javított CSV
Csak ha volt biztos eltérés: a teljes javított CSV egy ```csv kódblokkban (a bizonytalan
eltéréseket ne javítsd, csak sorold fel).
## Megjegyzések
Ami egy embernek még ellenőrizendő.
````

## Minta: a végleges CSV

Egy kitalált település (Mintafalva) táblája; csak a formát mutatja, az értékek nem valósak.

<!-- minta-csv -->
```csv
szab_ov,megnevezes,beep_mod,min_ter,min_szel,min_mely,beep_szaz,terepalatti,szintter,min_mag,max_mag,max_epitm_mag,min_zold,elokert,oldalkert,hatsokert,kozmu,rendeltetes,tiltott,egyeb,hivatkozas,rendelet
"Lke-1","Kertvárosias lakóterület","oldalhatáron álló (O)",700,16,,30,,,,5.5,,50,5,,6,"teljes","lakó; helyi ellátást szolgáló kereskedelmi, szolgáltató","üzemanyagtöltő állomás","Saroktelken a beépítettség legfeljebb 35%.","15. § (3); 2. melléklet","Helyi építési szabályzat"
"Lke-2","Kertvárosias lakóterület","szabadonálló (SZ)",900,18,,25,,,,6,,60,5,,6,"teljes","lakó","",,"15. § (4); 2. melléklet","Helyi építési szabályzat"
"Vt","Településközpont terület","zártsorú (Z)",400,,,60,80,,4.5,9,,20,"kialakult",,,"teljes","igazgatási; kereskedelmi, szolgáltató; lakó","","Az utcai homlokzatmagasság legfeljebb 7,5 m.","17. §; 2. melléklet","Helyi építési szabályzat"
"Gksz","Kereskedelmi, szolgáltató gazdasági terület","szabadonálló (SZ)",2000,30,,50,,,,12,,25,10,6,10,"teljes","kereskedelmi, szolgáltató; raktározás","lakó (kivéve a szolgálati lakás)",,"19. § (2); 2. melléklet","Helyi építési szabályzat"
"Má","Általános mezőgazdasági terület","szabadonálló (SZ)",,,,3,,,,7.5,,,,,,"részleges","mezőgazdasági termelés; birtokközpont","",,"24. § (5)","Helyi építési szabályzat"
"KÖu","Közúti közlekedési terület",,,,,,,,,,,,,,,,"","","A szabályozási szélességet a szabályozási terv jelöli.","27. §","Helyi építési szabályzat"
```
