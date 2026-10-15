"""Animated markers in the web viewer: the frames (one icon layer each) are
shown in turn at the marker's frame rate, one at a time; printing shows the
first frame, still, and the animation goes on after it."""

import os
import sys

import pytest

from publishing.controller import export_local
from publishing.models import LayerConfig, PublicationProfile
from publishing.preview_server import PreviewServer

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_animated_markers import _gif, _points  # pylint: disable=import-error
    from test_publishing_pipeline import EXTENT  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    base = tmp_path_factory.mktemp("animation")
    project = reset_project()
    layer = _points(base, _gif(base / "blink.gif"), rate=8.0)
    project.addMapLayer(layer)
    profile = PublicationProfile(title="Animation", slug="animation", locale="en")
    profile.view.min_zoom, profile.view.max_zoom = 11, 13
    profile.output.local_directory = str(base / "out")
    profile.layers = [LayerConfig(layer.id(), initially_visible=True)]
    profile.interaction.panel_closed = True
    result = export_local(project, profile, EXTENT)
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        yield server.url(f"{profile.slug}/index.html")


SHOWN = """const m = q2vtViewer.map;
  return m.getStyle().layers.filter((l) => l.metadata && l.metadata['q2vt:frame'])
    .map((l) => [l.metadata['q2vt:frame'].index, m.getPaintProperty(l.id, 'icon-opacity')]);"""


def test_frames_play_in_turn_and_print_still(site, tmp_path, monkeypatch):
    monkeypatch.setenv("Q2VT_MOTION", "1")  # the harness asks for reduced motion otherwise
    sys.path.insert(0, HERE)
    from test_web_viewer_views import _run  # pylint: disable=import-error
    actions = [{"wait": 1500}]
    for _ in range(8):
        actions += [{"eval": SHOWN}, {"wait": 60}]
    actions += [
        {"eval": "window.print = () => {}; q2vtViewer.printer.prepare('map'); await new Promise((r) => setTimeout(r, 400)); return 1;"},
        {"eval": SHOWN},
        {"eval": "q2vtViewer.printer.restore(); await new Promise((r) => setTimeout(r, 600)); return 1;"},
    ]
    for _ in range(6):
        actions += [{"eval": SHOWN}, {"wait": 60}]
    results = _run(site, actions, tmp_path)
    playing, printed, after = results[:8], results[9], results[11:]

    def visible(sample):
        shown = [index for index, opacity in sample if opacity not in (0, None)]
        assert len(shown) == 1, sample  # one frame at a time
        return shown[0]
    assert len(set(visible(s) for s in playing)) >= 2  # 8 frames a second over ~0.5 s
    assert visible(printed) == 0 and dict(printed)[0] == 1
    assert len(set(visible(s) for s in after)) >= 2


def test_reduced_motion_keeps_the_first_frame(site, tmp_path):
    sys.path.insert(0, HERE)
    from test_web_viewer_views import _run  # pylint: disable=import-error
    samples = _run(site, [{"wait": 1500}, {"eval": SHOWN}, {"wait": 400}, {"eval": SHOWN}], tmp_path)
    for sample in samples:
        assert [index for index, opacity in sample if opacity not in (0, None)] == [0], sample
