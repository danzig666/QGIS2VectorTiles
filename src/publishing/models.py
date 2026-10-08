"""
Data contracts of the publishing workflow (pure Python, no QGIS).

* ``PublicationProfile`` — every non-secret setting of a publication (what
  the Publish window shows and saves in the project). Secrets are never
  stored here: the destination holds a credential *reference* (QGIS
  authentication configuration id).
* ``ExportBundle`` — the private result of one local export: absolute paths
  allowed, never serialized into public files.
* ``ReleaseState`` — normalized result states of a publish run.

File boundaries use versioned JSON (schemas/publishing/*-v1.schema.json).
"""

import copy
import enum
import re
import uuid
from dataclasses import asdict, dataclass, field, fields
from typing import Any, Dict, List, Optional, Tuple

PROFILE_SCHEMA_VERSION = 1
ARCHIVE_FORMATS = ("pmtiles", "mbtiles", "both")
DESTINATION_KINDS = ("local", "r2", "s3")
LOCALES = ("en", "hu")
FILTER_KINDS = ("values", "range", "text")
FIELD_TYPES = ("string", "integer", "number", "boolean", "date", "url")
RASTER_FORMATS = ("png", "jpeg", "webp")
# Documents published with the map (never HTML/SVG: they would run in the map's own site).
DOCUMENT_EXTENSIONS = (".pdf", ".docx", ".doc", ".odt", ".rtf", ".txt", ".png", ".jpg", ".jpeg", ".webp")
BASEMAP_KINDS = ("none", "protomaps")
BASEMAP_FLAVORS = ("light", "dark", "white", "grayscale", "black")
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
SECRET_KEYS = re.compile(r"(?i)(secret|password|passwd|token|accesskey|access_key|privatekey)")


class ReleaseState(str, enum.Enum):
    LOCAL_READY = "LOCAL_READY"
    UPLOADED_NOT_ACTIVE = "UPLOADED_NOT_ACTIVE"
    PUBLISHED = "PUBLISHED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    CONFLICT = "CONFLICT"


def slugify(text: str, fallback: str = "map") -> str:
    """ASCII object-key slug of a (possibly Hungarian) title."""
    import unicodedata  # pylint: disable=import-outside-toplevel
    text = unicodedata.normalize("NFKD", (text or "").replace("ß", "ss"))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:64].strip("-")
    return text or fallback


# --- profile parts -----------------------------------------------------------------

@dataclass
class PopupField:
    field: str
    alias: str = ""
    type: str = "string"  # FIELD_TYPES; "url" is rendered as a safe link


@dataclass
class FilterField:
    field: str
    kind: str = "values"  # FILTER_KINDS
    alias: str = ""


@dataclass
class LayerConfig:
    """One QGIS layer of the publication (by QGIS layer id)."""

    layer_id: str
    included: bool = True              # exported (also when initially hidden)
    initially_visible: bool = True     # checked in the viewer at start
    title: str = ""                    # overrides the QGIS layer name
    popup_fields: List[PopupField] = field(default_factory=list)
    search_fields: List[str] = field(default_factory=list)
    key_fields: List[str] = field(default_factory=list)  # stable feature key (unique, not NULL)
    display_expression: str = ""       # search/popup title (QGIS expression, export side)
    filter_fields: List[FilterField] = field(default_factory=list)
    legend: bool = True
    opacity: float = 1.0
    deep_links: bool = True
    # Every feature gets its label in the web map: one that does not fit is
    # drawn smaller, at worst at the roomiest point (never dropped).
    label_always: bool = False
    # The viewer's measurement tools snap to this layer's corners and edges.
    snap: bool = False
    # Polygon layers: a numeric field of heights in metres; the viewer's 3D
    # view raises the polygons (e.g. buildings) to it. Written to the tiles.
    height_field: str = ""
    toggleable: bool = True            # the viewer's user may switch it off
    # Web map only, on top of the layer's own QGIS scale range (QGIS scale
    # denominators, 0 = no limit): hidden when zoomed out beyond 1:min_scale
    # or zoomed in beyond 1:max_scale. Not tiled where hidden.
    min_scale: float = 0.0
    max_scale: float = 0.0
    # Raster layers only (rendered by QGIS into their own raster PMTiles archive):
    raster_format: str = "webp"        # RASTER_FORMATS; webp and png keep transparency
    raster_min_zoom: Optional[int] = None   # None = the publication's tile zooms
    raster_max_zoom: Optional[int] = None
    raster_quality: int = 85           # jpeg / webp
    raster_hidpi: bool = False         # 512 px images (the Publish window no longer sets it)
    # Without a maximum zoom: as sharp as the image (its own resolution sets
    # the zoom); False: the publication's maximum tile zoom.
    raster_match_native: bool = True


@dataclass
class GroupConfig:
    """A QGIS layer-tree group, identified by its path of names."""

    path: List[str] = field(default_factory=list)
    toggleable: bool = True
    initially_visible: bool = True
    expanded: bool = True


@dataclass
class ThemeConfig:
    """QGIS map themes offered as presets ("views") in the web map."""

    names: List[str] = field(default_factory=list)
    initial: str = ""                  # theme applied at start ("" = layer settings)


@dataclass
class XyzBasemap:
    """A web basemap of XYZ tiles, loaded by the visitor's browser from its
    own server while browsing (not copied into the release)."""

    title: str = ""
    url: str = ""                      # https://…/{z}/{x}/{y}.png  ({-y}: TMS rows, {s}: a/b/c servers)
    attribution: str = ""
    min_zoom: int = 0
    max_zoom: int = 19


@dataclass
class BasemapConfig:
    """Optional vector basemap: an OpenStreetMap extract (Protomaps schema)
    bundled into the release as its own PMTiles archive."""

    kind: str = "none"                 # BASEMAP_KINDS
    source: str = ""                   # "" = latest Protomaps daily build; URL or local .pmtiles
    flavors: List[str] = field(default_factory=lambda: ["light"])
    initial: str = "light"             # a flavor, or "none" (switched off at start)
    max_zoom: int = 15                 # detail zoom of the extract (Protomaps data ends at 15)
    padding: float = 0.5               # detail area = extent grown by this fraction per side
    overview_zoom: int = 7             # zooms 0..overview_zoom cover a wide area
    overview_km: float = 300.0         # side of the wide overview area
    # Web basemaps (XYZ tiles) offered next to it; "initial" may name one ("xyz-1", ...).
    xyz: List[XyzBasemap] = field(default_factory=list)

@dataclass
class CutLineConfig:
    """A line layer that cuts parcels into parts (regulation line, zone boundary)."""

    layer_id: str = ""
    title: str = ""


@dataclass
class RestrictionConfig:
    """A layer whose features restrict the parcels they touch."""

    layer_id: str = ""
    title: str = ""
    note: str = ""                     # one-sentence explanation
    reference: str = ""                # legal reference, e.g. "Étv. 23. §"
    name_field: str = ""               # optional feature name shown (approved field)
    buffer_m: float = 0.0              # lines/points: protection distance in metres




@dataclass
class ParcelInfoConfig:
    """Parcel report: clicking a parcel shows its area, its parts cut by the
    zoning (and the regulation / zone boundary lines), its zones, the
    restrictions touching it and the zone regulations. Computed at export
    time in QGIS from exact geometry; only the result becomes public."""

    enabled: bool = False
    parcel_layer_id: str = ""
    key_field: str = ""                # unique parcel id, e.g. hrsz
    fields: List[PopupField] = field(default_factory=list)          # parcel data shown
    zoning_layer_id: str = ""
    zoning_code_field: str = ""        # e.g. szab_ov
    zoning_fields: List[PopupField] = field(default_factory=list)   # zone values per part
    cut_lines: List[CutLineConfig] = field(default_factory=list)
    restrictions: List[RestrictionConfig] = field(default_factory=list)
    regulation_layer_id: str = ""      # optional table joined by zone code (e.g. HÉSZ)
    regulation_code_field: str = ""
    regulation_fields: List[PopupField] = field(default_factory=list)
    min_area: float = 1.0              # m²: smaller slivers are ignored
    min_share: float = 0.5             # %: smaller restriction overlaps are ignored
    disclaimer: str = ""               # "" = the viewer's own notice in its language


@dataclass
class DocumentConfig:
    """A document published with the map (e.g. the decree as PDF): copied
    into the release and listed in the viewer."""

    title: str = ""
    path: str = ""                     # local file (DOCUMENT_EXTENSIONS)


@dataclass
class InfoConfig:
    """What the map shows about itself: who issued it, its legal state and
    the date of its data (separate from the export date), and documents."""

    issuer: str = ""                   # e.g. the municipality
    decree: str = ""                   # e.g. "12/2025. (X. 1.) önkormányzati rendelet"
    legal_date: str = ""               # in force from (free text, e.g. "2025. 10. 01.")
    data_date: str = ""                # data as of (free text)
    documents: List[DocumentConfig] = field(default_factory=list)


@dataclass
class TerrainConfig:
    """Terrain from a DEM raster layer (heights in metres): the 3D view's
    relief, an optional hillshade and elevation profiles in the viewer."""

    layer_id: str = ""                 # "" = no terrain
    hillshade: bool = True             # shaded relief drawn under the map (also in 2D)
    exaggeration: float = 1.5          # vertical exaggeration in 3D


@dataclass
class ViewConfig:
    extent: Optional[List[float]] = None   # [xmin, ymin, xmax, ymax] EPSG:3857; None = canvas
    extent_layer: str = ""                # layer id: the extent follows that layer's extent
    min_zoom: int = 0
    max_zoom: int = 16
    max_view_zoom: int = 22               # browser zoom limit (overzoom)
    # The web map stays on the extent: its centre cannot leave it and it zooms
    # out only a little beyond the whole extent.
    limit_to_extent: bool = True


@dataclass
class InteractionConfig:
    labels_toggle: bool = True
    opacity_controls: bool = True
    search: bool = True
    filters: bool = True
    popups: bool = True
    permalinks: bool = True
    coordinates: bool = True
    measure: bool = False
    print: bool = True
    legend_visible_only: bool = False  # legend lists only what is drawn in the current view
    layers_panel: bool = True          # the Layers tab (off: visitors see the legend only)
    # Named OpenStreetMap streets inside the extent layer in the search (read
    # from the basemap; without a basemap from its source: needs internet).
    street_search: bool = False
    # House numbers in the search ("Fő utca 12"): a point (or polygon) layer
    # with the number; the street from a field or the nearest OSM street.
    address_layer_id: str = ""
    address_number_field: str = ""
    address_street_field: str = ""     # "" = the nearest named OpenStreetMap street
    # Optional viewer extras, all off unless chosen:
    # a small overview map in a corner with the current view marked;
    overview_map: bool = False
    # the 3D button (tilted view, polygons raised by their height field,
    # terrain relief); the hillshade and elevation profiles need terrain only;
    three_d: bool = False
    # drawing (points, lines, areas, text) kept in the shared link, exported
    # as GeoJSON / KML; nothing is stored on the site.
    drawing: bool = False
    # Google Street View in the viewer (a tap on the map opens the panorama
    # looking toward the tapped point). The key is public in the page:
    # restrict it to the site's address (HTTP referrer) in Google Cloud.
    street_view: bool = False
    google_api_key: str = ""


@dataclass
class OutputConfig:
    archive: str = "pmtiles"           # ARCHIVE_FORMATS
    xyz_package: bool = False          # legacy XYZ static package as well
    local_directory: str = ""
    zip: bool = False                  # offline ZIP next to the bundle (not inside it)
    cpu_percent: int = 100
    fidelity_mode: int = 0
    overzoom: int = 0
    polygon_labels_base: int = 2
    include_all_fields: bool = False   # False = required fields only (+ approved fields)
    reuse_unchanged: bool = True       # export cache: layers unchanged since the last export are reused


@dataclass
class DestinationConfig:
    kind: str = "local"                # DESTINATION_KINDS
    account_id: str = ""               # R2 account id (endpoint derived from it)
    endpoint: str = ""                 # S3 API endpoint (not the public URL)
    region: str = "auto"
    bucket: str = ""
    prefix: str = ""                   # publication prefix inside the bucket
    public_base_url: str = ""          # e.g. https://maps.example.com
    credential_ref: str = ""           # QGIS auth configuration id (no secret)
    retention: int = 3                 # releases kept (current + previous)
    conditional_writes: bool = True    # provider supports If-Match/If-None-Match


@dataclass
class Approval:
    """Disclosure review: what the user approved to make public."""

    fingerprint: str = ""
    approved_at: str = ""


@dataclass
class PublicationProfile:
    title: str = "Web map"
    description: str = ""
    slug: str = "map"
    locale: str = "hu"
    attribution: str = ""
    logo_path: str = ""                # local file; copied into the bundle as a UI asset
    profile_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    publication_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    vector_only: bool = True
    layers: List[LayerConfig] = field(default_factory=list)
    groups: List[GroupConfig] = field(default_factory=list)
    themes: ThemeConfig = field(default_factory=ThemeConfig)
    basemap: BasemapConfig = field(default_factory=BasemapConfig)
    accent_color: str = "#2563eb"      # viewer accent colour
    parcel_info: ParcelInfoConfig = field(default_factory=ParcelInfoConfig)
    info: InfoConfig = field(default_factory=InfoConfig)
    terrain: TerrainConfig = field(default_factory=TerrainConfig)
    view: ViewConfig = field(default_factory=ViewConfig)
    interaction: InteractionConfig = field(default_factory=InteractionConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    destination: DestinationConfig = field(default_factory=DestinationConfig)
    approval: Approval = field(default_factory=Approval)
    schema_version: int = PROFILE_SCHEMA_VERSION

    # -- helpers --------------------------------------------------------------
    def layer(self, layer_id: str) -> Optional[LayerConfig]:
        return next((layer for layer in self.layers if layer.layer_id == layer_id), None)

    def included_layer_ids(self) -> List[str]:
        return [layer.layer_id for layer in self.layers if layer.included]

    def group(self, path) -> Optional[GroupConfig]:
        path = list(path)
        return next((group for group in self.groups if list(group.path) == path), None)

    def to_dict(self) -> dict:
        return _camel(asdict(self))

    @classmethod
    def from_dict(cls, data: dict) -> "PublicationProfile":
        from .profile import load_profile  # pylint: disable=import-outside-toplevel
        return load_profile(data)


# --- export bundle -------------------------------------------------------------

@dataclass
class ExportBundle:
    """A validated local export, consumed by packaging. Private: absolute
    paths are fine here and never written to public files."""

    export_dir: str
    mbtiles_path: str
    style_path: str
    style: dict
    source_name: str
    sprite_dir: str = ""
    glyphs_dir: str = ""
    report_path: str = ""              # private fidelity report
    log_path: str = ""                 # private export log
    publication_id: str = ""
    build_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    plugin_version: str = ""
    runtime_versions: Dict[str, str] = field(default_factory=dict)
    bounds_wgs84: Tuple[float, float, float, float] = (-180.0, -85.0511, 180.0, 85.0511)
    view: Dict[str, Any] = field(default_factory=dict)       # center, zoom, minZoom, maxZoom
    tile_zooms: Tuple[int, int] = (0, 0)
    groups: List[dict] = field(default_factory=list)         # logical QGIS groups
    layers: List[dict] = field(default_factory=list)         # logical layers
    rules: List[dict] = field(default_factory=list)          # logical rules
    components: List[dict] = field(default_factory=list)     # style-layer ownership
    legend_dir: str = ""                                     # rendered swatches
    search_records_path: str = ""                            # JSON lines of public records
    feature_count: Dict[str, int] = field(default_factory=dict)
    public_fields: Dict[str, List[str]] = field(default_factory=dict)
    data_fingerprint: str = ""
    config_fingerprint: str = ""
    diagnostics_summary: Dict[str, int] = field(default_factory=dict)
    crs: Optional[Dict[str, Any]] = None                     # project CRS (coordinate readout)
    warnings: List[str] = field(default_factory=list)
    raster_archives: List[dict] = field(default_factory=list)  # {layerId, path, ...} (private paths)
    basemap: Optional[dict] = None                            # extract + style templates (private)
    themes: List[dict] = field(default_factory=list)          # viewer presets


# --- serialization helpers ----------------------------------------------------------

def _camel_key(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def _snake_key(key: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", key).lower()


def _camel(value):
    if isinstance(value, dict):
        return {_camel_key(k): _camel(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_camel(v) for v in value]
    return value


def snake(value):
    if isinstance(value, dict):
        return {_snake_key(k): snake(v) for k, v in value.items()}
    if isinstance(value, list):
        return [snake(v) for v in value]
    return value


def build(cls, data: Optional[dict], errors: List[str], where: str):
    """Instantiate dataclass ``cls`` from snake_case ``data``; unknown keys
    and wrong types are reported in ``errors`` (defaults are kept)."""
    data = data or {}
    if not isinstance(data, dict):
        errors.append(f"{where}: expected an object")
        return cls()
    known = {f.name: f for f in fields(cls)}
    kwargs = {}
    for key, value in data.items():
        if key not in known:
            errors.append(f"{where}.{key}: unknown setting")
            continue
        kwargs[key] = copy.deepcopy(value)
    try:
        return cls(**kwargs)
    except TypeError as error:
        errors.append(f"{where}: {error}")
        return cls()
