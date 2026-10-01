"""Loopback preview server (PUB-07): real byte ranges, HEAD, MIME types, no
outer encoding, traversal protection, nested Unicode paths."""

import http.client
import os
import socket
import urllib.parse

import pytest

from publishing.preview_server import PreviewServer, parse_range, stop_all


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    (root / "nested dir" / "ő ű").mkdir(parents=True)
    (root / "data").mkdir()
    (root / "data" / "map.pmtiles").write_bytes(bytes(range(256)) * 40)  # 10240 bytes
    (root / "index.html").write_text("<p>root</p>", encoding="utf-8")
    (root / "nested dir" / "ő ű" / "index.html").write_text("<p>deep</p>", encoding="utf-8")
    (root / "app.mjs").write_text("export {};", encoding="utf-8")
    (root / "glyphs").mkdir()
    (root / "glyphs" / "0-255.pbf").write_bytes(b"\x0a\x00")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    try:
        os.symlink(tmp_path / "secret.txt", root / "link.txt")
    except OSError:
        pass
    with PreviewServer(str(root)) as server:
        yield server


def _request(server, path, method="GET", headers=None):
    conn = http.client.HTTPConnection(server.host, server.port, timeout=10)
    conn.request(method, path, headers=headers or {})
    response = conn.getresponse()
    body = response.read()
    conn.close()
    return response, body


@pytest.mark.parametrize("header, status, span", [
    ("bytes=0-126", 206, (0, 126)),
    ("bytes=10200-", 206, (10200, 10239)),
    ("bytes=-100", 206, (10140, 10239)),
    ("bytes=10000-99999", 206, (10000, 10239)),
    ("bytes=20000-", 416, None),
    ("bytes=0-1,5-9", 200, None),       # several ranges: whole file (documented)
    ("items=0-5", 200, None),           # malformed: ignored
])
def test_ranges(site, header, status, span):
    response, body = _request(site, "/data/map.pmtiles", headers={"Range": header})
    full = bytes(range(256)) * 40
    assert response.status == status
    assert response.getheader("Content-Encoding") is None  # never an outer encoding
    assert response.getheader("Accept-Ranges") == "bytes"
    if status == 206:
        start, end = span
        assert body == full[start:end + 1]
        assert response.getheader("Content-Range") == f"bytes {start}-{end}/10240"
        assert int(response.getheader("Content-Length")) == end - start + 1
    elif status == 416:
        assert response.getheader("Content-Range") == "bytes */10240" and body == b""
    else:
        assert body == full


def test_head_and_types(site):
    response, body = _request(site, "/data/map.pmtiles", method="HEAD")
    assert response.status == 200 and body == b"" and response.getheader("Content-Length") == "10240"
    assert response.getheader("Content-Type") == "application/vnd.pmtiles"
    for path, kind in (("/app.mjs", "text/javascript"), ("/index.html", "text/html"),
                       ("/glyphs/0-255.pbf", "application/x-protobuf")):
        response, _ = _request(site, path)
        assert response.getheader("Content-Type").startswith(kind)
    response, _ = _request(site, "/missing.mjs")
    assert response.status == 404 and response.getheader("Content-Type").startswith("text/plain")


def test_nested_unicode_paths_and_index(site):
    response, body = _request(site, "/" + urllib.parse.quote("nested dir/ő ű/index.html"))
    assert response.status == 200 and body == "<p>deep</p>".encode()
    response, body = _request(site, "/" + urllib.parse.quote("nested dir/ő ű/"))
    assert body == "<p>deep</p>".encode()
    response, _ = _request(site, "/nested%20dir")  # no directory listing
    assert response.status == 404


@pytest.mark.parametrize("path", ["/../secret.txt", "/%2e%2e/secret.txt", "/data/../../secret.txt",
                                  "/link.txt", "/..%5csecret.txt"])
def test_no_escape_from_the_root(site, path):
    response, body = _request(site, path)
    assert response.status in (403, 404) and b"outside" not in body


def test_loopback_only_and_stop(tmp_path):
    with pytest.raises(ValueError):
        PreviewServer(str(tmp_path), host="0.0.0.0")
    server = PreviewServer(str(tmp_path)).start()
    port = server.port
    stop_all()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()


def test_parse_range_edges():
    assert parse_range("bytes=-0", 10) == ("invalid", None)
    assert parse_range("bytes=5-2", 10) == ("invalid", None)
    assert parse_range("bytes=0-0", 1) == ("partial", (0, 0))
    assert parse_range(None, 10) == ("full", None)


def test_requests_are_recorded(site):
    _request(site, "/data/map.pmtiles", headers={"Range": "bytes=0-9"})
    assert site.requests[-1] == {"path": "/data/map.pmtiles", "method": "GET", "status": 206,
                                 "bytes": 10, "range": "bytes=0-9"}
