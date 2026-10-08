"""
Preset: Hungarian settlement plan (településrendezési terv / HÉSZ).

Fills the parcel report (telekinformáció) from the usual layer and field
names of Hungarian zoning plans:

* parcels: the "Földrészletek" layer, key ``hrsz``;
* zoning: the polygon layer with the zone code ``szab_ov`` (the one drawn
  with colours, so the report shows the zone's legend graphic), zone values
  ``p_beepmod``, ``p_beepszaz``, ``p_beepmag``, ``p_terulet``, ``p_zold``,
  ``p_szabkieg``;
* cut lines: "Szabályozási vonal" and "Övezethatár";
* zone regulations: a table (any layer, also one without geometry) named
  like "övezeti előírások" / "HÉSZ" with a zone code field: its other fields
  are shown in the parcel report and in a zone's popup, a reference field
  (hivatkozas, paragrafus …) as a link to the decree (a URL, or the
  name of a document published with the map);
* restrictions: layers whose names match the catalogue below (protected
  areas, monuments, archaeological sites, safety zones, ...), each with a
  short explanation and a legal reference.

The legal references are suggestions: the planner checks them for the
settlement (the HÉSZ itself is imported later). Only settings change; the
user reviews them in the Publish window before publishing.

Variant branch hu-hesz only (see docs/BRANCHES.md).
"""

import unicodedata
from typing import Iterable, List, Optional, Tuple

from ..models import CutLineConfig, LayerConfig, PopupField, RestrictionConfig

PRESET_ID = "hu-hesz"
TITLE = "Magyar településrendezési terv (HÉSZ, szab_ov)"

PARCEL_NAMES = ("foldreszlet", "telek", "parcella")
PARCEL_KEYS = ("hrsz", "helyrajzi_szam", "helyr_szam")
PARCEL_FIELDS = (
    ("kozter_nev", "Közterület"),
    ("kivett", "Megnevezés (kivett)"),
    ("muv_ag", "Művelési ág"),
    ("fekves", "Fekvés"),
)
ZONE_CODE_FIELDS = ("szab_ov",)
ZONE_FIELDS = (
    ("p_beepmod", "Beépítési mód"),
    ("p_beepszaz", "Legnagyobb beépítettség (%)"),
    ("p_beepmag", "Legnagyobb épületmagasság (m)"),
    ("p_terulet", "Legkisebb telekterület (m²)"),
    ("p_zold", "Legkisebb zöldfelület (%)"),
    ("p_szabkieg", "Kiegészítő előírás"),
)
REGULATION_NAMES = ("eloiras", "hesz", "szabalyzat", "ovezeti")
REGULATION_CODE_FIELDS = ("szab_ov", "ovezet", "ovezet_jel", "ovezetjel", "jel", "kod")
REGULATION_LINK_FIELDS = ("hivatkozas", "paragrafus", "szakasz", "hesz_hiv", "rendelet", "link", "url")
REGULATION_TITLES = {
    "beep_mod": "Beépítési mód", "beepmod": "Beépítési mód",
    "beep_szaz": "Legnagyobb beépítettség (%)", "beepszaz": "Legnagyobb beépítettség (%)",
    "max_mag": "Legnagyobb épületmagasság (m)", "epmag": "Legnagyobb épületmagasság (m)",
    "beep_mag": "Legnagyobb épületmagasság (m)", "min_ter": "Legkisebb telekterület (m²)",
    "telekter": "Legkisebb telekterület (m²)", "min_zold": "Legkisebb zöldfelület (%)",
    "zold": "Legkisebb zöldfelület (%)", "hivatkozas": "HÉSZ hivatkozás", "paragrafus": "HÉSZ hivatkozás",
    "szakasz": "HÉSZ hivatkozás", "megnevezes": "Övezet megnevezése", "nev": "Övezet megnevezése",
    # The columns of the table made with docs/hu/HESZ_ELOIRAS_PROMPT.md.
    "min_szel": "Legkisebb telekszélesség (m)", "min_mely": "Legkisebb telekmélység (m)",
    "terepalatti": "Terepszint alatti beépítés legnagyobb mértéke (%)",
    "szintter": "Legnagyobb szintterületi mutató (m²/m²)", "min_mag": "Legkisebb épületmagasság (m)",
    "max_epitm_mag": "Legnagyobb építménymagasság (m)", "elokert": "Előkert (m)",
    "oldalkert": "Oldalkert (m)", "hatsokert": "Hátsókert (m)", "kozmu": "Közművesítettség",
    "rendeltetes": "Elhelyezhető rendeltetések", "tiltott": "Nem helyezhető el",
    "egyeb": "Egyéb előírás", "rendelet": "Rendelet", "link": "Rendelet", "url": "Rendelet",
}
CUT_LINES = (
    ("szabalyozasi vonal", "Szabályozási vonal"),
    ("ovezethatar", "Övezethatár"),
)
CUT_LINE_FIELDS = ("lszerk_ov", "rszerk_ov")   # zone codes on both sides of a line
NAME_FIELDS = ("nev", "name", "megnevezes", "vedettorokerteknev", "azon", "tipus", "kategoria")

# (name keywords, note, reference, buffer in metres for line/point layers).
# The first matching entry wins, so specific keywords come first.
# References are suggestions to be checked for the settlement.
CATALOGUE: Tuple[Tuple[Tuple[str, ...], str, str, float], ...] = (
    (("muemleki kornyezet",),
     "Műemléki környezet: az építési munkákhoz az örökségvédelmi hatóság közreműködése szükséges.",
     "2001. évi LXIV. törvény (Kötv.); 68/2018. (IV. 9.) Korm. rendelet", 0.0),
    (("muemlek",),
     "Műemlék vagy műemléki érték: a beavatkozás örökségvédelmi engedélyhez kötött.",
     "2001. évi LXIV. törvény (Kötv.)", 0.0),
    (("regeszeti",),
     "Régészeti lelőhely: földmunkával járó beavatkozásnál régészeti feladatellátás szükséges lehet.",
     "2001. évi LXIV. törvény (Kötv.); 68/2018. (IV. 9.) Korm. rendelet", 0.0),
    (("natura 2000",),
     "Natura 2000 terület: a terv vagy beruházás hatásbecsléshez kötött lehet.",
     "275/2004. (X. 8.) Korm. rendelet", 0.0),
    (("ex lege", "lap", "barlang"),
     "Ex lege vagy országos jelentőségű védett természeti terület (láp, barlang felszíni védőövezete).",
     "1996. évi LIII. törvény (Tvt.)", 0.0),
    (("vedett termeszeti", "termeszetvedelmi"),
     "Védett természeti terület: a természetvédelmi hatóság engedélye szükséges lehet.",
     "1996. évi LIII. törvény (Tvt.)", 0.0),
    (("okologiai halozat",),
     "Országos ökológiai hálózat övezete: a területrendezési előírások szerint korlátozott beépítés.",
     "2018. évi CXXXIX. törvény (MaTrT)", 0.0),
    (("tajkepvedelmi",),
     "Tájképvédelmi terület övezete: a tájkép védelmét szolgáló előírások vonatkoznak rá.",
     "2018. évi CXXXIX. törvény (MaTrT); 9/2019. (VI. 14.) MvM rendelet", 0.0),
    (("egyedi tajertek",),
     "Egyedi tájérték: megőrzéséről gondoskodni kell.",
     "1996. évi LIII. törvény (Tvt.)", 10.0),
    (("helyi vedett", "helyi vedelem"),
     "Helyi védelem alatt álló érték: a településképi rendelet előírásai vonatkoznak rá.",
     "2016. évi LXXIV. törvény (településkép védelméről); településképi rendelet", 0.0),
    (("vizbazis", "vedoidom", "vizmukut vedo"),
     "Vízbázis védőterülete: a védőterületen tiltott és korlátozott tevékenységek vannak.",
     "123/1997. (VII. 18.) Korm. rendelet", 0.0),
    (("vizminoseg",),
     "Vízminőség-védelmi terület övezete.",
     "2018. évi CXXXIX. törvény (MaTrT); 9/2019. (VI. 14.) MvM rendelet", 0.0),
    (("nyersanyag",),
     "Ásványi nyersanyagvagyon övezete: a bányászati érdekeket figyelembe kell venni.",
     "2018. évi CXXXIX. törvény (MaTrT); 1993. évi XLVIII. törvény (Bt.)", 0.0),
    (("alabanyaszott",),
     "Alábányászott terület: építés előtt geotechnikai vizsgálat szükséges lehet.",
     "1993. évi XLVIII. törvény (Bt.)", 0.0),
    (("felszinmozgas", "csuszamlas", "suvadas"),
     "Felszínmozgással érintett terület: építés előtt geotechnikai vizsgálat szükséges lehet.",
     "", 0.0),
    (("elektromos", "villamos", "kv"),
     "Villamos vezeték biztonsági övezete: építmény elhelyezése az üzemeltető hozzájárulásához kötött.",
     "2007. évi LXXXVI. törvény (VET); 2/2013. (I. 22.) NGM rendelet", 0.0),
    (("foldgaz", "gazvezetek", "szallitovezetek"),
     "Földgázvezeték biztonsági övezete: építmény elhelyezése az üzemeltető hozzájárulásához kötött.",
     "2008. évi XL. törvény (GET)", 0.0),
    (("ut vedotav", "utak vedotav"),
     "Közút védőtávolsága: építmény elhelyezéséhez a közútkezelő hozzájárulása szükséges.",
     "1988. évi I. törvény (Kkt.) 42/A. §", 0.0),
)

# Layers that never are restrictions even if a keyword matches.
NOT_RESTRICTIONS = ("felirat", "szintvonal", "hazszam", "epulet", "alreszlet", "kozigazgatasi",
                    "telepulesek", "belterulet", "kerekpar", "szabalyozas szin", "szabalyozas ovezet")


def plain(text: str) -> str:
    """Lower case without accents and with single spaces: "Övezethatár" -> "ovezethatar"."""
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(text.lower().replace("_", " ").replace("*", " ").split())


def _vector_layers(project) -> List[object]:
    from qgis.core import QgsVectorLayer  # pylint: disable=import-outside-toplevel
    order = [layer.layer() for layer in project.layerTreeRoot().findLayers() if layer.layer()]
    return [layer for layer in order if isinstance(layer, QgsVectorLayer) and layer.isValid()]


def _geometry(layer) -> str:
    from qgis.core import QgsWkbTypes  # pylint: disable=import-outside-toplevel
    return {QgsWkbTypes.PointGeometry: "point", QgsWkbTypes.LineGeometry: "line",
            QgsWkbTypes.PolygonGeometry: "polygon"}.get(layer.geometryType(), "none")


def _field(layer, names: Iterable[str]) -> str:
    """The layer's first field among ``names`` (case-insensitive)."""
    existing = {plain(f.name()).replace(" ", "_"): f.name() for f in layer.fields()}
    for name in names:
        if name in existing:
            return existing[name]
    return ""


def _drawn(layer) -> bool:
    renderer = layer.renderer()
    return renderer is not None and renderer.type() != "nullSymbol"


def _find_parcels(layers) -> Optional[object]:
    candidates = [layer for layer in layers if _geometry(layer) == "polygon" and _field(layer, PARCEL_KEYS)]
    for keyword in PARCEL_NAMES:
        for layer in candidates:
            if keyword in plain(layer.name()):
                return layer
    return None


def _find_zoning(layers, exclude) -> Optional[object]:
    candidates = [layer for layer in layers if layer not in exclude and _geometry(layer) == "polygon"
                  and _field(layer, ZONE_CODE_FIELDS)]
    # The coloured one first: its symbols become the zone graphic of the report.
    candidates.sort(key=lambda layer: (not _drawn(layer), "szin" not in plain(layer.name())))
    return candidates[0] if candidates else None


def _find_cut_lines(layers) -> List[Tuple[object, str]]:
    found = []
    lines = [layer for layer in layers if _geometry(layer) == "line"]
    for keyword, title in CUT_LINES:
        for layer in lines:
            if keyword in plain(layer.name()) and layer not in [item for item, _ in found]:
                found.append((layer, title))
                break
    if not found:  # a line layer carrying the zone codes of both sides
        found = [(layer, layer.name()) for layer in lines if all(_field(layer, (name,)) for name in CUT_LINE_FIELDS)]
    return found


def _find_regulations(layers, exclude) -> Optional[object]:
    """The zone regulation table: named like REGULATION_NAMES, with a zone code
    field and at least two other fields (with or without geometry)."""
    for layer in layers:
        if layer in exclude or not any(word in plain(layer.name()) for word in REGULATION_NAMES):
            continue
        code = _field(layer, REGULATION_CODE_FIELDS)
        others = [f for f in layer.fields() if f.name() != code and plain(f.name()) not in ("fid", "id", "ogc fid")]
        if code and len(others) >= 2:
            return layer
    return None


def _regulation_fields(layer, code: str) -> List[PopupField]:
    """Every other field of the table, titled by its QGIS alias, else a
    Hungarian title where known; the reference field as a link (URL values)
    or text (a document name links too)."""
    out = []
    for field in layer.fields():
        name = field.name()
        key = plain(name).replace(" ", "_")
        if name == code or key in ("fid", "id", "ogc_fid"):
            continue
        kind = "number" if field.isNumeric() else "string"
        if key in REGULATION_LINK_FIELDS:
            values = [str(v) for v in layer.uniqueValues(layer.fields().indexOf(name), 20) if v]
            kind = "url" if values and all(v.strip().lower().startswith("https://") or
                                           v.strip().lower().startswith("http://") for v in values) else "string"
        out.append(PopupField(name, field.alias() or REGULATION_TITLES.get(key, name), kind))
    return out


def _catalogue_entry(name: str):
    text = plain(name)
    if any(word in text for word in NOT_RESTRICTIONS):
        return None
    for keywords, note, reference, buffer_m in CATALOGUE:
        for keyword in keywords:
            # "kv" and "lap" only as whole words ("132 kV-os", "Ex lege védett láp").
            if (keyword in text.replace("-", " ").split()) if len(keyword) <= 3 else (keyword in text):
                return note, reference, buffer_m
    return None


def _fields(layer, pairs) -> List[PopupField]:
    out = []
    for name, title in pairs:
        actual = _field(layer, (name,))
        if actual:
            out.append(PopupField(actual, title, _type(layer, actual)))
    return out


def _type(layer, name: str) -> str:
    field = layer.fields().field(name)
    return "number" if field.isNumeric() else "string"


def _ensure_published(profile, layer, notes: List[str], why: str):
    config = profile.layer(layer.id())
    if config is None:
        profile.layers.append(LayerConfig(layer_id=layer.id()))
        notes.append(f"A(z) „{layer.name()}” réteg bekerült a publikálandók közé ({why}).")
    elif not config.included:
        config.included = True
        notes.append(f"A(z) „{layer.name()}” réteg publikálása bekapcsolva ({why}).")


def apply(project, profile) -> List[str]:
    """Fill ``profile`` (in place) for a Hungarian zoning plan; return notes in Hungarian."""
    notes: List[str] = []
    layers = _vector_layers(project)
    info = profile.parcel_info
    profile.locale = "hu"

    parcels = _find_parcels(layers)
    if parcels is None:
        notes.append("Nem található földrészlet réteg (hrsz mezővel): a telekinformáció kikapcsolva marad.")
        return notes
    zoning = _find_zoning(layers, [parcels])
    if zoning is None:
        notes.append("Nem található szab_ov mezős övezeti réteg: a telekinformáció kikapcsolva marad.")
        return notes

    info.enabled = True
    info.parcel_layer_id = parcels.id()
    info.key_field = _field(parcels, PARCEL_KEYS)
    info.fields = _fields(parcels, PARCEL_FIELDS)
    info.zoning_layer_id = zoning.id()
    info.zoning_code_field = _field(zoning, ZONE_CODE_FIELDS)
    info.zoning_fields = _fields(zoning, ZONE_FIELDS)
    _ensure_published(profile, parcels, notes, "a telekre kattintáshoz")
    # Every parcel number on the map (smaller on narrow / tiny parcels), and
    # the parcel number searchable.
    parcel_config = profile.layer(parcels.id())
    parcel_config.label_always = True
    parcel_config.snap = True  # measurements snap to parcel corners and boundaries
    if not parcel_config.search_fields:
        parcel_config.search_fields = [info.key_field]
    notes.append("Minden helyrajzi szám kiíródik a térképen (keskeny telken kisebb méretben), "
                 "és a helyrajzi számra lehet keresni.")
    notes.append(f"Telkek: „{parcels.name()}”, azonosító: {info.key_field}.")
    notes.append(f"Övezetek: „{zoning.name()}”, övezetkód: {info.zoning_code_field}; "
                 f"{len(info.zoning_fields)} övezeti érték.")

    # Zone regulations (HÉSZ table): in the parcel report and the zone's popup.
    regulations = _find_regulations(layers, [parcels, zoning])
    if regulations is not None:
        info.regulation_layer_id = regulations.id()
        info.regulation_code_field = _field(regulations, REGULATION_CODE_FIELDS)
        info.regulation_fields = _regulation_fields(regulations, info.regulation_code_field)
        notes.append(f"Övezeti előírások: „{regulations.name()}” ({info.regulation_code_field} szerint), "
                     f"{len(info.regulation_fields)} mező; az övezetre kattintva is megjelennek.")
        zoning_config = profile.layer(zoning.id())
        if zoning_config is not None and zoning_config.included and not any(
                p.field == info.zoning_code_field for p in zoning_config.popup_fields):
            # The zone popup needs the code to find its regulations.
            zoning_config.popup_fields.insert(0, PopupField(info.zoning_code_field, "Övezet"))
    else:
        notes.append("Nem található övezeti előírás tábla (pl. „HÉSZ övezeti előírások” szab_ov mezővel): "
                     "az övezeti értékek a szab_ov réteg mezőiből jönnek.")

    cut_lines = _find_cut_lines(layers)
    info.cut_lines = [CutLineConfig(layer.id(), title) for layer, title in cut_lines]
    for layer, _title in cut_lines:  # ... and to the regulation lines / zone boundaries
        _ensure_published(profile, layer, notes, "a mérés illesztéséhez")
        profile.layer(layer.id()).snap = True
    if cut_lines:
        notes.append("Telekrészeket vágó vonalak: " + ", ".join(f"„{layer.name()}”" for layer, _ in cut_lines) + ".")
    else:
        notes.append("Nem található szabályozási vonal / övezethatár réteg: a telekrészeket csak az övezetek vágják.")

    used = {parcels.id(), zoning.id()} | {layer.id() for layer, _ in cut_lines}
    existing = {item.layer_id: item for item in info.restrictions}
    restrictions = []
    for layer in layers:
        if layer.id() in used:
            continue
        entry = _catalogue_entry(layer.name())
        if entry is None:
            continue
        note, reference, buffer_m = entry
        previous = existing.get(layer.id())
        if previous is not None:  # keep what the user already wrote
            restrictions.append(previous)
            continue
        restrictions.append(RestrictionConfig(
            layer_id=layer.id(), title=layer.name().replace("*", " ").replace("  ", " "),
            note=note, reference=reference, name_field=_field(layer, NAME_FIELDS),
            buffer_m=buffer_m if _geometry(layer) in ("line", "point") else 0.0))
    info.restrictions = restrictions
    if restrictions:
        notes.append(f"{len(restrictions)} korlátozó réteg: "
                     + ", ".join(f"„{project.mapLayer(item.layer_id).name()}”" for item in restrictions
                                 if project.mapLayer(item.layer_id)) + ".")
    notes.append("A jogszabályi hivatkozások javaslatok: a település tervéhez ellenőrizze őket "
                 "(Publish ablak → Telekinformáció).")
    if not info.disclaimer:
        info.disclaimer = ("Tájékoztató jellegű adat, nem minősül hatósági bizonyítványnak vagy "
                           "településrendezési tervből kiadott hiteles másolatnak. Az előírásokat a "
                           "hatályos HÉSZ és szabályozási terv tartalmazza.")
    return notes
