"""Interactive web viewer on a real local publication (PUB-08..12, plan
A05-A10, A12, A13, A15, A24): layer/group/rule toggles and labels switch,
opacity without nesting, visible-polygon labels following toggles and
filters, identify with one record per feature and XSS-safe popups, global
search, cold deep links, permalinks, measurement and the phone layout."""

import json
import os
import subprocess
import urllib.parse

import pytest
from qgis.core import QgsRectangle

from publishing.controller import export_local
from publishing.preview_server import PreviewServer
from publishing.provenance import layer_logical_id

HERE = os.path.dirname(os.path.abspath(__file__))
EXTENT = QgsRectangle(2119000, 6019000, 2123000, 6023000)


def _run(url, actions, tmp_path, width=1000, height=700):
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
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_publishing_pipeline import CANARY, _parcels, _profile  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    base = tmp_path_factory.mktemp("viewer")
    parcels = _parcels(str(base / "parcels.gpkg"))
    project = reset_project()
    group = project.layerTreeRoot().addGroup("Szabályozás")
    project.addMapLayer(parcels, False)
    group.addLayer(parcels)
    profile = _profile(parcels, base)
    profile.layers[0].initially_visible = True
    profile.interaction.measure = True
    result = export_local(project, profile, EXTENT, canaries=[CANARY])
    rel = result.release.release_dir
    features = json.load(open(os.path.join(rel, "features", "manifest.json"), encoding="utf-8"))
    records = []
    for shard in features["shards"]:
        records += json.load(open(os.path.join(rel, "features", shard["path"]), encoding="utf-8"))
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        yield {"server": server, "url": server.url(f"{profile.slug}/index.html"),
               "lid": layer_logical_id(parcels.id()),
               "records": {r["k"]: r for r in records}}


PRELUDE = "const v = q2vtViewer, m = v.map, c = v.controls.control, s = v.controls.state, man = v.manifest;"
VIS = ("const vis = (cid) => c.components.get(cid).styleLayerIds.filter((id) => m.getLayer(id))"
       ".map((id) => m.getLayoutProperty(id, 'visibility') !== 'none');")
LABELS = ("const labels = () => { let n = 0; for (const g of v.labels.groups.values()) "
          "{ const d = m.getSource(g.source)._data; n += ((d.geojson || d).features || []).length; } return n; };")


def test_layer_group_rule_and_label_toggles(site, tmp_path):
    results = _run(site["url"], [
        {"eval": PRELUDE + VIS + """
          const k1 = man.rules.find((r) => r.title === 'Kertvárosi');
          const label = man.components.find((x) => x.role === 'label');
          const all = () => man.components.map((x) => vis(x.id).every(Boolean));
          const before = all();
          s.setIn('rules', k1.id, false);
          const ruleOff = { k1: vis(k1.componentIds[0]), label: vis(label.id),
            others: man.rules.filter((r) => r !== k1).map((r) => vis(r.componentIds[0])) };
          s.setIn('rules', k1.id, true);
          s.set({ labels: false });
          const labelsOff = { label: vis(label.id), fills: man.rules.map((r) => vis(r.componentIds[0])) };
          s.set({ labels: true });
          s.setIn('groups', man.groups[0].id, false);
          const groupOff = all();
          const loader = m.getLayoutProperty('q2vt_visible_loader_' + label.dependsOnSourceLayers[0], 'visibility');
          s.reset();
          return { before, ruleOff, labelsOff, groupOff, loader, after: all() };"""},
    ], tmp_path)
    r = results[0]
    assert all(r["before"]) and all(r["after"])
    assert r["ruleOff"]["k1"] == [False] and all(all(x) for x in r["ruleOff"]["others"])
    assert all(r["ruleOff"]["label"])  # labels follow the layer, not one category
    assert not any(r["labelsOff"]["label"]) and all(all(x) for x in r["labelsOff"]["fills"])
    assert not any(r["groupOff"]) and r["loader"] == "none"  # helper data not loaded while hidden


def test_opacity_is_derived_from_the_original(site, tmp_path):
    results = _run(site["url"], [
        {"eval": PRELUDE + """
          const lid = man.layers[0].id;
          const id = c.components.get(man.rules[0].componentIds[0]).styleLayerIds[0];
          const original = JSON.stringify(m.getPaintProperty(id, 'fill-opacity') ?? null);
          const seen = [];
          for (const value of [0.5, 0.3, 0.8, 1]) { s.setIn('opacity', lid, value); seen.push(m.getPaintProperty(id, 'fill-opacity')); }
          return { original, seen: seen.map((x) => JSON.stringify(x ?? null)) };"""},
    ], tmp_path)
    r = results[0]
    original = json.loads(r["original"])
    seen = [json.loads(x) for x in r["seen"]]
    base = 1 if original is None else original
    if isinstance(base, (int, float)):
        assert seen[:3] == pytest.approx([base * 0.5, base * 0.3, base * 0.8])
    assert "*\", [\"*" not in r["seen"][2]  # never nested
    assert seen[3] == original or (original is None and seen[3] in (1, None))  # A07 reset


def test_visible_labels_follow_toggles_and_filters(site, tmp_path):
    results = _run(site["url"], [
        {"eval": PRELUDE + LABELS + "return labels();"},
        {"eval": PRELUDE + "s.set({ filters: { [man.layers[0].id]: { zone: { values: ['K2'], nulls: false } } } }); return 1;"},
        {"wait": 300}, {"idle": True},
        {"eval": PRELUDE + LABELS + """
          const label = man.components.find((x) => x.role === 'label');
          const id = label.styleLayerIds[0];
          return { n: labels(), filter: JSON.stringify(m.getFilter(id)) };"""},
        {"eval": PRELUDE + "s.setIn('layers', man.layers[0].id, false); return 1;"},
        {"wait": 300}, {"idle": True},
        {"eval": PRELUDE + LABELS + "return labels();"},
    ], tmp_path)
    assert results[0] == 3
    assert results[2]["n"] == 1 and "K2" in results[2]["filter"]
    assert results[4] == 0  # no orphan labels for a hidden layer


def test_identify_one_record_per_feature_and_safe_popup(site, tmp_path):
    record = site["records"]["00123/4"]
    results = _run(site["url"], [
        {"eval": "window.__xss = 0; return 1;"},
        {"clickLngLat": record["p"]},
        {"wait": 600},
        {"eval": """
          const popup = document.querySelector('.q2vt-popup');
          return { title: popup && popup.querySelector('h3').textContent,
                   rows: popup ? [...popup.querySelectorAll('tr')].map((r) => [r.cells[0].textContent, r.cells[1].textContent]) : [],
                   choices: document.querySelectorAll('.q2vt-choices button').length,
                   scripts: popup ? popup.querySelectorAll('script').length : -1,
                   xss: window.__xss, hash: location.hash };"""},
    ], tmp_path)
    r = results[1]
    assert r["title"] == "00123/4" and r["choices"] == 0  # one logical record (fill + label + outline)
    assert ["Helyrajzi szám", "00123/4"] in r["rows"]
    assert ["Megjegyzés", "<script>alert(1)</script>"] in r["rows"]  # A15: shown as text
    assert r["scripts"] == 0 and r["xss"] == 0
    assert "sel=" in r["hash"] and urllib.parse.quote("00123/4", safe="") in r["hash"].replace("%2F", "%2F")


def test_search_finds_offscreen_features(site, tmp_path):
    results = _run(site["url"], [
        {"eval": "q2vtViewer.map.jumpTo({ center: [0, 0], zoom: 3 }); return 1;"},  # far away
        {"type": ["#q2vt-search", "00123/5"]},
        {"wait": 1200},
        {"eval": "return [...document.querySelectorAll('#q2vt-results li[role=option]')].map((li) => li.textContent);"},
        {"press": "ArrowDown"}, {"press": "Enter"},
        {"eval": "for (let i = 0; i < 100 && !document.querySelector('.q2vt-popup h3'); i++) "
                 "await new Promise((r) => setTimeout(r, 100)); return 1;"},
        {"eval": """const p = document.querySelector('.q2vt-popup h3');
                    const c = q2vtViewer.map.getCenter();
                    return { title: p && p.textContent, zoom: q2vtViewer.map.getZoom(), lng: c.lng };"""},
        {"type": ["#q2vt-search", "1234567890123"]},
        {"wait": 1000},
        {"eval": "return [...document.querySelectorAll('#q2vt-results li[role=option]')].map((li) => li.firstChild.textContent);"},
    ], tmp_path)
    assert results[1] and results[1][0].startswith("00123/5")
    assert results[3]["title"] == "00123/5" and results[3]["zoom"] > 10 and abs(results[3]["lng"] - 19.06) < 0.05
    assert results[4] == ["12345678901234567890"]  # 64-bit-like id kept as text


def test_cold_deep_links(site, tmp_path):
    lid = site["lid"]
    base = site["url"]
    link = f"{base}#v=1&sel={lid}~{urllib.parse.quote('00123/5', safe='')}"
    missing = f"{base}#v=1&sel={lid}~nincs-ilyen"
    results = _run(link, [
        {"wait": 1500},
        {"eval": "const p = document.querySelector('.q2vt-popup h3'); return p && p.textContent;"},
        {"goto": missing}, {"wait": 1000},
        {"eval": "return document.getElementById('q2vt-warning').textContent;"},
    ], tmp_path)
    assert results[0] == "00123/5"
    assert "nem található" in results[1]  # Hungarian "not found" message


def test_permalink_round_trip(site, tmp_path):
    results = _run(site["url"], [
        {"eval": PRELUDE + """
          s.setIn('rules', man.rules[1].id, false); s.set({ labels: false });
          s.setIn('opacity', man.layers[0].id, 0.4); m.jumpTo({ center: [19.05, 47.48], zoom: 14.5 });
          await new Promise((r) => setTimeout(r, 700)); return location.href;"""},
    ], tmp_path)
    link = results[0]
    restored = _run(link, [{"eval": PRELUDE + """
        return { rule: s.value.rules[man.rules[1].id], labels: s.value.labels,
                 opacity: s.value.opacity[man.layers[0].id], zoom: Math.round(m.getZoom() * 10) / 10 };"""}],
        tmp_path)
    assert restored[0] == {"rule": False, "labels": False, "opacity": 0.4, "zoom": 14.5}
    bad = _run(site["url"] + "#v=1&r-=not-a-rule&o=lyr-x:500&f=%%%&map=99/999/999", [
        {"eval": PRELUDE + "return { errors: v.diagnostics.errors.length, zoom: m.getZoom() };"}], tmp_path)
    assert bad[0]["errors"] == 0 and bad[0]["zoom"] <= 24  # untrusted URL state ignored safely


def test_measure_distance(site, tmp_path):
    a, b = site["records"]["00123/4"]["p"], site["records"]["00123/5"]["p"]
    results = _run(site["url"], [
        {"eval": "document.getElementById('q2vt-tab-tools').click(); return 1;"},
        {"eval": "[...document.querySelectorAll('#q2vt-pane-tools button')][0].click(); return 1;"},
        {"clickLngLat": a}, {"clickLngLat": b}, {"press": "Enter"}, {"wait": 300},
        {"eval": "return document.querySelector('#q2vt-pane-tools .q2vt-tool-output[aria-live]').textContent;"},
        {"eval": "return document.querySelectorAll('.q2vt-popup').length;"},
    ], tmp_path)
    assert results[2].endswith(" m") or results[2].endswith(" km")
    distance = float(results[2].split()[0].replace(" ", "").replace(",", ".").replace(" ", ""))
    assert 600 < distance < 800  # two adjacent 1 km Web Mercator squares at 47.5° N (~675 m)
    assert results[3] == 0  # measuring does not open popups


def test_phone_layout(site, tmp_path):
    results = _run(site["url"], [
        {"eval": """return { panelHidden: document.getElementById('q2vt-panel').hidden,
                             scrollWidth: document.documentElement.scrollWidth, width: window.innerWidth };"""},
        {"click": [24, 24]}, {"wait": 200},
        {"eval": """const p = document.getElementById('q2vt-panel');
                    const r = p.getBoundingClientRect();
                    return { hidden: p.hidden, bottom: Math.round(r.bottom), height: Math.round(r.height),
                             expanded: document.getElementById('q2vt-menu').getAttribute('aria-expanded') };"""},
        {"press": "Escape"},
        {"eval": "return document.getElementById('q2vt-panel').hidden;"},
    ], tmp_path, width=360, height=640)
    assert results[0]["panelHidden"] and results[0]["scrollWidth"] <= results[0]["width"]
    assert not results[1]["hidden"] and results[1]["expanded"] == "true" and results[1]["bottom"] == 640
    assert results[2] is True
