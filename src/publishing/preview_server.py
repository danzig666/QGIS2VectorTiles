"""
Loopback HTTP preview of a published folder, with real byte ranges.

PMTiles readers fetch archive byte ranges, so ``file://`` and naive static
servers are not enough. This server:

* binds 127.0.0.1 (never the LAN) on a free port by default;
* serves GET and HEAD with one byte range (``bytes=a-b``, ``bytes=a-``,
  ``bytes=-n``): 206 + Content-Range; unsatisfiable ranges: 416;
  several ranges in one request: ignored, 200 with the whole file (RFC 9110
  permits ignoring Range; PMTiles clients never send multiple ranges);
* never adds a Content-Encoding (PMTiles offsets must stay valid);
* sends the content types of ``content_types.py`` and ``no-cache``;
* refuses paths outside the served folder (``..``, absolute, symlinks
  leading out) and never serves directory listings;
* records requests (path, status, bytes sent) for tests and diagnostics.

``PreviewServer`` runs in a daemon thread; ``stop()`` (or plugin unload via
``stop_all()``) closes the socket.
"""

import os
import re
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import List, Optional, Tuple

from .content_types import content_type

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
_SERVERS: List["PreviewServer"] = []


def parse_range(header: Optional[str], size: int) -> Tuple[str, Optional[Tuple[int, int]]]:
    """("full", None) | ("partial", (start, end inclusive)) | ("invalid", None)."""
    if not header:
        return "full", None
    header = header.strip()
    if "," in header:
        return "full", None  # multiple ranges: ignored (documented)
    match = _RANGE.match(header.replace(" ", ""))
    if not match or (not match.group(1) and not match.group(2)):
        return "full", None  # malformed Range headers are ignored (RFC 9110)
    first, last = match.group(1), match.group(2)
    if first == "":
        length = int(last)
        if length == 0 or size == 0:
            return "invalid", None
        return "partial", (max(0, size - length), size - 1)
    start = int(first)
    end = int(last) if last else size - 1
    if start >= size or (last and end < start):
        return "invalid", None
    return "partial", (start, min(end, size - 1))


class _Handler(BaseHTTPRequestHandler):
    server_version = "Q2VTPreview/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # quiet; requests are recorded instead
        pass

    def _resolve(self) -> Optional[str]:
        root = self.server.root  # type: ignore[attr-defined]
        raw = urllib.parse.urlsplit(self.path).path
        path = urllib.parse.unquote(raw)
        if "\x00" in path or "\\" in path:
            return None
        parts = [p for p in path.split("/") if p]
        if any(p in (".", "..") for p in parts):
            return None
        target = os.path.join(root, *parts)
        if raw.endswith("/") or not parts:
            target = os.path.join(target, "index.html")
        real = os.path.realpath(target)
        if os.path.commonpath([real, root]) != root:
            return None
        return real if os.path.isfile(real) else ""

    def _send(self, head_only: bool):
        target = self._resolve()
        if target is None:
            self._error(HTTPStatus.FORBIDDEN)
            return
        if not target:
            self._error(HTTPStatus.NOT_FOUND)
            return
        size = os.path.getsize(target)
        kind, span = parse_range(self.headers.get("Range"), size)
        if kind == "invalid":
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{size}")
            self.send_header("Content-Length", "0")
            self._common_headers(target)
            self.end_headers()
            self._record(416, 0)
            return
        start, end = span if span else (0, size - 1)
        length = max(0, end - start + 1)
        self.send_response(HTTPStatus.PARTIAL_CONTENT if span else HTTPStatus.OK)
        if span:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self._common_headers(target)
        self.end_headers()
        sent = 0
        if not head_only and length:
            with open(target, "rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining:
                    block = handle.read(min(1 << 16, remaining))
                    if not block:
                        break
                    try:
                        self.wfile.write(block)
                    except (BrokenPipeError, ConnectionResetError):
                        break
                    sent += len(block)
                    remaining -= len(block)
        self._record(206 if span else 200, sent)

    def _common_headers(self, target: str):
        rel = os.path.relpath(target, self.server.root).replace(os.sep, "/")  # type: ignore[attr-defined]
        self.send_header("Content-Type", content_type(rel))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers",
                         "Accept-Ranges, Content-Range, Content-Length, ETag")

    def _error(self, status: HTTPStatus):
        body = f"{status.value} {status.phrase}".encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self._record(status.value, 0)

    def _record(self, status: int, sent: int):
        with self.server.lock:  # type: ignore[attr-defined]
            self.server.requests.append({  # type: ignore[attr-defined]
                "path": urllib.parse.unquote(urllib.parse.urlsplit(self.path).path),
                "method": self.command, "status": status, "bytes": sent,
                "range": self.headers.get("Range")})

    def do_GET(self):  # noqa: N802
        self._send(head_only=False)

    def do_HEAD(self):  # noqa: N802
        self._send(head_only=True)

    def do_OPTIONS(self):  # noqa: N802
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Range, If-Match, If-None-Match")
        self.send_header("Content-Length", "0")
        self.end_headers()


class PreviewServer:
    """``with PreviewServer(folder) as server: open(server.url("index.html"))``."""

    def __init__(self, root: str, port: int = 0, host: str = "127.0.0.1"):
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise ValueError("The preview server only binds the loopback interface.")
        self.root = os.path.realpath(root)
        self.host = host
        self.port = port
        self.httpd: Optional[ThreadingHTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self) -> "PreviewServer":
        httpd = ThreadingHTTPServer((self.host, self.port), _Handler)
        httpd.daemon_threads = True
        httpd.root = self.root  # type: ignore[attr-defined]
        httpd.requests = []  # type: ignore[attr-defined]
        httpd.lock = threading.Lock()  # type: ignore[attr-defined]
        self.httpd = httpd
        self.port = httpd.server_address[1]
        self.thread = threading.Thread(target=httpd.serve_forever, name="q2vt-preview", daemon=True)
        self.thread.start()
        _SERVERS.append(self)
        return self

    def url(self, path: str = "") -> str:
        quoted = "/".join(urllib.parse.quote(part) for part in path.split("/"))
        return f"http://{self.host}:{self.port}/{quoted}"

    @property
    def requests(self) -> List[dict]:
        if self.httpd is None:
            return []
        with self.httpd.lock:  # type: ignore[attr-defined]
            return list(self.httpd.requests)  # type: ignore[attr-defined]

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.thread is not None:
            self.thread.join(timeout=5)
            self.thread = None
        if self in _SERVERS:
            _SERVERS.remove(self)

    def __enter__(self) -> "PreviewServer":
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def stop_all() -> None:
    """Stop every preview server (plugin unload)."""
    for server in list(_SERVERS):
        server.stop()
