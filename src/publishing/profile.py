"""
Publication profiles: load (with migration), validate, serialize.

A profile is plain JSON (camelCase keys, ``schemaVersion``). It never holds
secrets; any key that looks like one is rejected instead of being stored.
"""

import hashlib
import json
import re
from typing import List, Optional
from urllib.parse import urlparse

from .errors import PublishingError
from .models import (CutLineConfig, ParcelInfoConfig, RestrictionConfig,
                     ARCHIVE_FORMATS, BASEMAP_FLAVORS, BASEMAP_KINDS, DESTINATION_KINDS,
                     FIELD_TYPES, FILTER_KINDS, LOCALES, PROFILE_SCHEMA_VERSION, RASTER_FORMATS,
                     SECRET_KEYS, SLUG, Approval, BasemapConfig, DestinationConfig, FilterField,
                     GroupConfig, InteractionConfig, LayerConfig, OutputConfig, PopupField,
                     PublicationProfile, ThemeConfig, ViewConfig, build, snake)

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _find_secrets(value, path: str = "") -> List[str]:
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            here = f"{path}.{key}" if path else key
            if SECRET_KEYS.search(key) and key not in ("credentialRef", "credential_ref"):
                found.append(here)
            found.extend(_find_secrets(item, here))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_secrets(item, f"{path}[{index}]"))
    return found


def migrate(data: dict) -> dict:
    """Bring an older profile document to the current schema version."""
    version = data.get("schemaVersion", data.get("schema_version"))
    if version is None:
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID", "The profile has no schemaVersion.")
    if not isinstance(version, int) or version < 1:
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID", f"Bad schemaVersion {version!r}.")
    if version > PROFILE_SCHEMA_VERSION:
        raise PublishingError(
            "Q2VT_PUB_SCHEMA_UNSUPPORTED",
            f"This profile was saved by a newer plugin (schema {version}); "
            f"this version reads schema {PROFILE_SCHEMA_VERSION}. Update the plugin.")
    return data  # version 1 is current


def load_profile(data) -> PublicationProfile:
    """Profile from a JSON string or dict; raises PublishingError listing
    every problem."""
    if isinstance(data, (str, bytes)):
        try:
            data = json.loads(data)
        except ValueError as error:
            raise PublishingError("Q2VT_PUB_PROFILE_INVALID", f"Not JSON: {error}") from error
    if not isinstance(data, dict):
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID", "A profile is a JSON object.")
    secrets = _find_secrets(data)
    if secrets:
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID",
                              "Profiles never store secrets; remove: " + ", ".join(secrets[:5]))
    data = snake(migrate(data))
    errors: List[str] = []
    nested = {
        "view": ViewConfig, "interaction": InteractionConfig, "output": OutputConfig,
        "destination": DestinationConfig, "approval": Approval, "themes": ThemeConfig,
        "basemap": BasemapConfig,
    }
    top = {k: v for k, v in data.items() if k not in nested and k not in ("layers", "groups", "parcel_info")}
    profile = build(PublicationProfile, top, errors, "profile")
    for key, cls in nested.items():
        setattr(profile, key, build(cls, data.get(key), errors, key))
    layers = []
    for index, raw in enumerate(data.get("layers") or []):
        where = f"layers[{index}]"
        raw = dict(raw) if isinstance(raw, dict) else {}
        popup = [build(PopupField, item, errors, f"{where}.popupFields")
                 for item in raw.pop("popup_fields", []) or []]
        filters = [build(FilterField, item, errors, f"{where}.filterFields")
                   for item in raw.pop("filter_fields", []) or []]
        layer = build(LayerConfig, raw, errors, where)
        layer.popup_fields, layer.filter_fields = popup, filters
        layers.append(layer)
    profile.layers = layers
    profile.groups = [build(GroupConfig, raw, errors, f"groups[{index}]")
                      for index, raw in enumerate(data.get("groups") or [])]
    profile.parcel_info = _parcel_info(data.get("parcel_info"), errors)
    errors.extend(validate(profile))
    if errors:
        raise PublishingError("Q2VT_PUB_PROFILE_INVALID", "; ".join(errors[:12]),
                              detail="\n".join(errors))
    return profile


def _parcel_info(raw, errors: List[str]) -> ParcelInfoConfig:
    raw = dict(raw) if isinstance(raw, dict) else {}
    lists = {key: raw.pop(key, []) or [] for key in
             ("fields", "zoning_fields", "regulation_fields", "cut_lines", "restrictions")}
    config = build(ParcelInfoConfig, raw, errors, "parcelInfo")
    for key in ("fields", "zoning_fields", "regulation_fields"):
        setattr(config, key, [build(PopupField, item, errors, f"parcelInfo.{key}") for item in lists[key]])
    config.cut_lines = [build(CutLineConfig, item, errors, "parcelInfo.cutLines") for item in lists["cut_lines"]]
    config.restrictions = [build(RestrictionConfig, item, errors, "parcelInfo.restrictions")
                           for item in lists["restrictions"]]
    return config


def validate(profile: PublicationProfile) -> List[str]:
    """Problems of an in-memory profile (empty list: valid)."""
    errors = []
    if profile.schema_version != PROFILE_SCHEMA_VERSION:
        errors.append("schemaVersion: unsupported")
    for key in ("profile_id", "publication_id"):
        if not _UUID.match(str(getattr(profile, key))):
            errors.append(f"{key}: not a UUID")
    if not profile.title.strip():
        errors.append("title: required")
    if not SLUG.match(profile.slug or ""):
        errors.append("slug: lowercase ASCII letters, digits and '-', 1-64 characters")
    if profile.locale not in LOCALES:
        errors.append(f"locale: one of {', '.join(LOCALES)}")
    if profile.vector_only is not True:
        errors.append("vectorOnly: publications are vector-only (MVT); it cannot be turned off")
    seen = set()
    for index, layer in enumerate(profile.layers):
        where = f"layers[{index}]"
        if not layer.layer_id:
            errors.append(f"{where}.layerId: required")
        if layer.layer_id in seen:
            errors.append(f"{where}.layerId: duplicate {layer.layer_id}")
        seen.add(layer.layer_id)
        if not 0.0 <= float(layer.opacity) <= 1.0:
            errors.append(f"{where}.opacity: between 0 and 1")
        if layer.initially_visible and not layer.included:
            errors.append(f"{where}: an excluded layer cannot be initially visible")
        if layer.included and not layer.toggleable and not layer.initially_visible:
            errors.append(f"{where}: a layer that cannot be switched off must be visible at start")
        if layer.raster_format not in RASTER_FORMATS:
            errors.append(f"{where}.rasterFormat: one of {RASTER_FORMATS}")
        for key in ("raster_min_zoom", "raster_max_zoom"):
            value = getattr(layer, key)
            if value is not None and (not isinstance(value, int) or not 0 <= value <= 22):
                errors.append(f"{where}.{key}: 0-22 or empty")
        if layer.raster_min_zoom is not None and layer.raster_max_zoom is not None \
                and layer.raster_min_zoom > layer.raster_max_zoom:
            errors.append(f"{where}: rasterMinZoom > rasterMaxZoom")
        if not 1 <= int(layer.raster_quality) <= 100:
            errors.append(f"{where}.rasterQuality: 1-100")
        for popup in layer.popup_fields:
            if popup.type not in FIELD_TYPES:
                errors.append(f"{where}.popupFields: type '{popup.type}' not in {FIELD_TYPES}")
            if not popup.field:
                errors.append(f"{where}.popupFields: field required")
        for flt in layer.filter_fields:
            if flt.kind not in FILTER_KINDS:
                errors.append(f"{where}.filterFields: kind '{flt.kind}' not in {FILTER_KINDS}")
        if any(not isinstance(name, str) or not name for name in
               layer.search_fields + layer.key_fields):
            errors.append(f"{where}: field names must be non-empty strings")
    paths = set()
    for index, group in enumerate(profile.groups):
        where = f"groups[{index}]"
        if not group.path or not all(isinstance(p, str) and p for p in group.path):
            errors.append(f"{where}.path: a list of group names")
        elif tuple(group.path) in paths:
            errors.append(f"{where}.path: duplicate {'/'.join(group.path)}")
        paths.add(tuple(group.path or ()))
        if not group.toggleable and not group.initially_visible:
            errors.append(f"{where}: a group that cannot be switched off must be visible at start")
    themes = profile.themes
    if any(not isinstance(name, str) or not name for name in themes.names) \
            or len(set(themes.names)) != len(themes.names):
        errors.append("themes.names: distinct theme names")
    if themes.initial and themes.initial not in themes.names:
        errors.append("themes.initial: one of the published themes")
    basemap = profile.basemap
    if basemap.kind not in BASEMAP_KINDS:
        errors.append(f"basemap.kind: one of {BASEMAP_KINDS}")
    if basemap.kind != "none":
        if not basemap.flavors or any(f not in BASEMAP_FLAVORS for f in basemap.flavors) \
                or len(set(basemap.flavors)) != len(basemap.flavors):
            errors.append(f"basemap.flavors: distinct values of {BASEMAP_FLAVORS}")
        if basemap.initial != "none" and basemap.initial not in basemap.flavors:
            errors.append("basemap.initial: one of the flavors or 'none'")
        if not 0 <= basemap.overview_zoom <= basemap.max_zoom <= 15:
            errors.append("basemap: 0 <= overviewZoom <= maxZoom <= 15")
        if not 0 <= float(basemap.padding) <= 10 or not 0 <= float(basemap.overview_km) <= 5000:
            errors.append("basemap: padding 0-10, overviewKm 0-5000")
        source = basemap.source or ""
        if source.startswith(("http://", "https://")):
            parsed = urlparse(source)
            loopback = parsed.hostname in ("127.0.0.1", "localhost", "::1")
            if parsed.scheme != "https" and not loopback:
                errors.append("basemap.source: https:// URL (http only on this computer) or a local file")
    info = profile.parcel_info
    if info.enabled:
        for key in ("parcel_layer_id", "key_field", "zoning_layer_id", "zoning_code_field"):
            if not getattr(info, key):
                errors.append(f"parcelInfo.{key}: required when the parcel report is on")
        if not 0 <= float(info.min_area) <= 1000 or not 0 <= float(info.min_share) <= 50:
            errors.append("parcelInfo: minArea 0-1000 m², minShare 0-50 %")
        seen_ids = set()
        for item in info.restrictions:
            if not item.layer_id or item.layer_id in seen_ids:
                errors.append("parcelInfo.restrictions: a layer id once per layer")
            seen_ids.add(item.layer_id)
            if not 0 <= float(item.buffer_m) <= 1000:
                errors.append("parcelInfo.restrictions.bufferM: 0-1000 m")
        if bool(info.regulation_layer_id) != bool(info.regulation_code_field):
            errors.append("Parcel report → Zone regulations table: choose the table's zone code field "
                          "(the field with the same zone codes as the zone layer, e.g. szab_ov), or set "
                          "Table to empty if you do not use a regulations table (it is optional)")
    if not re.match(r"^#[0-9a-fA-F]{6}$", profile.accent_color or ""):
        errors.append("accentColor: #rrggbb")
    view = profile.view
    if not 0 <= view.min_zoom <= view.max_zoom <= 22:
        errors.append("view: 0 <= minZoom <= maxZoom <= 22")
    if not view.max_zoom <= view.max_view_zoom <= 24:
        errors.append("view.maxViewZoom: between maxZoom and 24")
    if view.extent is not None and (len(view.extent) != 4 or view.extent[0] >= view.extent[2]
                                    or view.extent[1] >= view.extent[3]):
        errors.append("view.extent: [xmin, ymin, xmax, ymax] with xmin < xmax, ymin < ymax")
    if profile.output.archive not in ARCHIVE_FORMATS:
        errors.append(f"output.archive: one of {ARCHIVE_FORMATS}")
    if not 0 <= profile.output.cpu_percent <= 100:
        errors.append("output.cpuPercent: 0-100")
    dest = profile.destination
    if dest.kind not in DESTINATION_KINDS:
        errors.append(f"destination.kind: one of {DESTINATION_KINDS}")
    if dest.kind in ("r2", "s3"):
        if profile.output.archive == "mbtiles":
            errors.append("output.archive: web hosting needs PMTiles (choose PMTiles or Both)")
        if not dest.bucket:
            errors.append("destination.bucket: required")
        if dest.kind == "r2" and not (dest.account_id or dest.endpoint):
            errors.append("destination.accountId: required for R2 (or an explicit endpoint)")
        if dest.kind == "s3" and not dest.endpoint:
            errors.append("destination.endpoint: required")
        for key in ("endpoint", "public_base_url"):
            value = getattr(dest, key)
            parsed = urlparse(value) if value else None
            loopback = parsed is not None and parsed.hostname in ("127.0.0.1", "localhost", "::1")
            if value and parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
                errors.append(f"destination.{key}: must be an https:// URL "
                              "(plain http only for local test servers on this computer)")
        if not dest.public_base_url:
            errors.append("destination.publicBaseUrl: required (the public custom domain)")
    if dest.prefix:
        try:
            normalize_prefix(dest.prefix)
        except PublishingError as error:
            errors.append(f"destination.prefix: {error.message}")
    if not 1 <= int(dest.retention) <= 100:
        errors.append("destination.retention: 1-100")
    return errors


def normalize_prefix(prefix: str) -> str:
    """``maps/my-map`` (no leading/trailing slash, safe ASCII segments)."""
    parts = [p for p in (prefix or "").strip().strip("/").split("/") if p]
    for part in parts:
        if part in (".", "..") or not re.match(r"^[A-Za-z0-9._-]+$", part):
            raise PublishingError("Q2VT_PUB_PATH_UNSAFE",
                                  f"Prefix segment '{part}' must be ASCII letters, digits, '.', '_' or '-'.")
    return "/".join(parts)


def publication_prefix(profile: PublicationProfile) -> str:
    """The object-key prefix this publication owns: the configured prefix,
    or ``maps/<slug>``."""
    if profile.destination.prefix:
        return normalize_prefix(profile.destination.prefix)
    return f"maps/{profile.slug}"


def dumps(profile: PublicationProfile) -> str:
    """Stable JSON (sorted keys) for saving in the project / sidecar."""
    return json.dumps(profile.to_dict(), ensure_ascii=False, sort_keys=True, indent=1)


def disclosure_fingerprint(profile: PublicationProfile, extra: Optional[dict] = None) -> str:
    """Hash of everything that decides *what becomes public*: included
    layers, exposed fields, search/filter/key fields, destination URL. A
    change requires a new review/approval before publishing."""
    payload = {
        "publication": profile.publication_id,
        "layers": sorted(
            [layer.layer_id, sorted(p.field for p in layer.popup_fields),
             sorted(layer.search_fields), sorted(layer.key_fields),
             sorted(f.field for f in layer.filter_fields), layer.display_expression,
             layer.deep_links]
            for layer in profile.layers if layer.included),
        "allFields": profile.output.include_all_fields,
        "parcelInfo": profile.to_dict()["parcelInfo"] if profile.parcel_info.enabled else None,
        "destination": [profile.destination.kind, profile.destination.public_base_url,
                        profile.destination.bucket, publication_prefix(profile)],
        "extra": extra or {},
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def needs_review(profile: PublicationProfile, extra: Optional[dict] = None) -> bool:
    return profile.approval.fingerprint != disclosure_fingerprint(profile, extra)
