"""Viewer with a bundled vector basemap, a QGIS raster layer, theme presets
and locked layers/groups: flavors switch under the project layers (project
background hidden while shown), the raster draws from its own image
archive, presets switch layers but never a locked one, links cannot switch
a locked layer off, nothing is requested from another site; desktop and
phone layouts without horizontal overflow; dark appearance switch."""

import json
import os
import subprocess

import pytest
from qgis.core import QgsLayerTreeModel, QgsMapThemeCollection, QgsRasterLayer

from publishing.controller import export_local
from publishing.models import GroupConfig, LayerConfig
from publishing.preview_server import PreviewServer
from publishing.provenance import group_logical_id, layer_logical_id
from publishing_fixtures import protomaps_planet

HERE = os.path.dirname(os.path.abspath(__file__))


def _run(url, actions, tmp_path, width=1200, height=800):
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
    from test_publishing_pipeline import _parcels, _profile  # pylint: disable=import-error
    from test_publishing_raster import EXTENT, make_raster  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    base = tmp_path_factory.mktemp("bmviewer")
    project = reset_project()
    parcels = _parcels(str(base / "parcels.gpkg"))
    zones = _parcels(str(base / "zones.gpkg"))
    zones.setName("Övezetek")
    ortho = QgsRasterLayer(make_raster(base / "ortho.tif", alpha=True), "Ortofotó")
    root = project.layerTreeRoot()
    plan, background = root.addGroup("Szabályozás"), root.addGroup("Háttér")
    for layer in (parcels, zones, ortho):
        project.addMapLayer(layer, False)
    plan.addLayer(parcels)
    plan.addLayer(zones)
    background.addLayer(ortho)
    profile = _profile(parcels, base)
    profile.layers[0].initially_visible = True
    profile.layers.append(LayerConfig(zones.id(), toggleable=False))
    profile.layers.append(LayerConfig(ortho.id(), raster_format="png", raster_max_zoom=15))
    profile.groups = [GroupConfig(["Szabályozás"], toggleable=False)]
    root.findLayer(ortho.id()).setItemVisibilityChecked(False)
    project.mapThemeCollection().insert("Terv", QgsMapThemeCollection.createThemeFromCurrentState(
        root, QgsLayerTreeModel(root)))
    root.findLayer(ortho.id()).setItemVisibilityChecked(True)
    root.findLayer(parcels.id()).setItemVisibilityChecked(False)
    root.findLayer(zones.id()).setItemVisibilityChecked(False)
    project.mapThemeCollection().insert("Csak orto", QgsMapThemeCollection.createThemeFromCurrentState(
        root, QgsLayerTreeModel(root)))
    root.findLayer(parcels.id()).setItemVisibilityChecked(True)
    root.findLayer(zones.id()).setItemVisibilityChecked(True)
    profile.themes.names = ["Terv", "Csak orto"]
    profile.basemap.kind = "protomaps"
    profile.basemap.source = protomaps_planet(str(base / "planet.pmtiles"))
    profile.basemap.flavors, profile.basemap.initial = ["light", "dark"], "light"
    profile.accent_color = "#0f766e"
    profile.interaction.street_search = True  # OpenStreetMap street names in the search
    result = export_local(project, profile, EXTENT)
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        yield {"server": server, "url": server.url(f"{profile.slug}/index.html"),
               "parcels": layer_logical_id(parcels.id()), "zones": layer_logical_id(zones.id()),
               "ortho": layer_logical_id(ortho.id()), "plan": group_logical_id(("Szabályozás",))}


PRELUDE = "const v = q2vtViewer, m = v.map, s = v.controls.state, man = v.manifest;"
ORDER = ("const ids = m.getStyle().layers.map((l) => l.id);"
         "const firstProject = ids.findIndex((id) => !id.startsWith('q2vt-bm-') && m.getLayer(id).type !== 'background');")


def test_basemap_raster_themes_and_locks(site, tmp_path):
    results = _run(site["url"], [
        {"eval": PRELUDE + ORDER + """
          const bm = ids.filter((id) => id.startsWith('q2vt-bm-'));
          const raster = ids.indexOf('q2vt-raster-""" + site["ortho"] + """');
          return { active: v.basemap.active, bm: bm.length, lastBm: ids.indexOf(bm[bm.length - 1]), firstProject,
                   raster, rasterVisible: m.getLayoutProperty(ids[raster], 'visibility') !== 'none',
                   background: m.getLayoutProperty('background', 'visibility'),
                   accent: getComputedStyle(document.documentElement).getPropertyValue('--q2vt-accent').trim(),
                   chips: [...document.querySelectorAll('#q2vt-themes .q2vt-chip')].map((c) => c.textContent),
                   locks: document.querySelectorAll('#q2vt-pane-layers .q2vt-lock').length,
                   source: !!m.getSource('q2vt_basemap') };"""},
        {"eval": PRELUDE + "s.set({ basemap: 'dark' }); await new Promise((r) => setTimeout(r, 600));" + ORDER +
                 "return { active: v.basemap.active, dark: ids.filter((i) => i.startsWith('q2vt-bm-')).length, firstProject,"
                 " pressed: document.querySelector('.q2vt-basemap-option[aria-pressed=true] span:last-child').textContent };"},
        {"eval": PRELUDE + "s.set({ basemap: 'none' }); await new Promise((r) => setTimeout(r, 400));"
                 "return { bm: m.getStyle().layers.filter((l) => l.id.startsWith('q2vt-bm-')).length,"
                 " background: m.getLayoutProperty('background', 'visibility') };"},
        {"eval": PRELUDE + """
          const preset = man.themes.presets.find((p) => p.title === 'Csak orto');
          s.applyTheme(preset.id);
          const after = { parcels: s.value.layers['""" + site["parcels"] + """'], zones: s.value.layers['""" + site["zones"] + """'],
                          ortho: s.value.layers['""" + site["ortho"] + """'],
                          pressed: [...document.querySelectorAll('#q2vt-themes .q2vt-chip[aria-pressed=true]')].map((c) => c.textContent) };
          s.setIn('layers', '""" + site["zones"] + """', false);
          s.setIn('groups', '""" + site["plan"] + """', false);
          after.lockedStays = s.value.layers['""" + site["zones"] + """'] && s.value.groups['""" + site["plan"] + """'];
          return after;"""},
    ], tmp_path)
    first, dark, none, theme = results
    assert first["source"] and first["active"] == "light" and first["bm"] > 30
    assert first["lastBm"] < first["firstProject"]  # basemap under every project layer
    assert first["background"] == "none"            # project background hidden under the basemap
    assert first["raster"] >= first["firstProject"] and first["rasterVisible"]
    assert first["accent"] == "#0f766e" and first["chips"] == ["Terv", "Csak orto"]
    assert first["locks"] == 2                       # locked group + locked layer
    assert dark["active"] == "dark" and dark["dark"] > 30 and dark["pressed"] in ("Sötét", "Dark")
    assert none == {"bm": 0, "background": "visible"}
    assert theme["parcels"] is False and theme["ortho"] is True
    assert theme["zones"] is True                    # a preset never switches a locked layer off
    assert theme["pressed"] == ["Csak orto"] and theme["lockedStays"]


def test_links_cannot_switch_locked_layers_off_and_no_third_party(site, tmp_path):
    url = site["url"] + f"#v=1&l-={site['zones']},{site['parcels']}&g-={site['plan']}&bm=dark"
    results = _run(url, [
        {"eval": PRELUDE + "return { zones: s.value.layers['" + site["zones"] + "'], parcels: s.value.layers['"
                 + site["parcels"] + "'], plan: s.value.groups['" + site["plan"] + "'], bm: v.basemap.active,"
                 " external: performance.getEntriesByType('resource').map((e) => e.name)"
                 ".filter((n) => !n.startsWith(location.origin) && !n.startsWith('blob:') && !n.startsWith('data:')) };"},
    ], tmp_path)
    assert results[0]["zones"] is True and results[0]["plan"] is True
    assert results[0]["parcels"] is False and results[0]["bm"] == "dark"
    assert results[0]["external"] == []


@pytest.mark.parametrize("width,height", [(1366, 820), (390, 800), (360, 640)])
def test_layouts_fit_and_dark_switch(site, tmp_path, width, height):
    results = _run(site["url"], [
        {"eval": """const d = document.documentElement;
          const button = document.querySelector('#q2vt-about-actions button');
          const before = d.dataset.theme || 'auto';
          if (document.getElementById('q2vt-panel').hidden) document.getElementById('q2vt-menu').click();
          await new Promise((r) => setTimeout(r, 300));
          button.click();
          const header = document.getElementById('q2vt-header').getBoundingClientRect();
          const panel = document.getElementById('q2vt-panel').getBoundingClientRect();
          const controls = document.querySelector('.maplibregl-ctrl-top-right').getBoundingClientRect();
          return { overflow: d.scrollWidth - window.innerWidth, before, after: d.dataset.theme,
                   headerBottom: header.bottom, panelTop: panel.top, panelBottom: panel.bottom,
                   controlsTop: controls.top, width: window.innerWidth, height: window.innerHeight };"""},
    ], tmp_path, width, height)
    r = results[0]
    assert r["overflow"] <= 0 and r["after"] in ("dark", "light") and r["after"] != r["before"]
    if width <= 760:  # phone: bottom sheet, controls below the top bar
        assert r["panelBottom"] == r["height"] and r["controlsTop"] >= r["headerBottom"]
    else:             # desktop: the panel floats under the header
        assert r["panelTop"] > r["headerBottom"]


def test_one_search_box_finds_fields_and_streets(site, tmp_path):
    results = _run(site["url"], [
        {"eval": "document.getElementById('q2vt-search').focus(); return 1;"}, {"wait": 800},
        {"type": ["#q2vt-search", "fő utca"]}, {"wait": 1500},
        {"eval": """return [...document.querySelectorAll('#q2vt-results li[role=option]')].map((li) =>
          [li.querySelector('.q2vt-result-label').textContent, li.querySelector('.q2vt-layer-name').textContent]);"""},
        {"press": "Enter"}, {"wait": 2500},
        {"eval": """const m = q2vtViewer.map, c = m.getCenter();
          return { popups: document.querySelectorAll('.maplibregl-popup').length,
            marker: m.getSource('q2vt_search_marker').serialize().data.features.length, zoom: m.getZoom(), lat: c.lat,
            value: document.getElementById('q2vt-search').value };"""},
        {"type": ["#q2vt-search", "00123/4"]}, {"wait": 1500},
        {"eval": """return [...document.querySelectorAll('#q2vt-results li[role=option] .q2vt-layer-name')]
          .map((n) => n.textContent);"""},
    ], tmp_path)
    streets, chosen, parcels = results[1], results[2], results[3]
    assert streets and all(label == "Fő utca" and kind == "Utca (OpenStreetMap)" for label, kind in streets)
    assert chosen["popups"] == 0 and chosen["marker"] == 1 and chosen["value"] == "Fő utca"
    assert parcels and "Utca (OpenStreetMap)" not in parcels  # the same box finds the parcels
