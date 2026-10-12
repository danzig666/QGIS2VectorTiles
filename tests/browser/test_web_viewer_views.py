"""Viewer views (map themes) that do not fit in one row become a drop-down
instead of buttons running off the screen; the map panel can start closed
(4.35). The panel opens at start by default (test_web_viewer_basemap_raster)."""

import json
import os
import subprocess
import sys

import pytest
from qgis.core import QgsLayerTreeModel, QgsMapThemeCollection

from publishing.controller import export_local
from publishing.preview_server import PreviewServer
from publishing.provenance import layer_logical_id

HERE = os.path.dirname(os.path.abspath(__file__))
VIEWS = [f"Szabályozási nézet {number}" for number in range(1, 9)]


def _run(url, actions, tmp_path, width=1200, height=800):
    path = tmp_path / f"actions_{abs(hash(json.dumps(actions)))}_{width}.json"
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
    from test_publishing_pipeline import EXTENT, _parcels, _profile  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    base = tmp_path_factory.mktemp("views")
    project = reset_project()
    parcels = _parcels(str(base / "parcels.gpkg"))
    project.addMapLayer(parcels)
    root = project.layerTreeRoot()
    for number, name in enumerate(VIEWS):
        root.findLayer(parcels.id()).setItemVisibilityChecked(number % 2 == 0)
        project.mapThemeCollection().insert(name, QgsMapThemeCollection.createThemeFromCurrentState(
            root, QgsLayerTreeModel(root)))
    root.findLayer(parcels.id()).setItemVisibilityChecked(True)
    profile = _profile(parcels, base)
    profile.layers[0].initially_visible = True
    profile.themes.names = list(VIEWS)
    profile.interaction.panel_closed = True
    result = export_local(project, profile, EXTENT)
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        yield {"url": server.url(f"{profile.slug}/index.html"), "parcels": layer_logical_id(parcels.id())}


STATE = """const v = q2vtViewer, s = v.controls.state, bar = document.getElementById('q2vt-themes');
  const select = bar.querySelector('.q2vt-theme-select');
  const shown = (e) => !e.hidden && getComputedStyle(e).display !== 'none';
  const state = () => ({ chips: [...bar.querySelectorAll('.q2vt-chip')].filter(shown).length,
    select: shown(select), value: select.value, options: select.options.length,
    overflow: bar.scrollWidth > bar.clientWidth + 1,
    right: Math.round(Math.max(...[...bar.children].filter(shown).map((c) => c.getBoundingClientRect().right))),
    width: innerWidth, panel: !document.getElementById('q2vt-panel').hidden,
    parcels: s.value.layers['%s'] });"""


def test_many_views_become_a_drop_down_and_the_panel_starts_closed(site, tmp_path):
    prelude = STATE % site["parcels"]
    for width in (1200, 420):
        start, picked, back = _run(site["url"], [
            {"eval": prelude + "return state();"},
            {"eval": prelude + """const id = v.manifest.themes.presets.find((p) => p.title === '%s').id;
                select.value = id; select.dispatchEvent(new Event('change'));
                await new Promise((r) => setTimeout(r, 200)); return state();""" % VIEWS[1]},
            {"eval": prelude + """document.getElementById('q2vt-menu').click();
                await new Promise((r) => setTimeout(r, 300)); return state();"""},
        ], tmp_path, width=width)
        assert start["select"] and start["chips"] == 0, (width, start)  # eight views do not fit
        assert start["options"] == len(VIEWS) + 1 and not start["overflow"]
        assert start["right"] <= start["width"]                         # nothing off the screen
        assert not start["panel"] and start["parcels"] is True          # closed panel at start
        presets = picked["value"]
        assert presets and picked["parcels"] is False                   # view 2: parcels off
        assert back["panel"]                                            # the menu button opens it
