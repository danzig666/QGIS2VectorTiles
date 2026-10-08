"""
Web basemaps of XYZ tiles: loaded by the visitor's browser from their own
servers while browsing (not part of the release). Addresses use the QGIS /
Leaflet templates: ``{z}``, ``{x}``, ``{y}``; ``{-y}`` for TMS row order;
``{s}`` for the a/b/c servers of a tile service. A WMS service is a GetMap
template with ``{bbox-epsg-3857}`` (MapLibre requests one image per tile).
"""

from typing import Iterable, List, Set, Tuple
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

WMS_BBOX = "{bbox-epsg-3857}"


def is_wms(url: str) -> bool:
    return WMS_BBOX in (url or "")


def wms_template(service_url: str, layers: str, image_format: str = "image/png",
                 styles: str = "", version: str = "1.3.0") -> str:
    """A WMS GetMap address for 256 px Web Mercator tiles. The service's own
    query parameters (e.g. a map= or key) are kept; the GetMap ones replaced."""
    parts = urlsplit(service_url.strip())
    getmap = {"service", "version", "request", "layers", "styles", "crs", "srs", "bbox", "width",
              "height", "format", "transparent"}
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in getmap]
    crs = "CRS" if version.startswith("1.3") else "SRS"
    query += [("SERVICE", "WMS"), ("VERSION", version), ("REQUEST", "GetMap"), ("LAYERS", layers),
              ("STYLES", styles), (crs, "EPSG:3857"), ("WIDTH", "256"), ("HEIGHT", "256"),
              ("FORMAT", image_format), ("TRANSPARENT", "TRUE" if image_format.endswith("png") else "FALSE")]
    text = urlencode(query, quote_via=quote, safe=":,/")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, text, "")) + f"&BBOX={WMS_BBOX}"

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
