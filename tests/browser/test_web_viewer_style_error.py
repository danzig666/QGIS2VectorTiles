"""A style MapLibre rejects (a layer's minzoom past 24) fires "error" and
never "load": the viewer showed "Loading…" for ever. It shows the error. An
error after the style was accepted (a source layer with no tiles: empty in
the export extent) does not stop the map."""

import json
import os
import sys

import pytest

from publishing.controller import export_local
from publishing.preview_server import PreviewServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = """await new Promise((r) => setTimeout(r, 3000));
  const box = document.getElementById('q2vt-error');
  return { shown: !box.hidden, failed: document.body.classList.contains('q2vt-failed'),
           ready: q2vtViewer.ready, codes: q2vtViewer.diagnostics.errors.map((e) => e.code) };"""


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    """The exported map's URL and its style.json path."""
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
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        yield server.url(f"{profile.slug}/index.html"), style_path


def _open_with_extra_layer(site, tmp_path, **change):
    sys.path.insert(0, HERE)
    from test_web_viewer_views import _run  # pylint: disable=import-error
    url, style_path = site
    original = open(style_path, encoding="utf-8").read()
    style = json.loads(original)
    extra = dict(next(layer for layer in style["layers"] if layer.get("source-layer")))
    extra.update(id="extra", **change)
    style["layers"].append(extra)
    try:
        json.dump(style, open(style_path, "w", encoding="utf-8"))
        (state,) = _run(url, [{"eval": STATE}], tmp_path)
    finally:
        open(style_path, "w", encoding="utf-8").write(original)
    return state


def test_a_rejected_style_shows_an_error_instead_of_loading(site, tmp_path):
    state = _open_with_extra_layer(site, tmp_path, minzoom=30)
    assert state["shown"] and state["failed"] and not state["ready"], state
    assert state["codes"] == ["Q2VT_PUB_STYLE"], state


def test_a_source_layer_without_tiles_does_not_stop_the_map(site, tmp_path):
    state = _open_with_extra_layer(site, tmp_path, **{"source-layer": "empty_in_extent"})
    assert state["ready"] and not state["shown"] and not state["codes"], state
