"""4.26: a zone's full regulation text (a table of simple HTML per zone code)
is published as one small file per zone, cleaned of everything but simple
structure, and opened on request under each zone of the parcel report."""

import json
import os
import subprocess
import sys

import pytest
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry, QgsRectangle

from publishing.controller import export_local
from publishing.models import LayerConfig, PublicationProfile
from publishing.preview_server import PreviewServer

HERE = os.path.dirname(os.path.abspath(__file__))
LKE = ('<h3>Az övezet saját előírásai</h3><h4 class="x">15. § (3)</h4><p onclick="alert(1)">(3) Az előkert '
       '5,0 m.</p><ul><li>a) egy<li>b) kettő</ul><script>window.hacked = 1</script>'
       '<img src="x" onerror="window.hacked = 2"><table><tr><th>Jel</th><td colspan="2">Lke-1</td></tr></table>')


def _run(url, actions, tmp_path, width=1280, height=820):
    path = tmp_path / f"actions_{abs(hash(json.dumps(actions)))}.json"
    path.write_text(json.dumps(actions))
    run = subprocess.run(["node", "interact.mjs", url, str(path), str(width), str(height)],
                         capture_output=True, text=True, cwd=HERE, timeout=300)
    assert run.returncode == 0, run.stderr[-3000:]
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert not out["pageErrors"], out
    return out["results"]


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_publishing_parcel_report import EOV, X, Y, box, layer  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    base = tmp_path_factory.mktemp("texts")
    project = reset_project()
    parcels = layer("Polygon", "Földrészletek", ["hrsz"], [(("100/1",), box(0, 0, 100, 40))], base / "parcels.gpkg")
    zoning = layer("Polygon", "Övezetek", ["szab_ov"],
                   [(("Lke-1",), box(0, 0, 70, 40)), (("Gksz",), box(70, -60, 150, 40))])
    texts = layer("None", "Előírások szövege", ["szab_ov", "eloiras_html"],
                  [((" Lke-1 ", LKE), QgsGeometry()), (("Vt", "<p>Másik övezet</p>"), QgsGeometry())])
    for item in (parcels, zoning, texts):
        project.addMapLayer(item)
    profile = PublicationProfile(title="Szövegek", slug="szovegek", locale="hu")
    profile.view.min_zoom, profile.view.max_zoom = 14, 15
    profile.output.local_directory = str(base / "out")
    profile.layers = [LayerConfig(parcels.id()), LayerConfig(zoning.id())]
    info = profile.parcel_info
    info.enabled, info.parcel_layer_id, info.key_field = True, parcels.id(), "hrsz"
    info.zoning_layer_id, info.zoning_code_field = zoning.id(), "szab_ov"
    info.text_layer_id, info.text_code_field, info.text_field = texts.id(), "szab_ov", "eloiras_html"
    to_web = QgsCoordinateTransform(QgsCoordinateReferenceSystem(EOV), QgsCoordinateReferenceSystem("EPSG:3857"),
                                    project.transformContext())
    result = export_local(project, profile, to_web.transformBoundingBox(QgsRectangle(X - 50, Y - 100, X + 200, Y + 100)))
    to_wgs = QgsCoordinateTransform(QgsCoordinateReferenceSystem(EOV), QgsCoordinateReferenceSystem("EPSG:4326"),
                                    project.transformContext())
    point = to_wgs.transform(X + 30, Y + 20)
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        yield {"url": server.url(f"{os.path.basename(result.publication_dir)}/index.html"),
               "release": result.release.release_dir, "parcel": [point.x(), point.y()]}


def test_texts_are_published_one_file_per_zone_and_cleaned(site):
    folder = os.path.join(site["release"], "parcels")
    catalog = json.load(open(os.path.join(folder, "catalog.json"), encoding="utf-8"))
    assert catalog["texts"] == {"Lke-1": "text-1.json", "Vt": "text-2.json"}  # trimmed code, sorted
    text = json.load(open(os.path.join(folder, "text-1.json"), encoding="utf-8"))
    assert text["code"] == "Lke-1"
    assert "<script" not in text["html"] and "onclick" not in text["html"] and "<img" not in text["html"]
    assert '<h4>15. § (3)</h4>' in text["html"] and '<td colspan="2">Lke-1</td>' in text["html"]
    assert "<li>a) egy</li><li>b) kettő</li>" in text["html"]


def test_parcel_report_opens_the_zone_text(site, tmp_path):
    out = _run(site["url"], [
        {"eval": "q2vtViewer.map.jumpTo({ center: %s, zoom: 18 }); return 1;" % json.dumps(site["parcel"])},
        {"idle": True}, {"clickLngLat": site["parcel"]}, {"wait": 900},
        {"eval": """
          const pane = document.getElementById('q2vt-pane-parcel');
          const blocks = [...pane.querySelectorAll('details.q2vt-rich-block')];
          const before = blocks.map((b) => b.querySelector('.q2vt-rich') !== null);
          blocks[0].open = true;
          await new Promise((r) => setTimeout(r, 600));
          const rich = blocks[0].querySelector('.q2vt-rich');
          // The viewer cleans again: even a text injected into the file stays plain.
          const { richText } = await import(new URL('assets/rich_text.mjs', q2vtViewer.releaseUrl).href);
          const dirty = richText('<p onmouseover="x()">a</p><a href="javascript:x()">b</a><svg><script>x()</script></svg>');
          return { summaries: blocks.map((b) => b.querySelector('summary').textContent), before,
                   headings: [...rich.querySelectorAll('h3, h4')].map((h) => h.textContent),
                   items: rich.querySelectorAll('li').length, attrs: [...rich.querySelectorAll('*')]
                     .filter((n) => n.attributes.length && !n.matches('[colspan], [rowspan]')).length,
                   hacked: window.hacked || 0, dirty: dirty.innerHTML };
        """},
    ], tmp_path)[1]
    assert out["summaries"] == ["A(z) Lke-1 övezet teljes előírásai"]  # Gksz has no text
    assert out["before"] == [False]  # loaded only when opened
    assert out["headings"] == ["Az övezet saját előírásai", "15. § (3)"] and out["items"] == 2
    assert out["attrs"] == 0 and out["hacked"] == 0
    assert out["dirty"] == "<p>a</p>b"
