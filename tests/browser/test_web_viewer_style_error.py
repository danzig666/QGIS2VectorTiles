"""A style MapLibre rejects (a layer's minzoom past 24) fires "error" and
never "load": the viewer showed "Loading…" for ever. It shows the error."""

import json
import os
import sys

import pytest

from publishing.controller import export_local
from publishing.preview_server import PreviewServer

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope="module")
def broken_site(tmp_path_factory):
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_publishing_pipeline import EXTENT, _parcels, _profile  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    base = tmp_path_factory.mktemp("style_error")
    project = reset_project()
    parcels = _parcels(str(base / "parcels.gpkg"))
    project.addMapLayer(parcels)
    profile = _profile(parcels, base)
    result = export_local(project, profile, EXTENT)
    style_path = next(os.path.join(root, "style.json") for root, _, files
                      in os.walk(result.publication_dir) if "style.json" in files)
    style = json.load(open(style_path, encoding="utf-8"))
    broken = dict(next(layer for layer in style["layers"] if layer.get("source-layer")))
    broken.update(id="broken", minzoom=30)
    style["layers"].append(broken)
    json.dump(style, open(style_path, "w", encoding="utf-8"))
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        yield server.url(f"{profile.slug}/index.html")


def test_a_rejected_style_shows_an_error_instead_of_loading(broken_site, tmp_path):
    sys.path.insert(0, HERE)
    from test_web_viewer_views import _run  # pylint: disable=import-error
    (state,) = _run(broken_site, [{"eval": """await new Promise((r) => setTimeout(r, 3000));
        const box = document.getElementById('q2vt-error');
        return { shown: !box.hidden, failed: document.body.classList.contains('q2vt-failed'),
                 codes: q2vtViewer.diagnostics.errors.map((e) => e.code) };"""}], tmp_path)
    assert state["shown"] and state["failed"], state
    assert state["codes"] == ["Q2VT_PUB_STYLE"], state
