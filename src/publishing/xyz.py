"""
Web basemaps of XYZ tiles: loaded by the visitor's browser from their own
servers while browsing (not part of the release). Addresses use the QGIS /
Leaflet templates: ``{z}``, ``{x}``, ``{y}``; ``{-y}`` for TMS row order;
``{s}`` for the a/b/c servers of a tile service.
"""

from typing import Iterable, List, Set, Tuple
from urllib.parse import urlsplit

SUBDOMAINS = ("a", "b", "c")


def tile_urls(url: str) -> Tuple[List[str], str]:
    """(MapLibre tile URLs, scheme "xyz" | "tms") of a template."""
    scheme = "tms" if "{-y}" in url else "xyz"
    url = url.replace("{-y}", "{y}")
    if "{s}" in url:
        return [url.replace("{s}", s) for s in SUBDOMAINS], scheme
    return [url], scheme


def origins(urls: Iterable[str]) -> Set[str]:
    """https://host[:port] of tile URLs (the page may load from these)."""
    out = set()
    for url in urls:
        parts = urlsplit(url)
        if parts.scheme == "https" and parts.netloc and "{" not in parts.netloc:
            out.add(f"https://{parts.netloc}")
    return out


def manifest_entries(entries) -> List[dict]:
    """The viewer's description of the web basemaps (ids xyz-1, xyz-2, ...)."""
    out = []
    for index, entry in enumerate(entries):
        tiles, scheme = tile_urls(entry.url)
        out.append({"id": f"xyz-{index + 1}", "title": entry.title, "tiles": tiles, "scheme": scheme,
                    "attribution": entry.attribution, "minzoom": int(entry.min_zoom),
                    "maxzoom": int(entry.max_zoom), "tileSize": 256})
    return out
