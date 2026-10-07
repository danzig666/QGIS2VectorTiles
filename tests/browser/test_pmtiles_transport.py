"""PMTiles web releases in the real viewer (PUB-07, plan A01/A03/A04/A08/A16/A23):

* XYZ and PMTiles transports of the same QGIS export render the same pixels
  in the same browser (fractional zooms, overzoom, map-unit hatch/outline);
* visible-polygon labels work on PMTiles-loaded tiles;
* a cold view reads byte ranges of a large archive, not the whole file;
* nested folders with spaces/Unicode, the stable entry (query/hash kept),
  no third-party requests, distinct error states.
"""

import http.server
import json
import os
import subprocess
import threading
import urllib.parse

import pytest
from PIL import Image, ImageChops
from qgis.core import (QgsFillSymbol, QgsLinePatternFillSymbolLayer, QgsPalLayerSettings,
                       QgsProcessingFeedback, QgsRectangle, QgsSingleSymbolRenderer, QgsTextFormat,
                       QgsUnitTypes, QgsVectorLayerSimpleLabeling, Qgis)
from qgis.PyQt.QtGui import QColor

from publishing.models import PublicationProfile
from publishing.preview_server import PreviewServer
from publishing.web_builder import build_release
from publishing_fixtures import fixture_bundle
from q2vt_fixtures import reset_project, zoning_layer

HERE = os.path.dirname(os.path.abspath(__file__))
EXTENT = QgsRectangle(2119000, 6019000, 2123000, 6023000)
MAP_UNITS = Qgis.RenderUnit.MapUnits if hasattr(Qgis, "RenderUnit") else QgsUnitTypes.RenderMapUnits


def _node(script, *args):
    run = subprocess.run(["node", script, *map(str, args)], capture_output=True, text=True,
                         cwd=HERE, timeout=300)
    assert run.returncode == 0, run.stderr[-3000:]
    return json.loads(run.stdout.strip().splitlines()[-1])


def _layer(path):
    layer = zoning_layer(path=path)
    fill = QgsFillSymbol.createSimple({"color": "255,220,200", "outline_color": "120,0,0",
                                       "outline_width": "12", "outline_width_unit": "MapUnit"})
    hatch = QgsLinePatternFillSymbolLayer()
    hatch.setLineAngle(30)
    hatch.setDistance(120)
    hatch.setDistanceUnit(MAP_UNITS)
    hatch.subSymbol().symbolLayer(0).setColor(QColor("darkgreen"))
    fill.appendSymbolLayer(hatch)
    layer.setRenderer(QgsSingleSymbolRenderer(fill))
    settings = QgsPalLayerSettings()
    settings.fieldName = "zone"
    settings.placement = Qgis.LabelPlacement.OverPoint
    settings.centroidWhole = False  # QGIS default: label on the visible part
    fmt = QgsTextFormat()
    fmt.setSize(14)
    fmt.setColor(QColor("black"))
    settings.setFormat(fmt)
    layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    layer.setLabelsEnabled(True)
    return layer


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-error
    base = tmp_path_factory.mktemp("transport")
    layer = _layer(str(base / "zoning.gpkg"))
    reset_project(layer)
    exporter = QGIS2VectorTiles(min_zoom=10, max_zoom=14, extent=EXTENT, output_dir=str(base / "out"),
                                feedback=QgsProcessingFeedback(), serve=False, add_result_layer=False)
    assert exporter.convert_project_to_vector_tiles()
    bundle = exporter.export_bundle()
    site = base / "site"
    profile = PublicationProfile(title="Transport parity", slug="parity", locale="en")
    pm = build_release(bundle, profile, str(site / "nested dir" / "ő pmtiles"))
    xyz = build_release(bundle, profile, str(site / "nested dir" / "ő xyz"), transport="xyz")
    with PreviewServer(str(site)) as server:
        yield {"server": server, "bundle": bundle, "pm": pm, "xyz": xyz, "base": base,
               "site": str(site)}


def _entry(server, release):
    rel = os.path.relpath(release.release_dir, server.root).replace(os.sep, "/")
    return server.url(f"{rel}/index.html")


def test_xyz_and_pmtiles_render_identically(published, tmp_path):
    center = published["bundle"].view["center"]
    views = [{"id": f"z{zoom}".replace(".", "_"), "lon": center[0], "lat": center[1], "zoom": zoom}
             for zoom in (10.6, 12, 13.5, 14, 15.4)]  # fractional, max tile zoom, overzoom
    (tmp_path / "views.json").write_text(json.dumps(views))
    out = {}
    for kind in ("pm", "xyz"):
        folder = tmp_path / kind
        folder.mkdir()
        out[kind] = _node("transport_capture.mjs", _entry(published["server"], published[kind]),
                          tmp_path / "views.json", folder)
        assert not out[kind]["pageErrors"]
        assert all(not r["errors"] for r in out[kind]["results"]), out[kind]
    for view in views:
        a = Image.open(tmp_path / "pm" / f"{view['id']}.png").convert("RGB")
        b = Image.open(tmp_path / "xyz" / f"{view['id']}.png").convert("RGB")
        diff = ImageChops.difference(a, b)
        changed = sum(1 for px in diff.getdata() if max(px) > 8)
        assert changed <= 0.002 * a.width * a.height, (view, changed)  # same renderer, same tiles
        # The map is drawn (not an empty/error page): several distinct colours.
        assert len(a.getcolors(1 << 20) or []) > 20
    labels = {kind: [r["labels"] for r in out[kind]["results"]] for kind in out}
    assert labels["pm"] == labels["xyz"] and all(n > 0 for n in labels["pm"]), labels


def test_stable_entry_routes_to_the_release_with_state(published):
    server = published["server"]
    root = os.path.dirname(os.path.dirname(published["pm"].release_dir))
    rel = os.path.relpath(root, server.root).replace(os.sep, "/")
    # (zoom 14: the map does not zoom out far beyond its 4 km extent)
    url = server.url(f"{rel}/index.html") + "?layers=a#v=1&map=14.00/47.481352/19.053267"
    state = _node("open_viewer.mjs", url, "", 800, 600)
    assert state["ready"] and not state["errors"], state
    # The entry loads the current release, which shows the stable address
    # again with the query and hash (4.7.2: reloads and shared links follow
    # the current release); the camera state is applied.
    assert urllib.parse.unquote(state["url"]).endswith(
        f"{rel}/index.html?layers=a#v=1&map=14.00/47.481352/19.053267"), state["url"]
    assert abs(state["zoom"] - 14) < 1e-6
    assert state["external"] == []  # A23: no third-party requests
    assert not [c for c in state["console"] if "Content Security Policy" in c]


@pytest.fixture(scope="module")
def large(tmp_path_factory):
    base = tmp_path_factory.mktemp("large")
    bundle = fixture_bundle(str(base / "export"), max_zoom=7)  # 21,845 tiles
    bundle.style["layers"] = [l for l in bundle.style["layers"] if l["type"] != "symbol"]
    release = build_release(bundle, PublicationProfile(title="Large", slug="large", locale="en"),
                            str(base / "site" / "large"))
    return base, release


def test_cold_view_reads_ranges_not_the_archive(large):
    base, release = large
    archive = os.path.getsize(os.path.join(release.release_dir, "data", "map.pmtiles"))
    with PreviewServer(str(base / "site")) as server:
        state = _node("open_viewer.mjs", _entry(server, release), "", 800, 600, "(async () => {"
                      "const m = q2vtViewer.map; m.jumpTo({center: [10, 45], zoom: 7});"
                      "await new Promise(r => { m.once('idle', r); m.triggerRepaint(); }); return 1; })()")
        assert state["ready"] and not state["errors"], state
        reads = [r for r in server.requests if r["path"].endswith("map.pmtiles")]
    assert reads and all(r["status"] == 206 and r["range"] for r in reads)
    served = sum(r["bytes"] for r in reads)
    assert served < 0.25 * archive, (served, archive)  # A04: partial reads only


class _NoRangeHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def test_server_without_ranges_is_reported(large):
    base, release = large
    handler = lambda *a, **k: _NoRangeHandler(*a, directory=str(base / "site"), **k)  # noqa: E731
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        rel = os.path.relpath(release.release_dir, base / "site").replace(os.sep, "/")
        state = _node("open_viewer.mjs", f"http://127.0.0.1:{httpd.server_address[1]}/{rel}/index.html",
                      "", 800, 600)
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert [e["code"] for e in state["errors"]] == ["Q2VT_PUB_RANGE_UNSUPPORTED"], state


def test_invalid_manifest_and_raster_style_are_refused(published, tmp_path):
    import shutil  # pylint: disable=import-outside-toplevel
    server = published["server"]
    for case in ("manifest", "raster"):
        copy = os.path.join(published["site"], f"broken-{case}")
        shutil.copytree(published["pm"].release_dir, copy)
        if case == "manifest":
            with open(os.path.join(copy, "manifest.json"), "w", encoding="utf-8") as handle:
                handle.write('{"schemaVersion": 7}')
            expected = "Q2VT_PUB_SCHEMA_UNSUPPORTED"
        else:
            path = os.path.join(copy, "style.json")
            style = json.load(open(path, encoding="utf-8"))
            style["sources"]["osm"] = {"type": "raster", "tiles": ["https://x/{z}/{x}/{y}.png"]}
            json.dump(style, open(path, "w", encoding="utf-8"))
            expected = "Q2VT_PUB_RASTER_SOURCE"
        state = _node("open_viewer.mjs", server.url(f"broken-{case}/index.html"), "", 600, 400)
        assert [e["code"] for e in state["errors"]] == [expected], state
