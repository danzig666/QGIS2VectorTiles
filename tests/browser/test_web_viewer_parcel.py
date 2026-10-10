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
        # Far from every feature (with the extent limit lifted).
        {"eval": "q2vtViewer.map.setTransformConstrain(null); q2vtViewer.map.jumpTo({ center: [0, 0], zoom: 15 }); return 1;"},
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
    bounds: q2vtViewer.map.getBounds().toArray().flat(),
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
    standard = [100, 200, 250, 500, 1000, 1500, 2000, 2500, 4000, 5000, 10000, 20000, 25000, 50000,
                100000, 200000, 500000]
    shown = int("".join(ch for ch in parcel["scale"].split(":")[-1] if ch.isdigit()))
    assert shown in standard
    # A parcel prints zoomed on it: wholly on the map, at the closest standard
    # scale that shows it (not the screen's zoom 18), filling much of the map.
    west, south, east, north = site["records"]["100/1"]["bb"]
    w, s, e, n = parcel["bounds"]
    assert w < west and e > east and s < south and n > north
    assert max((east - west) / (e - w), (north - south) / (n - s)) > 0.3
    assert "".join(ch for ch in legend["scale"].split(":")[-1] if ch.isdigit()) == "1000"
    paper = legend["metres"]["px"] * 0.0254 / 96
    assert abs(legend["metres"]["m"] / paper / 1000 - 1) < 0.01


def test_the_address_bar_keeps_the_stable_entry(site, tmp_path):
    """Opened through the publication's stable address, the page shows that
    address (not releases/<id>/index.html); data still loads from the release,
    and both links are right: the stable one and this version's own."""
    results = _run(site["url"], [
        {"idle": True},
        {"eval": """const links = q2vtViewer.permalink.links();
          return { path: location.pathname, base: document.baseURI, stable: links.stable, versioned: links.versioned,
            tiles: q2vtViewer.map.querySourceFeatures(q2vtViewer.map.getStyle().layers.find((l) => l.source)?.source || '').length };"""},
    ], tmp_path)
    state = results[0]
    entry = "/" + site["url"].split("/", 3)[3]
    assert state["path"] == entry and "/releases/" not in state["path"]
    assert "/releases/r-" in state["base"]                     # relative URLs: the release
    assert state["stable"].split("#")[0].endswith(entry)
    assert "/releases/r-" in state["versioned"] and "#" in state["versioned"]


@pytest.fixture(scope="module")
def legend_only_site(tmp_path_factory):
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_publishing_parcel_report import build_site  # pylint: disable=import-error
    built = build_site(tmp_path_factory.mktemp("legendonly"), layers_panel=False)
    publication = os.path.dirname(os.path.dirname(built["rel"]))
    with PreviewServer(os.path.dirname(publication)) as server:
        built["url"] = server.url(f"{os.path.basename(publication)}/index.html")
        yield built


@pytest.fixture(scope="module")
def no_labels_switch_site(tmp_path_factory):
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_publishing_parcel_report import build_site  # pylint: disable=import-error
    built = build_site(tmp_path_factory.mktemp("nolabelsswitch"), labels_toggle=False)
    publication = os.path.dirname(os.path.dirname(built["rel"]))
    with PreviewServer(os.path.dirname(publication)) as server:
        built["url"] = server.url(f"{os.path.basename(publication)}/index.html")
        yield built


def test_the_labels_switch_can_be_left_out(no_labels_switch_site, tmp_path):
    """Interaction -> Viewer -> Labels switch unticked: the Layers tab has no
    labels switch, and labels stay on (a remembered or linked 'labels off'
    could not be undone without the switch). The viewer used to ignore it."""
    results = _run(no_labels_switch_site["url"], [
        {"eval": """return { row: !!document.querySelector('#q2vt-pane-layers .q2vt-row-labels'),
                             tree: !!document.querySelector('#q2vt-pane-layers .q2vt-tree'),
                             labels: q2vtViewer.controls.state.value.labels };"""},
        {"eval": "q2vtViewer.controls.state.set({ labels: false }); "
                 "await new Promise((r) => setTimeout(r, 200)); return q2vtViewer.controls.state.value.labels;"},
    ], tmp_path)
    assert results[0] == {"row": False, "tree": True, "labels": True}
    assert results[1] is True


LEGEND_ROWS = """return [...document.querySelectorAll('#q2vt-pane-legend .q2vt-legend-layer')].map((b) => ({
  title: (b.querySelector('h3 > span:last-child') || b.querySelector('.q2vt-legend-item > span:last-child')).textContent,
  on: b.querySelector('input[role=switch]') ? b.querySelector('input[role=switch]').checked : null,
  off: b.classList.contains('q2vt-legend-off') }));"""


def test_without_the_layers_tab_the_legend_switches_layers(legend_only_site, tmp_path):
    anchor = legend_only_site["records"]["100/1"]["p"][0]["x"]
    results = _run(legend_only_site["url"], [
        {"eval": "document.getElementById('q2vt-tab-legend').click(); q2vtViewer.map.jumpTo({ center: %s, zoom: 15 }); return 1;" % json.dumps(anchor)},
        {"idle": True}, {"wait": 300},
        {"eval": "return !!document.getElementById('q2vt-tab-layers');"},
        {"eval": LEGEND_ROWS},
        {"eval": """const row = [...document.querySelectorAll('#q2vt-pane-legend .q2vt-legend-layer')]
            .find((b) => b.textContent.includes('Földrészletek'));
          row.querySelector('input[role=switch]').click(); return 1;"""},
        {"idle": True}, {"wait": 300},
        {"eval": LEGEND_ROWS},
        {"eval": "return location.hash;"},
    ], tmp_path)
    tab, before, after = results[1], results[2], results[4]
    assert tab is False
    parcels = next(r for r in before if r["title"] == "Földrészletek")
    assert parcels["on"] is True and not parcels["off"]
    # Switched off: still listed (one row, switch off), so it can come back.
    parcels = next(r for r in after if r["title"] == "Földrészletek")
    assert parcels["on"] is False and parcels["off"]
    assert len(after) == len(before)
