"""
QGIS's own XYZ tile connections (Browser panel → XYZ Tiles): the Publish
window offers them as web basemaps and saves its web basemaps there too, so
both lists stay the same. Stored under ``connections/xyz/items/<name>/``
(url, zmin, zmax); the attribution, which QGIS does not keep, under
``q2vt-attribution`` next to them.
"""

from typing import List

from qgis.core import QgsSettings

from ..publishing.models import XyzBasemap

ROOT = "connections/xyz/items"


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def qgis_xyz_connections() -> List[XyzBasemap]:
    """The XYZ connections saved in QGIS (name, URL, zooms, attribution)."""
    settings = QgsSettings()
    settings.beginGroup(ROOT)
    names = settings.childGroups()
    settings.endGroup()
    out = []
    for name in sorted(names, key=str.lower):
        base = f"{ROOT}/{name}"
        url = str(settings.value(f"{base}/url", "") or "")
        if not url:
            continue
        out.append(XyzBasemap(title=name, url=url,
                              attribution=str(settings.value(f"{base}/q2vt-attribution", "") or ""),
                              min_zoom=_int(settings.value(f"{base}/zmin", 0), 0),
                              max_zoom=_int(settings.value(f"{base}/zmax", 19), 19)))
    return out


def save_qgis_xyz_connections(entries) -> int:
    """Add or update QGIS XYZ connections from web basemaps (by title);
    returns how many changed. Other connections, and a connection of the same
    name made in QGIS with another address, are left alone."""
    settings = QgsSettings()
    changed = 0
    for entry in entries:
        name = str(entry.title).strip().replace("/", "-")
        if not name or not entry.url or "{bbox-epsg-3857}" in entry.url:  # WMS: not an XYZ connection
            continue
        base = f"{ROOT}/{name}"
        existing = str(settings.value(f"{base}/url", "") or "")
        if existing and existing != entry.url and not settings.contains(f"{base}/q2vt-attribution"):
            continue  # the user's own connection of the same name: never overwritten
        wanted = {"url": entry.url, "zmin": int(entry.min_zoom), "zmax": int(entry.max_zoom),
                  "q2vt-attribution": entry.attribution}
        for key, value in wanted.items():
            if str(settings.value(f"{base}/{key}", "")) != str(value):
                settings.setValue(f"{base}/{key}", value)
                changed += 1
    return changed
