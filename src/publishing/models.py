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


@dataclass
class ViewConfig:
    extent: Optional[List[float]] = None   # [xmin, ymin, xmax, ymax] EPSG:3857; None = canvas
    min_zoom: int = 0
    max_zoom: int = 16
    max_view_zoom: int = 22               # browser zoom limit (overzoom)


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
    warnings: List[str] = field(default_factory=list)


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
