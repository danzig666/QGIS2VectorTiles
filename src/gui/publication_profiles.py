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

SCOPE = "QGIS2VectorTilesFork"
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
