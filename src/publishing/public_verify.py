"""
Public verification of an uploaded release (plan §13.4), through the public
URL that browsers use — not through authenticated S3 reads.

Checks: page/module/manifest MIME types and content; the archive's HEAD
size and absence of an outer Content-Encoding; real byte ranges (header,
middle, end) equal to the local archive with consistent Content-Range; a
``200`` answer to a range request is aborted after a few KiB (never a full
download); CORS with the viewer origin when it differs; a glyph URL with
spaces; one tile located through the remote PMTiles directory and decoded
as MVT. Plain ``urllib`` with certificate verification (system/Python CA
store); proxies from the standard environment variables.
"""

import gzip
import hashlib
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

from . import mvt
from .errors import PublishingError

USER_AGENT = "QWebMap-publish-check/1"
SMALL = 256 * 1024


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class VerifyResult:
    checks: List[Check] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append(Check(name, ok, detail))
        return ok

    def failure_code(self) -> str:
        for check in self.checks:
            if not check.ok:
                return {"range": "Q2VT_PUB_RANGE_UNSUPPORTED", "cors": "Q2VT_PUB_CORS",
                        "mime": "Q2VT_PUB_MIME", "encoding": "Q2VT_PUB_MIME"}.get(
                            check.name.split(":")[0], "Q2VT_PUB_PUBLIC_VERIFY")
        return ""


def join_url(base: str, *parts: str) -> str:
    """``base`` + percent-encoded path segments (spaces, Unicode)."""
    base = base.rstrip("/") + "/"
    path = "/".join(urllib.parse.quote(p, safe="/") for p in parts if p)
    return base + path


class _NoRedirectDowngrade(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if req.full_url.startswith("https:") and newurl.startswith("http:"):
            return None  # never follow an https -> http downgrade
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Http:
    def __init__(self, timeout: float = 30.0, context: Optional[ssl.SSLContext] = None):
        self.timeout = timeout
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=context or ssl.create_default_context()),
            _NoRedirectDowngrade())

    def request(self, url: str, method: str = "GET", headers: Optional[dict] = None,
                limit: int = SMALL):
        """(status, headers dict lower-case, body ≤ limit bytes, truncated?)."""
        req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT,
                                                                  "Cache-Control": "no-cache",
                                                                  **(headers or {})})
        try:
            response = self.opener.open(req, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, OSError) as error:
            raise PublishingError("Q2VT_PUB_PUBLIC_VERIFY", f"{url}: {error}") from error
        with response:
            status = response.status if hasattr(response, "status") else response.code
            head = {k.lower(): v for k, v in response.headers.items()}
            body = b"" if method == "HEAD" else response.read(limit + 1)
        return status, head, body[:limit], len(body) > limit


def _range(http: Http, url: str, start: int, end: int, size: int, local: bytes, result: VerifyResult,
           origin: Optional[str] = None) -> bool:
    headers = {"Range": f"bytes={start}-{end}"}
    if origin:
        headers["Origin"] = origin
    status, head, body, _ = http.request(url, headers=headers, limit=max(end - start + 1, 1) + 16)
    if status == 200:
        return result.add(f"range:{start}-{end}", False,
                          "The server answered 200 (whole file) to a range request; byte ranges are "
                          "required (aborted after a few bytes).")
    expected = f"bytes {start}-{end}/{size}"
    ok = status == 206 and head.get("content-range") == expected and body == local
    return result.add(f"range:{start}-{end}", ok,
                      f"status {status}, Content-Range {head.get('content-range')!r}"
                      + ("" if body == local else ", bytes differ from the local archive"))


def verify_release(public_release_url: str, release_dir: str, viewer_origin: Optional[str] = None,
                   http: Optional[Http] = None) -> VerifyResult:
    """Verify ``public_release_url`` (…/releases/<id>/) against the local release."""
    http = http or Http()
    result = VerifyResult()
    with open(os.path.join(release_dir, "release.json"), encoding="utf-8") as handle:
        inventory = {f["path"]: f for f in json.load(handle)["files"]}
    with open(os.path.join(release_dir, "manifest.json"), encoding="utf-8") as handle:
        manifest = json.load(handle)
    # Page and module types: not downloads, not HTML error pages.
    for rel, kind in (("index.html", "text/html"), ("assets/app.mjs", "text/javascript"),
                      ("assets/maplibre-gl-worker.mjs", "text/javascript"),
                      ("style.json", "application/json")):
        status, head, body, truncated = http.request(join_url(public_release_url, rel))
        ok = status == 200 and head.get("content-type", "").startswith(kind) \
            and "attachment" not in head.get("content-disposition", "")
        if ok and not truncated and rel in inventory:
            ok = hashlib.sha256(_decoded(body, head)).hexdigest() == inventory[rel]["sha256"]
        result.add(f"mime:{rel}", ok, f"status {status}, Content-Type {head.get('content-type')!r}")
    status, head, body, _ = http.request(join_url(public_release_url, "manifest.json"))
    try:
        remote = json.loads(_decoded(body, head))
        ok = status == 200 and remote.get("releaseId") == manifest["releaseId"]
    except ValueError:
        ok = False
    result.add("manifest", ok, f"status {status}")
    # The archive: ranges, no outer encoding, a decodable tile.
    source = next((s for s in manifest["sources"] if s["kind"] == "pmtiles"), None)
    if source:
        url = join_url(public_release_url, source["href"])
        local_path = os.path.join(release_dir, *source["href"].split("/"))
        size = os.path.getsize(local_path)
        status, head, _, _ = http.request(url, method="HEAD")
        result.add("archive:head", status == 200 and int(head.get("content-length", -1)) == size,
                   f"status {status}, Content-Length {head.get('content-length')} (local {size})")
        result.add("encoding:archive", not head.get("content-encoding"),
                   f"Content-Encoding {head.get('content-encoding')!r} would break byte offsets")
        with open(local_path, "rb") as handle:
            data = handle.read(127)
            header_ok = _range(http, url, 0, 126, size, data, result, viewer_origin)
            for start in sorted({size // 2, max(0, size - 100)}):
                end = min(size - 1, start + 99)
                handle.seek(start)
                _range(http, url, start, end, size, handle.read(end - start + 1), result)
        if header_ok:
            _remote_tile(http, url, size, result)
        if viewer_origin:
            _cors(http, url, viewer_origin, result)
    glyphs = [p for p in inventory if p.startswith("glyphs/") and p.endswith("0-255.pbf")]
    if glyphs:
        status, head, _, _ = http.request(join_url(public_release_url, glyphs[0]))
        result.add("glyphs", status == 200, f"{glyphs[0]}: status {status}")
    return result


def _decoded(body: bytes, head: dict) -> bytes:
    if head.get("content-encoding") == "gzip":
        return gzip.decompress(body)
    return body


def _cors(http: Http, url: str, origin: str, result: VerifyResult) -> None:
    status, head, _, _ = http.request(url, headers={"Origin": origin, "Range": "bytes=0-15"}, limit=64)
    allowed = head.get("access-control-allow-origin")
    exposed = (head.get("access-control-expose-headers") or "").lower()
    ok = status in (200, 206) and allowed in (origin, "*") and \
        ("content-range" in exposed or allowed == "*" or not exposed)
    result.add("cors", ok, f"Origin {origin}: Access-Control-Allow-Origin {allowed!r}, "
                           f"expose {exposed!r}")


def _remote_tile(http: Http, url: str, size: int, result: VerifyResult) -> None:
    """Read the header, the root directory and one tile through ranges."""
    from .vendor.pmtiles.tile import deserialize_directory, deserialize_header  # pylint: disable=import-outside-toplevel

    def fetch(offset, length):
        status, _, body, _ = http.request(url, headers={"Range": f"bytes={offset}-{offset + length - 1}"},
                                          limit=length + 16)
        if status != 206:
            raise PublishingError("Q2VT_PUB_RANGE_UNSUPPORTED", f"status {status}")
        return body
    try:
        header = deserialize_header(fetch(0, 127))
        entries = deserialize_directory(fetch(header["root_offset"], header["root_length"]))
        depth = 0
        while entries and entries[0].run_length == 0 and depth < 3:  # leaf directory
            entries = deserialize_directory(fetch(header["leaf_directory_offset"] + entries[0].offset,
                                                  entries[0].length))
            depth += 1
        first = entries[0]
        tile = fetch(header["tile_data_offset"] + first.offset, first.length)
        layers = mvt.layer_names(tile)
        result.add("tile", bool(layers), f"tile id {first.tile_id}: {len(layers)} vector layer(s)")
    except (PublishingError, mvt.MvtDecodeError, IndexError, ValueError) as error:
        result.add("tile", False, f"Could not read a tile through ranges: {error}")


def verify_pointer(public_base: str, expected_release: str, http: Optional[Http] = None) -> Check:
    http = http or Http()
    status, head, body, _ = http.request(join_url(public_base, "current.json") +
                                         f"?check={os.urandom(4).hex()}")
    try:
        pointer = json.loads(_decoded(body, head))
    except ValueError:
        return Check("activation", False, f"current.json: status {status}, not JSON")
    ok = status == 200 and pointer.get("releaseId") == expected_release
    return Check("activation", ok, f"current.json points at {pointer.get('releaseId')} "
                                   f"(expected {expected_release}); Cache-Control "
                                   f"{head.get('cache-control')!r}")
