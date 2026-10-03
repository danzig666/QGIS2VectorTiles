"""Parcel report in the viewer: clicking a parcel opens its report in the
panel (no popup) with the total area, the numbered parts and their markers
on the map, zone graphics, restrictions with their legend graphics and the
notice; a link to the parcel reopens it; text only (no HTML injection).
Legend "only visible": entries outside the view disappear and come back."""

import json
import os
import subprocess
import sys

import pytest

from publishing.preview_server import PreviewServer

HERE = os.path.dirname(os.path.abspath(__file__))


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
    from test_publishing_parcel_report import build_site  # pylint: disable=import-error
    built = build_site(tmp_path_factory.mktemp("parcelviewer"), legend_visible_only=True)
    publication = os.path.dirname(os.path.dirname(built["rel"]))
    with PreviewServer(os.path.dirname(publication)) as server:
        built["url"] = server.url(f"{os.path.basename(publication)}/index.html")
        yield built


CARD = """const pane = document.getElementById('q2vt-pane-parcel');
  return { title: pane.querySelector('h2')?.textContent, total: pane.querySelector('.q2vt-pr-total-value')?.textContent,
    parts: [...pane.querySelectorAll('.q2vt-pr-part')].map((p) => p.querySelector('strong').textContent),
    nums: [...pane.querySelectorAll('.q2vt-pr-part .q2vt-pr-num')].map((n) => n.textContent),
    cuts: [...pane.querySelectorAll('.q2vt-pr-chip')].map((c) => c.textContent),
    restrictions: [...pane.querySelectorAll('.q2vt-pr-restriction strong')].map((s) => s.textContent),
    swatches: [...pane.querySelectorAll('.q2vt-pr-swatch')].filter((i) => i.complete && i.naturalWidth > 0).length,
    markers: document.querySelectorAll('.q2vt-pr-marker').length,
    popups: document.querySelectorAll('.maplibregl-popup').length,
    selected: document.getElementById('q2vt-tab-parcel').getAttribute('aria-selected'),
    disclaimer: pane.querySelector('.q2vt-pr-disclaimer')?.textContent };"""


def test_click_opens_the_parcel_report(site, tmp_path):
    anchor = site["records"]["100/1"]["p"][0]["x"]
    results = _run(site["url"], [
        {"eval": "q2vtViewer.map.jumpTo({ center: %s, zoom: 18 }); return 1;" % json.dumps(anchor)},
        {"idle": True},
        {"clickLngLat": anchor}, {"wait": 800},
        {"eval": CARD},
        {"eval": "q2vtViewer.permalink.schedule(); await new Promise((r) => setTimeout(r, 500)); return location.href;"},
    ], tmp_path)
    card, link = results[1], results[2]
    assert card["title"] == "Hrsz. 100/1" and card["total"] in ("4000 m²", "4 000 m²")
    assert card["parts"] == ["Lke-1", "Gksz", "Köu"] and card["nums"] == ["1", "2", "3"]
    assert card["markers"] == 3 and card["popups"] == 0 and card["selected"] == "true"
    assert any("Szabályozási vonal" in c for c in card["cuts"])
    assert card["restrictions"] == ["Régészeti lelőhely", "Műemlék környezete"]
    assert card["swatches"] >= 5 and card["disclaimer"].startswith("Tájékoztató")
    again = _run(link, [{"wait": 1500}, {"eval": CARD}], tmp_path)[0]
    assert again["title"] == "Hrsz. 100/1"


def test_legend_lists_only_what_is_visible(site, tmp_path):
    anchor = site["records"]["100/1"]["p"][0]["x"]
    results = _run(site["url"], [
        {"eval": "document.getElementById('q2vt-tab-legend').click(); q2vtViewer.map.jumpTo({ center: %s, zoom: 17 }); return 1;" % json.dumps(anchor)},
        {"idle": True}, {"wait": 300},
        {"eval": "return document.querySelectorAll('#q2vt-pane-legend .q2vt-legend-item').length;"},
        {"eval": "q2vtViewer.map.jumpTo({ center: [0, 0], zoom: 15 }); return 1;"},
        {"idle": True}, {"wait": 300},
        {"eval": "return [...document.querySelectorAll('#q2vt-pane-legend .q2vt-legend-item')].map((i) => i.textContent).concat([q2vtViewer.map.getZoom(), JSON.stringify(q2vtViewer.map.getCenter())]);"},
        {"eval": "const box = document.querySelector('#q2vt-pane-legend input[role=switch]'); box.click(); return box.checked;"},
        {"eval": "return document.querySelectorAll('#q2vt-pane-legend .q2vt-legend-item').length;"},
    ], tmp_path)
    assert results[1] >= 1                 # the parcels in view are listed
    assert results[3][:-2] == []            # nothing drawn far away: nothing listed
    assert results[4] is False and results[5] >= 1  # switched off: the full legend again


PRINT_STATE = """const sheet = document.getElementById('q2vt-print-sheet');
  return { classes: document.body.className, title: sheet?.querySelector('h1')?.textContent || '',
    card: !!sheet?.querySelector('article.q2vt-pr'), parts: [...(sheet?.querySelectorAll('.q2vt-pr-part strong') || [])].map((s) => s.textContent),
    buttons: sheet ? sheet.querySelectorAll('button').length : -1,
    legend: sheet ? sheet.querySelectorAll('.q2vt-legend-print .q2vt-legend-item').length : -1,
    scale: sheet?.querySelector('.q2vt-print-scale')?.textContent || '',
    metres: (() => { const m = q2vtViewer.map, w = m.getCanvas().getBoundingClientRect().width, h = m.getCanvas().getBoundingClientRect().height;
      return { px: w, m: m.unproject([0, h / 2]).distanceTo(m.unproject([w, h / 2])) }; })() };"""


def test_print_fills_one_sheet_and_restores(site, tmp_path):
    anchor = site["records"]["100/1"]["p"][0]["x"]
    results = _run(site["url"], [
        {"eval": "window.print = () => { window.__printed = (window.__printed || 0) + 1; }; q2vtViewer.map.jumpTo({ center: %s, zoom: 18 }); return 1;" % json.dumps(anchor)},
        {"idle": True},
        {"clickLngLat": anchor}, {"wait": 800},
        {"eval": "await q2vtViewer.printer.print('parcel'); return window.__printed;"},
        {"eval": PRINT_STATE},
        {"eval": "window.dispatchEvent(new Event('afterprint')); await new Promise((r) => setTimeout(r, 100)); return 1;"},
        {"eval": PRINT_STATE},
        {"eval": "window.dispatchEvent(new Event('afterprint')); q2vtViewer.printer.scale = 1000; await q2vtViewer.printer.print('map'); return window.__printed;"},
        {"eval": PRINT_STATE},
    ], tmp_path)
    parcel, restored, legend = results[2], results[4], results[6]
    assert results[1] == 1 and results[5] == 2
    assert "q2vt-printing-parcel" in parcel["classes"] and parcel["title"]
    assert parcel["card"] and parcel["parts"] == ["Lke-1", "Gksz", "Köu"] and parcel["buttons"] == 0
    assert "q2vt-printing" not in restored["classes"] and not restored["card"]
    assert "q2vt-printing-map" in legend["classes"] and legend["legend"] > 0 and not legend["card"]
    # Map scale: the screen's, rounded to a standard scale, or the chosen one, exact on paper
    # (a CSS pixel is 0.0254 / 96 m at 100 %).
    standard = {250, 500, 1000, 1500, 2000, 2500, 4000, 5000, 10000, 20000, 25000, 50000, 100000, 200000, 500000}
    shown = int("".join(ch for ch in parcel["scale"].split(":")[-1] if ch.isdigit()))
    assert shown in standard
    assert "".join(ch for ch in legend["scale"].split(":")[-1] if ch.isdigit()) == "1000"
    paper = legend["metres"]["px"] * 0.0254 / 96
    assert abs(legend["metres"]["m"] / paper / 1000 - 1) < 0.01
