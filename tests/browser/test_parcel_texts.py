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


READER = """
  const pane = document.getElementById('q2vt-pane-parcel');
  const block = pane.querySelector('details.q2vt-rich-block');
  block.open = true;
  await new Promise((r) => setTimeout(r, 600));
  const opener = block.querySelector('.q2vt-rich-open');
  opener.focus();
  opener.click();
  const dialog = document.querySelector('.q2vt-reader[role=dialog]');
  const text = dialog.querySelector('.q2vt-reader-text');
  const before = parseFloat(text.style.fontSize);
  dialog.querySelector('.q2vt-reader-size[aria-label="Nagyobb betű"]').click();
  const after = parseFloat(text.style.fontSize);
  const box = dialog.getBoundingClientRect();
  // The pages handed to blob URLs (fetching a blob: URL is outside the CSP).
  const blobs = new Map();
  const create = URL.createObjectURL;
  URL.createObjectURL = (blob) => { const url = create(blob); blobs.set(url, blob); return url; };
  // Save: the page goes to a download link (recorded, not followed).
  let saved = null;
  const click = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () { saved = { href: this.href, name: this.download }; };
  [...dialog.querySelectorAll('button')].find((b) => b.textContent === 'Mentés').click();
  HTMLAnchorElement.prototype.click = click;
  const page = await blobs.get(saved.href).text();
  // Print: the page alone in a hidden frame.
  [...dialog.querySelectorAll('button')].find((b) => b.textContent === 'Nyomtatás').click();
  const frame = document.querySelector('iframe.q2vt-reader-frame');
  const framed = await blobs.get(frame.src).text();
  URL.createObjectURL = create;
  await new Promise((r) => setTimeout(r, 500));
  const printed = frame.contentDocument && frame.contentDocument.querySelector('h1');
  document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
  return { title: dialog.querySelector('h2').textContent, width: box.width, height: box.height,
           headings: [...text.querySelectorAll('h3, h4')].map((h) => h.textContent), before, after,
           name: saved.name, page, framed: framed === page, printed: printed && printed.textContent,
           closed: !document.querySelector('.q2vt-reader'), focus: document.activeElement.className };
"""


def test_zone_text_opens_in_a_large_window_to_print_or_save(site, tmp_path):
    out = _run(site["url"], [
        {"eval": "q2vtViewer.map.jumpTo({ center: %s, zoom: 18 }); return 1;" % json.dumps(site["parcel"])},
        {"idle": True}, {"clickLngLat": site["parcel"]}, {"wait": 900}, {"eval": READER},
    ], tmp_path)[1]
    assert out["title"] == "A(z) Lke-1 övezet teljes előírásai"
    assert out["width"] > 900 and out["height"] > 600  # much larger than the side panel
    assert out["headings"] == ["Az övezet saját előírásai", "15. § (3)"]
    assert out["after"] > out["before"]
    assert out["name"] == "a-z-lke-1-ovezet-teljes-eloirasai.html"
    page = out["page"]
    assert page.startswith("<!doctype html>") and '<html lang="hu">' in page
    assert "<h1>A(z) Lke-1 övezet teljes előírásai</h1>" in page and "<p>Szövegek</p>" in page
    assert "<h4>15. § (3)</h4>" in page and "<script" not in page.split("</style>")[1]
    assert out["printed"] == out["title"]  # the frame loaded the page (allowed by the CSP)
    assert out["framed"] and out["closed"] and "q2vt-rich-open" in out["focus"]


BACK = """
  window.q2vtSamePage = 1;  // gone if Back left or reloaded the page
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const back = async () => { history.back(); await sleep(500); };
  const state = () => ({ panel: !document.getElementById('q2vt-panel').hidden,
                         reader: !!document.querySelector('.q2vt-reader') });
  const steps = [];
  steps.push(state());                                   // the parcel report on the phone sheet
  const block = document.querySelector('#q2vt-pane-parcel details.q2vt-rich-block');
  block.open = true;
  await sleep(600);
  block.querySelector('.q2vt-rich-open').click();
  await sleep(100);
  steps.push(state());                                   // + the reading window
  q2vtViewer.map.jumpTo({ center: q2vtViewer.map.getCenter(), zoom: q2vtViewer.map.getZoom() + 0.5 });
  await sleep(900);                                      // the permalink rewrites the hash
  const hash = location.hash;
  await back();
  steps.push(state());                                   // Back: the reading window closes
  await back();
  steps.push(state());                                   // Back: the sheet closes
  // Closed with its own button: Back then has nothing of it left to close.
  document.getElementById('q2vt-menu').click();
  await sleep(100);
  steps.push(state());
  document.getElementById('q2vt-menu').click();
  await sleep(300);
  steps.push(state());
  return { steps, same: window.q2vtSamePage === 1, hashKept: location.hash === hash,
           atBase: !(history.state && history.state.q2vtBack) };  // the next Back leaves
"""


def test_back_closes_what_is_open_instead_of_leaving(site, tmp_path):
    out = _run(site["url"], [
        {"eval": "q2vtViewer.map.jumpTo({ center: %s, zoom: 18 }); return 1;" % json.dumps(site["parcel"])},
        {"idle": True}, {"clickLngLat": site["parcel"]}, {"wait": 900}, {"eval": BACK},
        {"eval": "return [window.q2vtSamePage === 1, history.length];"},
    ], tmp_path, width=390, height=800)
    result = out[1]
    assert result["steps"] == [
        {"panel": True, "reader": False}, {"panel": True, "reader": True},
        {"panel": True, "reader": False}, {"panel": False, "reader": False},
        {"panel": True, "reader": False}, {"panel": False, "reader": False}]
    assert result["same"] and result["hashKept"]  # no reload, today's view kept
    assert result["atBase"]  # no leftover entries: Back with nothing open leaves, as usual
