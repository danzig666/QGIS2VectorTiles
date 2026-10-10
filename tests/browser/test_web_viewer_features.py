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
from publishing.shard_pack import read_shard

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
        records += read_shard(os.path.join(rel, "features"), shard)
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        yield {"server": server, "url": server.url(f"{profile.slug}/index.html"),
               "lid": layer_logical_id(parcels.id()),
               "records": {r["k"]: r for r in records},
               "manifest": json.load(open(os.path.join(rel, "manifest.json"), encoding="utf-8"))}


PRELUDE = "const v = q2vtViewer, m = v.map, c = v.controls.control, s = v.controls.state, man = v.manifest;"
VIS = ("const vis = (cid) => c.components.get(cid).styleLayerIds.filter((id) => m.getLayer(id))"
       ".map((id) => m.getLayoutProperty(id, 'visibility') !== 'none');")
LABELS = ("const labels = () => { let n = 0; for (const f of v.labels.snapshot().values()) n += f.length; "
          "return n; };")


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


@pytest.fixture(scope="module")
def site_without_popups(tmp_path_factory):
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_publishing_pipeline import CANARY, _parcels, _profile  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    base = tmp_path_factory.mktemp("nopopups")
    parcels = _parcels(str(base / "parcels.gpkg"))
    project = reset_project()
    project.addMapLayer(parcels)
    profile = _profile(parcels, base)
    profile.layers[0].initially_visible = True
    profile.interaction.popups = False
    result = export_local(project, profile, EXTENT, canaries=[CANARY])
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        yield {"url": server.url(f"{profile.slug}/index.html")}


def test_search_works_without_popups(site_without_popups, tmp_path):
    """With popups off the search box used to be left out (it opened the
    found feature through the popup code). Now a found feature is zoomed to
    and marked, without a popup."""
    results = _run(site_without_popups["url"], [
        {"eval": "q2vtViewer.map.jumpTo({ center: [0, 0], zoom: 3 }); return !!document.getElementById('q2vt-search');"},
        {"type": ["#q2vt-search", "00123/5"]},
        {"wait": 1200},
        {"press": "ArrowDown"}, {"press": "Enter"},
        {"wait": 2500},
        {"eval": """const m = q2vtViewer.map, c = m.getCenter();
                    return { zoom: m.getZoom(), lng: c.lng, popups: document.querySelectorAll('.q2vt-popup').length,
                             marker: !!m.getLayer('q2vt_search_marker') };"""},
    ], tmp_path)
    assert results[0] is True
    r = results[1]
    assert r["zoom"] > 10 and abs(r["lng"] - 19.06) < 0.05 and r["popups"] == 0 and r["marker"], r


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


def test_popup_has_no_version_note_on_export_scoped_layers(site, tmp_path):
    """A layer without a feature key (links only work in this release) used
    to add "Feature links of this layer only work in this version of the
    map." to every popup: confusing, and it took space. No such note now."""
    record = site["records"]["00123/4"]
    results = _run(site["url"], [
        {"eval": PRELUDE + "man.layers.forEach((l) => { l.identityScope = 'export'; }); return 1;"},
        {"clickLngLat": record["p"]},
        {"wait": 600},
        {"eval": """const p = document.querySelector('.q2vt-popup');
                    return { title: p && p.querySelector('h3').textContent,
                             notes: p ? [...p.querySelectorAll('.q2vt-note')].map((n) => n.textContent) : null };"""},
    ], tmp_path)
    assert results[1]["title"] == "00123/4" and results[1]["notes"] == [], results[1]


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
        {"eval": "return [...document.querySelectorAll('#q2vt-results li[role=option]')].map((li) => li.querySelector('.q2vt-result-label').textContent);"},
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


def test_a_link_shows_what_its_sender_saw(site, tmp_path):
    """A returning visitor's remembered choices (labels off, a rule off) used
    to stay when they opened someone's link: the link only names what the
    sender changed from the published map, and it was merged into the saved
    state. Pasted into a tab where the map was open, only the hash changed
    and the viewer ignored the link altogether. A link now shows the
    published map with the sender's changes, in a new tab or the same one."""
    rules = [r["id"] for r in site["manifest"]["rules"]]
    link = f"{site['url']}#v=1&r-={urllib.parse.quote(rules[1], safe='')}"
    state = PRELUDE + ("return { labels: s.value.labels, r0: s.value.rules[man.rules[0].id], "
                       "r1: s.value.rules[man.rules[1].id] };")
    remember = PRELUDE + "s.set({ labels: false }); s.setIn('rules', man.rules[0].id, false); return 1;"
    # The same tab: only the hash changes.
    results = _run(site["url"], [
        {"eval": remember},
        {"goto": link},
        {"eval": "for (let i = 0; i < 100 && !(window.q2vtViewer && q2vtViewer.ready && q2vtViewer.controls); i++) "
                 "await new Promise((r) => setTimeout(r, 100)); return 1;"},
        {"wait": 500},
        {"eval": state},
    ], tmp_path)
    assert results[2] == {"labels": True, "r0": True, "r1": False}
    # A new visit (a new tab) with the remembered choices in storage.
    results = _run(site["url"], [
        {"eval": remember},
        {"goto": site["url"] + "?new-tab=1" + link[len(site["url"]):]},
        {"eval": state},
    ], tmp_path)
    assert results[1] == {"labels": True, "r0": True, "r1": False}


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


def test_map_stays_on_the_extent(site, tmp_path):
    """"Keep the web map on the extent" (default): the centre cannot leave
    the publication extent and the map zooms out at most one level beyond
    the zoom that shows the whole extent."""
    results = _run(site["url"], [{"eval": PRELUDE + """
        const [w, s0, e, n] = man.view.bounds;
        const fit = m.cameraForBounds([[w, s0], [e, n]], { padding: 0 }).zoom;
        m.jumpTo({ center: [10, 60], zoom: 0 });
        const c0 = m.getCenter();
        m.panBy([5000, -5000], { animate: false });
        const c1 = m.getCenter();
        return { limit: man.view.limitToExtent, fit, zoom: m.getZoom(),
                 inside: [c0, c1].every((c) => c.lng >= w && c.lng <= e && c.lat >= s0 && c.lat <= n) };"""}],
        tmp_path)
    out = results[0]
    assert out["limit"] is True and out["inside"]
    assert out["zoom"] >= out["fit"] - 1 - 1e-6 and out["zoom"] > 5


def test_compact_embed_mode_in_another_page(site, tmp_path):
    """"Copy embed code" (?embed) in an <iframe> of another page: the map
    loads framed, compact, with the panel closed and a link to the full map
    (this view, without ?embed); the wheel scrolls the page, not the map
    (Ctrl + scroll / two fingers zoom it)."""
    from q2vt_plugin.src.gui.publish_dialog import embed_code  # pylint: disable=import-error
    server = site["server"]
    code = embed_code(site["url"] + "#v=1", "Térkép <teszt>")
    assert code.startswith('<iframe src="') and "index.html?embed#v=1" in code  # a view link keeps its #…
    assert 'title="Térkép &lt;teszt&gt;"' in code
    host = os.path.join(server.root, "host.html")
    with open(host, "w", encoding="utf-8") as handle:
        handle.write("<!doctype html><html><body style='margin:0'><h1>Önkormányzat</h1>"
                     + embed_code(site["url"], "Térkép") + "<div style='height:2000px'></div></body></html>")
    run = subprocess.run(["node", "embed_check.mjs", server.url("host.html")], capture_output=True,
                         text=True, cwd=HERE, timeout=300)
    assert run.returncode == 0, run.stderr[-3000:]
    out = json.loads(run.stdout.strip().splitlines()[-1])
    assert out["ready"] and not out["errors"] and not out["pageErrors"], out
    assert out["framed"] and out["embed"] and out["panelHidden"] and out["cooperative"], out
    assert out["fullMap"].startswith(site["url"].split("?")[0]) and "embed" not in out["fullMap"], out
    assert out["zoomAfterWheel"] == 0, out
    normal = _run(site["url"], [{"eval": "return [document.body.classList.contains('q2vt-embed'), "
                                         "document.getElementById('q2vt-panel').hidden, "
                                         "q2vtViewer.map.cooperativeGestures.isEnabled()];"}], tmp_path)
    assert normal[0] == [False, False, False]  # the full map is unchanged
