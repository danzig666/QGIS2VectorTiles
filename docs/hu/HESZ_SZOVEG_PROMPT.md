# HÉSZ teljes szövege övezetenként, nyelvi modellel (LLM prompt)

Az [övezeti előírás tábla](HESZ_ELOIRAS_PROMPT.md) a számszerű előírásokat adja (beépítettség,
épületmagasság …). Ez a leírás egy **második, külön táblát** készít: övezetenként **a HÉSZ minden
olyan rendelkezését, amelyet az övezetben alkalmazni kell**, szó szerint, egyszerű HTML-ben:

- az övezet saját előírásait;
- a területfelhasználási egység (pl. kertvárosias lakóterület) közös előírásait;
- a beépítésre szánt / nem szánt területek általános előírásait;
- a minden területre vonatkozó általános előírásokat (telekalakítás, közművek, parkolás,
  környezetvédelem …);
- a feltételesen alkalmazandó előírásokat (pl. ha a telket műemléki környezet vagy védőtávolság
  érinti), a feltétel megnevezésével;
- az övezetre vonatkozó mellékletrészeket (pl. az övezeti táblázat sorát).

A tábla két mezője: **`szab_ov`** (ugyanaz az övezeti jel, mint a térképen) és **`eloiras_html`**
(a szöveg). A webtérképen a telekinformációban minden övezet alatt ott van a lenyitható
„**A(z) Lke-1 övezet teljes előírásai**” rész, és az övezetre kattintva a felugró ablakban is. A
szöveg csak lenyitáskor töltődik le.

> A modell tévedhet, és a „mi vonatkozik az övezetre” kérdés jogi mérlegelés is. A kész szöveget
> egy ember, aki ismeri a tervet, ellenőrizze legalább 2–3 övezetben, mielőtt a térkép nyilvános
> lesz. A webtérkép tájékoztató jellegű; a hatályos rendelet szövege az irányadó.

## Hogyan működik

A modell nem közvetlenül táblát ír (több oldalas HTML egy CSV-cellában törékeny volna), hanem
**egyetlen HTML fájlt**, megjelölt blokkokkal:

```html
<!-- KÖZÖS: altalanos-kozmu -->
<h3>Általános előírások</h3>
<h4>8. § (1)</h4>
<p>(1) Az építési telken …</p>
<!-- VÉGE -->

<!-- ÖVEZET: Lke-1 -->
<h3>Az övezet saját előírásai</h3>
<h4>15. § (3)</h4>
<p>(3) Az Lke-1 jelű építési övezetben …</p>
<!-- BEILLESZT: lke-kozos -->
<!-- BEILLESZT: altalanos-kozmu -->
<!-- VÉGE -->
```

- **`KÖZÖS`** blokk: a több övezetre is vonatkozó rész, csak egyszer leírva. Így rövidebb a válasz,
  és nem lehet ugyanaz a szöveg két helyen eltérő.
- **`ÖVEZET`** blokk: egy övezet teljes szövege. A `BEILLESZT` jelek helyére kerülnek a közös
  blokkok.
- A fájl böngészőben is megnyitható, így átolvasható. Egy QGIS-szkript (lent) alakítja táblává: a
  beillesztéseket kibontja, és jelzi a hibákat (ismeretlen vagy fel nem használt blokk, hiányzó
  övezet).

A megengedett HTML: `h3`, `h4`, `h5`, `p`, `ul`, `li`, `strong`, `em`, `br`, `sup`, `sub`,
`table`, `tr`, `th`, `td`, `blockquote`, attribútumok nélkül (táblázatcellán a `colspan`/`rowspan`
megengedett). Minden mást a plugin és a webtérkép is eltávolít (a szöveg megmarad).

## Lépések

1. **Az övezeti jelek a térképről:** ugyanúgy, mint az [övezeti előírás táblánál](HESZ_ELOIRAS_PROMPT.md)
   (1. lépés, Python konzol).
2. **A HÉSZ szövege:** az egységes szerkezetű, hatályos rendelet a mellékleteivel együtt (Word
   vagy szöveges PDF; szkennelt PDF-et előbb szövegfelismerővel alakíts szöveggé).
3. **1. prompt** (lent) egy új beszélgetésben. Hosszú HÉSZ-nél a modell részletekben válaszol: írd
   neki, hogy „folytasd”, amíg a `<!-- KÉSZ -->` jel meg nem jelenik. Erős, hosszú szöveget
   kezelő modellt használj.
4. **A HTML mentése:** a válaszok `html` kódblokkjait sorrendben másold egyetlen fájlba
   (`hesz_szoveg.html`, **UTF-8** kódolással, pl. Jegyzettömb → Mentés másként → Kódolás: UTF-8).
   Nyisd meg böngészőben, és nézd át.
5. **2. prompt** (lent): **új** beszélgetésben, lehetőleg másik modellel, független ellenőrzés.
   A javított blokkokat cseréld ki a fájlban (ugyanazzal a `<!-- … -->` fejjel).
6. **Átalakítás táblává:** a QGIS *Python konzoljában* (Bővítmények → Python konzol → Szerkesztő
   megjelenítése) futtasd a lenti **átalakító szkriptet**, a `FORRAS`, `CEL` és `JELEK` sorokat
   kitöltve. Létrehoz egy „**HÉSZ övezeti előírások szövege**” táblát egy GeoPackage-ben, és
   hozzáadja a projekthez. A konzolban felsorolja az eredményt és a hibákat.
7. **Publish ablak → Előbeállítás (HÉSZ):** a jegyzetekben megjelenik: „Övezeti előírások teljes
   szövege: …”. A *Parcel report* fülön a *Full texts table* sorban ellenőrizheted. Próbapublikálás
   után nyiss le néhány övezetet a telekinformációban.

## 1. prompt: a szöveg elkészítése

Másold be az egészet, és töltsd ki a `<<< >>>` jelölt részeket.

````text
Magyar településrendezési szakértőként és gondos jogi szövegszerkesztőként dolgozol. A feladatod:
egy település helyi építési szabályzatából (HÉSZ) övezetenként összegyűjteni MINDEN olyan
rendelkezést, amelyet az adott övezetben (építési övezetben vagy övezetben) alkalmazni kell, és
ezeket szó szerint, egyszerű, jól tagolt HTML-ben leírni. A szöveg egy webes térkép
telekinformációjában jelenik meg a telektulajdonosoknak; a hiányzó vagy pontatlan rendelkezés
félrevezet, ezért a teljesség és a szó szerinti pontosság mindennél fontosabb.

Alapszabályok:
- A rendelkezések szövegét SZÓ SZERINT másold: ne rövidíts, ne foglald össze, ne fogalmazd át, ne
  javíts nyelvtant. A számok, mértékegységek, hivatkozások betűre egyezzenek.
- Saját szöveget csak a megengedett szerkezeti címekben (h3, h4) és a feltételek
  megnevezésében írhatsz.
- Semmit ne találj ki, és ne vegyél át más jogszabályból (OTÉK, TÉKA, Étv. …) vagy általános
  tudásból. Ha a HÉSZ más jogszabályra hivatkozik, a hivatkozás szövege marad, a hivatkozott
  jogszabály szövegét nem másolod be.
- Hatályon kívül helyezett szakaszt, módosítási lábjegyzetet („Módosította: …”), a rendelet
  hatálybalépésére, eljárási vagy módosító rendelkezéseire vonatkozó részt nem veszel fel.

=== BEMENETEK ===

[A] A térkép övezeti jelei (a `szab_ov` mező értékei; ez a lista mérvadó az írásmódra):
<<< ide a QGIS-ből kimásolt lista, soronként egy jel >>>

[B] A HÉSZ teljes, egységes szerkezetű szövege a mellékletekkel (vagy csatolt fájl):
<<< ide a rendelet szövege >>>

=== MUNKAMENET ===

1. SZERKEZETI TÉRKÉP. Olvasd végig a teljes HÉSZ-t a mellékletekkel, és készíts egy
   munkatáblát BEKEZDÉSENKÉNT (ahol a bekezdés pontokra oszlik, és a pontok más-más övezetre
   vonatkoznak, pontonként):
   hely (pl. 15. § (3) b)) | rövid tárgy (legfeljebb 8 szó) | hatály: kire vonatkozik |
   blokk-azonosító.
   A hatály egyike legyen:
   - egy vagy több övezeti jel (az [A] lista alakjában);
   - egy területfelhasználási egység minden övezete (pl. „minden Lke”);
   - „beépítésre szánt területek” / „beépítésre nem szánt területek” / „minden terület”;
   - „feltételes: <feltétel>”: csak ha a telket valami érinti (pl. műemléki környezet, régészeti
     lelőhely, védőtávolság, vízbázis, helyi védelem, szabályozási vonal, sajátos jogintézmény);
     add meg, melyik övezetekben fordulhat elő, ha a HÉSZ ezt meghatározza, különben minden
     érintett övezetben;
   - „nem övezeti: <ok>”: pl. a rendelet hatálya, hatálybalépés, eljárás, értelmező rendelkezés
     (lásd lent), mellékletek felsorolása.
   A fogalommeghatározásokat (értelmező rendelkezések) csak akkor vedd fel egy övezethez, ha az
   övezetre vonatkozó rendelkezések használják a fogalmat; ilyenkor csak az érintett
   fogalmakat.
   A HÉSZ belső hivatkozásait kövesd: ha egy, az övezetre vonatkozó rendelkezés a HÉSZ egy másik
   részére hivatkozik („a 8. § (2) bekezdés szerint”), a hivatkozott rész is az övezet
   szövegébe tartozik.

2. BLOKKOK. Ami több övezetre is vonatkozik, egy KÖZÖS blokkba kerül (azonosító: kisbetű,
   számjegy, kötőjel, pl. `lke-kozos`, `altalanos-telekalakitas`, `felt-mukemleki-kornyezet`).
   Minden övezet egy ÖVEZET blokkot kap, amely a saját szövegét tartalmazza, és a rá vonatkozó
   közös blokkokat BEILLESZT jelekkel hívja be. Egy közös blokk további közös blokkot is
   beilleszthet, de kerüld a mély láncokat.

3. TAGOLÁS. Minden ÖVEZET blokk ebben a sorrendben épüljön fel; az üres részeket hagyd el:
   <h3>Az övezet saját előírásai</h3>
   <h3>A(z) <területfelhasználási egység neve> közös előírásai</h3>
   <h3>A beépítésre szánt területek általános előírásai</h3>  (vagy: … nem szánt …)
   <h3>Általános előírások</h3>
   <h3>Feltételesen alkalmazandó előírások</h3>
       itt minden feltétel saját <h4>-et kap, pl. <h4>Ha a telket műemléki környezet érinti</h4>
   <h3>Az övezetre vonatkozó mellékletrészek</h3>  (pl. az övezeti táblázat fejléce és az övezet
       sora <table>-ként, a mellékletre hivatkozó <h4>-gyel)
   <h3>Fogalmak</h3>  (csak az érintett fogalommeghatározások)
   A h3 címek alatt a rendelkezéseket a HÉSZ sorrendjében add. Minden szakasz vagy bekezdés
   előtt egy <h4> mondja meg a helyét, pl. <h4>15. § (3)</h4>, vagy ha a szakasznak címe van:
   <h4>12. § Közművek</h4>. A bekezdés szövege <p>-ben, az eredeti számozással kezdve: <p>(3) …</p>.
   A pontokat (a), b) …) és alpontokat egy <ul>-ben add, minden pont egy <li>, az eredeti
   betűjelével kezdve: <li>a) …</li>. Más listát ne használj. Kiemelés csak ott, ahol a HÉSZ is
   kiemel (<strong>, <em>). Táblázat: <table><tr><th>…</th></tr><tr><td>…</td></tr></table>.

4. FORMA.
   - Csak ezek az elemek: h3, h4, h5, p, ul, li, strong, em, br, sup, sub, table, tr, th, td,
     blockquote. Attribútumot ne használj (kivétel: colspan, rowspan cellán). Ne írj <html>,
     <head>, <body>, <style>, <script>, <a> elemet.
   - Blokkjelek pontosan így, mindegyik külön sorban:
     <!-- KÖZÖS: azonosító --> … <!-- VÉGE -->
     <!-- ÖVEZET: övezeti jel --> … <!-- VÉGE -->
     <!-- BEILLESZT: azonosító -->   (csak blokkon belül)
   - Az ÖVEZET jel betűre egyezzen az [A] lista egy elemével. Az [A] lista minden jele kapjon
     ÖVEZET blokkot. Ha egy jelhez a HÉSZ-ben semmi sem tartozik, a blokkban egyetlen
     <p><em>A HÉSZ nem tartalmaz erre az övezetre vonatkozó előírást.</em></p> álljon, és
     jelezd a jelentésben.
   - Először minden KÖZÖS blokk, utána az ÖVEZET blokkok, az [A] lista sorrendjében.
   - Ha a válasz nem fér el egy üzenetben, blokkhatáron állj meg, írd a végére:
     <!-- FOLYTATÁS KÖVETKEZIK -->, és várd a „folytasd” üzenetet. Az utolsó részlet végére
     írd: <!-- KÉSZ -->.

=== KÉTSZERES ELLENŐRZÉS (kötelező, a HTML kiírása előtt) ===

E1 Lefedettség: a szerkezeti térkép minden bekezdése (pontja) vagy legalább egy övezethez van
   rendelve, vagy „nem övezeti” indokkal. Sorold fel a „nem övezeti” helyeket az indokkal.
E2 Övezetenkénti teljesség: minden övezetre menj végig a HÉSZ-en az elejétől a végéig, és
   ellenőrizd, hogy minden rá vonatkozó rendelkezés benne van az ÖVEZET blokkjában vagy a
   beillesztett közös blokkokban. Különösen: a területfelhasználási egység közös szakasza, a
   telekalakítási, közmű-, parkolási, zöldfelületi, környezetvédelmi, kerítés- és
   reklámszabályok, a melléklet övezeti táblázatának sora.
E3 Szó szerinti egyezés: minden bemásolt szöveget vess össze a forrással (szavanként, számjegyenként,
   írásjelekkel). Javítsd az eltéréseket.
E4 Belső hivatkozások: minden „… § … bekezdés szerint” típusú hivatkozás célja szerepel az
   övezet szövegében (ha az övezetre alkalmazandó).
E5 Feltételek: a feltételes rendelkezések csak a „Feltételesen alkalmazandó előírások” alatt
   vannak, mindegyik a feltétel megnevezésével.
E6 Jelek: minden ÖVEZET jel betűre szerepel az [A] listában, és az [A] lista minden jele kapott
   blokkot.
E7 Forma: csak megengedett elemek, nincs attribútum, minden blokk lezárva (VÉGE), minden
   BEILLESZT azonosítóhoz van KÖZÖS blokk, nincs fel nem használt KÖZÖS blokk.

=== A VÁLASZ FORMÁJA ===

## 1. Szerkezeti térkép
A munkatábla (hely | tárgy | hatály | blokk).

## 2. Ellenőrzés
E1–E7 egyenként: mit vizsgáltál, mit találtál, mit javítottál; ha nem volt hiba: „rendben”.

## 3. HTML
Egy vagy több ```html kódblokk, bennük csak a blokkok (KÖZÖS, majd ÖVEZET), a végén <!-- KÉSZ -->.

## 4. Jelentés az ember számára
- az [A] lista jelei, amelyekhez a HÉSZ-ben nincs előírás;
- a HÉSZ övezetei, amelyek nincsenek a térképen;
- a mérlegelést igénylő hozzárendelések (pl. nem egyértelmű, hogy egy szakasz vonatkozik-e egy
  övezetre), hellyel;
- olvashatatlan vagy hiányos részek a bemenetben.
````

## 2. prompt: független ellenőrzés

Új beszélgetésben (lehetőleg más modellel) futtasd.

````text
Egy település helyi építési szabályzatából (HÉSZ) készült, övezetenkénti HTML szöveget kell
ellenőrizned. Minden övezet blokkjának (a beillesztett közös blokkokkal együtt) a HÉSZ MINDEN
olyan rendelkezését szó szerint tartalmaznia kell, amelyet az övezetben alkalmazni kell: az
övezet saját előírásait, a területfelhasználási egység közös előírásait, az általános
előírásokat, a feltételesen alkalmazandókat (a feltétel megnevezésével), az övezetre vonatkozó
mellékletrészeket és az érintett fogalmakat. A szöveget egy másik szerző készítette, és hibás
lehet. Ne a HTML-ből indulj ki: a HÉSZ-t olvasd végig, és ahhoz mérd a HTML-t. Semmit ne
pótolj más jogszabályból vagy általános tudásból.

A blokkjelek: <!-- KÖZÖS: azonosító --> … <!-- VÉGE -->, <!-- ÖVEZET: jel --> … <!-- VÉGE -->,
<!-- BEILLESZT: azonosító --> (a közös blokk szövege a helyére kerül). Megengedett elemek: h3, h4,
h5, p, ul, li, strong, em, br, sup, sub, table, tr, th, td, blockquote, attribútumok nélkül.

[A] A térkép övezeti jelei:
<<< ugyanaz a lista, mint az 1. promptban >>>

[B] A HÉSZ teljes szövege a mellékletekkel (vagy csatolt fájl):
<<< a rendelet szövege >>>

[C] Az ellenőrzendő HTML:
<<< a hesz_szoveg.html tartalma >>>

Feladat:
1. Minden övezetre: menj végig a HÉSZ-en az elejétől a végéig, és jegyezd fel azokat a
   rendelkezéseket, amelyek az övezetre vonatkoznak, de hiányoznak a blokkjából (a beillesztett
   közös blokkokat is beleértve), illetve amelyek benne vannak, de nem vonatkoznak rá.
2. Szúrópróbával, de legalább övezetenként öt helyen vesd össze a szöveget a forrással szavanként
   és számjegyenként; a közös blokkokat teljes egészében.
3. Nézd meg a feltételes rendelkezések elhelyezését és a feltételek megnevezését.
4. Nézd meg a formát: blokkjelek, ismeretlen BEILLESZT azonosító, fel nem használt KÖZÖS blokk,
   nem megengedett elem vagy attribútum, az [A] lista minden jele szerepel-e.

A válaszod:
## Eltérések
Táblázat: blokk | hiba fajtája (hiányzik / fölösleges / szövegeltérés / feltétel / forma) |
hely a HÉSZ-ben | mi a helyes (szó szerinti idézet) | biztos / bizonytalan.
Ha nincs eltérés: „Nincs eltérés.”
## Javított blokkok
Csak a biztos hibákat tartalmazó blokkok, teljes egészükben, ugyanazzal a fejjel, ```html
kódblokkokban (a bizonytalanokat csak sorold fel).
## Megjegyzések
Ami egy embernek még ellenőrizendő.
````

## Átalakító szkript (QGIS Python konzol)

Bővítmények → Python konzol → *Szerkesztő megjelenítése*, illeszd be, töltsd ki a három nagybetűs
sort, és futtasd. A `JELEK`-be a térkép övezeti jeleit másold (ugyanazt a listát, mint a
promptba); üresen hagyva nem ellenőrzi őket.

<!-- atalakito -->
```python
import os
import re

from qgis.core import QgsFeature, QgsProject, QgsVectorFileWriter, QgsVectorLayer

FORRAS = r"C:\terv\hesz_szoveg.html"     # a nyelvi modell HTML-je (UTF-8)
CEL = r"C:\terv\hesz_szoveg.gpkg"        # ide kerül a tábla (GeoPackage)
JELEK = """
"""                                      # a térkép övezeti jelei, soronként egy (üres: nincs ellenőrzés)
NEV = "HÉSZ övezeti előírások szövege"   # erről a névről ismeri fel az előbeállítás

szoveg = open(FORRAS, encoding="utf-8").read()
blokkok = re.findall(r"<!--\s*(KÖZÖS|ÖVEZET):\s*(.+?)\s*-->(.*?)<!--\s*VÉGE\s*-->", szoveg, re.S)
kozos, ovezetek, hibak, hasznalt = {}, {}, [], set()
for fajta, nev, tartalom in blokkok:
    cel = kozos if fajta == "KÖZÖS" else ovezetek
    if nev.strip() in cel:
        hibak.append(f"kétszer szereplő {fajta} blokk: {nev.strip()}")
    cel[nev.strip()] = tartalom


def kibont(tartalom, lanc=()):
    def csere(talalat):
        azon = talalat.group(1).strip()
        if azon not in kozos:
            hibak.append(f"ismeretlen BEILLESZT: {azon}")
            return ""
        if azon in lanc or len(lanc) > 5:
            hibak.append(f"körkörös vagy túl mély beillesztés: {azon}")
            return ""
        hasznalt.add(azon)
        return kibont(kozos[azon], lanc + (azon,))
    return re.sub(r"<!--\s*BEILLESZT:\s*(.+?)\s*-->", csere, tartalom)


# string(0): korlátlan hosszú szöveg (a „string” 255 karakternél levágná, illetve eldobná)
reteg = QgsVectorLayer("None?field=szab_ov:string(0)&field=eloiras_html:string(0)", NEV, "memory")
elemek = []
for jel, tartalom in ovezetek.items():
    html = re.sub(r"<!--.*?-->", "", kibont(tartalom), flags=re.S).strip()
    elem = QgsFeature(reteg.fields())
    elem.setAttributes([jel, html])
    elemek.append(elem)
if not reteg.dataProvider().addFeatures(elemek)[0]:
    raise RuntimeError("Az övezetek nem tárolhatók: " + reteg.dataProvider().lastError())
for azon in sorted(set(kozos) - hasznalt):
    hibak.append(f"fel nem használt KÖZÖS blokk: {azon}")
vart = {sor.strip() for sor in JELEK.splitlines() if sor.strip()}
if vart:
    for jel in sorted(vart - set(ovezetek)):
        hibak.append(f"nincs blokk ehhez az övezethez: {jel}")
    for jel in sorted(set(ovezetek) - vart):
        hibak.append(f"nem a térkép jele: {jel}")

opciok = QgsVectorFileWriter.SaveVectorOptions()
opciok.driverName = "GPKG"
opciok.layerName = NEV
opciok.actionOnExistingFile = (QgsVectorFileWriter.CreateOrOverwriteLayer if os.path.exists(CEL)
                               else QgsVectorFileWriter.CreateOrOverwriteFile)
eredmeny = QgsVectorFileWriter.writeAsVectorFormatV3(reteg, CEL, QgsProject.instance().transformContext(), opciok)
if eredmeny[0] != QgsVectorFileWriter.NoError:
    raise RuntimeError(f"A tábla nem írható: {eredmeny[1]}")
for regi in QgsProject.instance().mapLayersByName(NEV):
    QgsProject.instance().removeMapLayer(regi.id())
uj = QgsVectorLayer(f"{CEL}|layername={NEV}", NEV, "ogr")
QgsProject.instance().addMapLayer(uj)
print(f"Kész: {len(elemek)} övezet, {len(kozos)} közös blokk → {CEL}")
for jel, tartalom in ovezetek.items():
    print(f"  {jel}: {len(kibont(tartalom)) // 1000} ezer karakter")
for hiba in hibak:
    print("HIBA:", hiba)
```

## Minta

Egy kitalált település (Mintafalva) két övezete; csak a formát mutatja, a szöveg nem valós.

<!-- minta-html -->
```html
<!-- KÖZÖS: lke-kozos -->
<h3>A kertvárosias lakóterület közös előírásai</h3>
<h4>14. § (1)</h4>
<p>(1) A kertvárosias lakóterület építési övezeteiben a telkenként elhelyezhető lakások száma legfeljebb kettő.</p>
<!-- VÉGE -->

<!-- KÖZÖS: altalanos -->
<h3>Általános előírások</h3>
<h4>6. § Telekalakítás</h4>
<p>(2) Nyúlványos telek nem alakítható ki.</p>
<h4>8. § (1)</h4>
<p>(1) Az építési telken a keletkező csapadékvizet</p>
<ul>
<li>a) telken belül kell elszikkasztani, vagy</li>
<li>b) a csapadékvíz-elvezető hálózatba kell vezetni.</li>
</ul>
<!-- VÉGE -->

<!-- KÖZÖS: felt-mukemleki -->
<h3>Feltételesen alkalmazandó előírások</h3>
<h4>Ha a telket műemléki környezet érinti</h4>
<p>(4) Műemléki környezetben az utcai homlokzaton anyagában színezett vakolat nem alkalmazható.</p>
<!-- VÉGE -->

<!-- ÖVEZET: Lke-1 -->
<h3>Az övezet saját előírásai</h3>
<h4>15. § (3)</h4>
<p>(3) Az Lke-1 jelű építési övezetben az előkert mérete 5,0 m.</p>
<!-- BEILLESZT: lke-kozos -->
<!-- BEILLESZT: altalanos -->
<!-- BEILLESZT: felt-mukemleki -->
<h3>Az övezetre vonatkozó mellékletrészek</h3>
<h4>2. melléklet: Az építési övezetek előírásai</h4>
<table>
<tr><th>Övezeti jel</th><th>Beépítési mód</th><th>Legnagyobb beépítettség (%)</th><th>Legnagyobb épületmagasság (m)</th></tr>
<tr><td>Lke-1</td><td>O</td><td>30</td><td>5,5</td></tr>
</table>
<!-- VÉGE -->

<!-- ÖVEZET: KÖu -->
<h3>Az övezet saját előírásai</h3>
<h4>27. § (1)</h4>
<p>(1) A közúti közlekedési területen az út szabályozási szélességét a szabályozási terv jelöli.</p>
<!-- BEILLESZT: altalanos -->
<!-- VÉGE -->
<!-- KÉSZ -->
```
