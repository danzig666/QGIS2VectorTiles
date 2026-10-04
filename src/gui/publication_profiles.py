"""
Publication profiles stored in the QGIS project (non-secret settings only).

Every setting of the Publish Web Map window is saved in the project file
(project custom properties, scope ``QGIS2VectorTilesFork``) so it travels
with the .qgz/.qgs. Secrets never go here: the destination keeps the id of
a QGIS authentication configuration (encrypted in QGIS's auth database).

Several profiles per project are supported; one is active. Each stored
profile remembers the project file it was saved from, so a copied project
can ask whether to update the same publication or start a new one.
"""

import json
import uuid
from typing import List, Optional, Tuple

from qgis.core import QgsProject

from ..publishing.models import PublicationProfile
from ..publishing.profile import dumps, load_profile

SCOPE = "QGIS2VectorTilesFork"  # the plugin's pre-rename name: kept so saved projects load
KEY_PROFILES = "publication_profiles"
KEY_ACTIVE = "active_publication_profile"


def _read(project: QgsProject) -> List[dict]:
    text, ok = project.readEntry(SCOPE, KEY_PROFILES, "")
    if not ok or not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    return [entry for entry in data if isinstance(entry, dict) and "profile" in entry] \
        if isinstance(data, list) else []


def list_profiles(project: QgsProject) -> List[Tuple[PublicationProfile, str]]:
    """[(profile, saved-from project file)] — invalid entries are skipped."""
    result = []
    for entry in _read(project):
        try:
            result.append((load_profile(entry["profile"]), entry.get("projectFile", "")))
        except Exception:  # noqa: BLE001 - an unreadable profile is ignored, not fatal
            continue
    return result


def active_profile(project: QgsProject) -> Optional[Tuple[PublicationProfile, str]]:
    profiles = list_profiles(project)
    if not profiles:
        return None
    active, _ = project.readEntry(SCOPE, KEY_ACTIVE, "")
    for profile, path in profiles:
        if profile.profile_id == active:
            return profile, path
    return profiles[0]


def save_profile(project: QgsProject, profile: PublicationProfile) -> None:
    """Insert or replace ``profile`` and make it active (marks the project
    as modified; the user saves the project to keep it)."""
    entries = [e for e in _read(project)
               if (e.get("profile") or {}).get("profileId") != profile.profile_id]
    entries.append({"profile": json.loads(dumps(profile)), "projectFile": project.fileName()})
    project.writeEntry(SCOPE, KEY_PROFILES, json.dumps(entries, ensure_ascii=False))
    project.writeEntry(SCOPE, KEY_ACTIVE, profile.profile_id)


def delete_profile(project: QgsProject, profile_id: str) -> None:
    entries = [e for e in _read(project) if (e.get("profile") or {}).get("profileId") != profile_id]
    project.writeEntry(SCOPE, KEY_PROFILES, json.dumps(entries, ensure_ascii=False))


def as_new_publication(profile: PublicationProfile) -> PublicationProfile:
    """A copy that publishes a *different* map (new ids, approval reset)."""
    copy = load_profile(dumps(profile))
    copy.profile_id = str(uuid.uuid4())
    copy.publication_id = str(uuid.uuid4())
    copy.approval.fingerprint = ""
    copy.approval.approved_at = ""
    return copy


def dumps_profile(profile: PublicationProfile) -> str:
    """Stable JSON of a profile (to compare settings between runs)."""
    return dumps(profile)


# ---------------------------------------------------------------- settings files
FILE_FORMAT = "q2vtPublicationSettings"


def export_document(profile: PublicationProfile, project: QgsProject, credentials=None) -> str:
    """The settings as a file: the profile plus the names of the layers it
    refers to (layer ids differ between projects; names let another project
    take the settings over). The profile never holds secrets; the object
    storage keys (``credentials``: access key id, secret) are added only when
    given, in plain text, at the owner's request - keep such a file private."""
    names = {layer_id: layer.name() for layer_id, layer in project.mapLayers().items()}
    data = json.loads(dumps(profile))
    used = {value for value in _strings(data) if value in names}
    document = {FILE_FORMAT: 1, "profile": data,
                "layerNames": {layer_id: names[layer_id] for layer_id in sorted(used)}}
    if credentials is not None and credentials.access_key_id and credentials.secret_access_key:
        document["credentials"] = {"accessKeyId": credentials.access_key_id,
                                   "secretAccessKey": credentials.secret_access_key}
        if getattr(credentials, "session_token", ""):
            document["credentials"]["sessionToken"] = credentials.session_token
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1)


def document_credentials(text: str):
    """The object storage keys of a settings file (``Credentials``), or None."""
    from ..publishing.providers.base import Credentials  # pylint: disable=import-outside-toplevel
    try:
        data = json.loads(text)
    except ValueError:
        return None
    found = data.get("credentials") if isinstance(data, dict) else None
    if not isinstance(found, dict):
        return None
    key, secret = str(found.get("accessKeyId") or "").strip(), str(found.get("secretAccessKey") or "")
    if not key or not secret:
        return None
    return Credentials(key, secret, str(found.get("sessionToken") or ""))


def import_document(text: str, project: QgsProject) -> Tuple[PublicationProfile, List[str]]:
    """Profile from a settings file (or a bare profile JSON), its layers
    matched to this project: by id, else by a unique layer name. Layers not
    found are dropped. Returns (profile, notes for the user)."""
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("not a settings file")
    names = (data.get("layerNames") or {}) if FILE_FORMAT in data else {}
    raw = data["profile"] if FILE_FORMAT in data else data
    here = project.mapLayers()
    by_name = {}
    for layer_id, layer in here.items():
        by_name.setdefault(layer.name(), []).append(layer_id)
    mapping, missing = {}, []
    for old_id, name in names.items():
        if old_id in here:
            continue
        candidates = by_name.get(name, [])
        if len(candidates) == 1:
            mapping[old_id] = candidates[0]
        else:
            missing.append(name)
    profile = load_profile(json.dumps(_replace(raw, mapping)))
    notes = []
    if mapping:
        notes.append(f"{len(mapping)} layer(s) matched by name.")
    kept = [config for config in profile.layers if config.layer_id in here]
    dropped = len(profile.layers) - len(kept)
    profile.layers = kept
    if profile.view.extent_layer and profile.view.extent_layer not in here:
        profile.view.extent_layer = ""
    if dropped or missing:
        notes.append(f"{max(dropped, len(missing))} layer(s) not found in this project and left out"
                     + (": " + ", ".join(sorted(set(missing))[:8]) if missing else "") + ".")
    return profile, notes


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _replace(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, dict):
        return {key: _replace(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [_replace(item, mapping) for item in value]
    return value
