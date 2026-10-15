"""QGIS diagrams in the web viewer: each feature's pie is drawn as an image
from its category values (resources/web_viewer/diagrams.mjs), in the
category colours and at QGIS's size; histogram bars follow QGIS's lengths."""

import os
import sys

import pytest
from PIL import Image
from qgis.core import QgsPieDiagram

from publishing.controller import export_local
from publishing.models import LayerConfig, PublicationProfile
from publishing.preview_server import PreviewServer

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_diagrams import _diagrams, _layer  # pylint: disable=import-error
    from test_publishing_pipeline import EXTENT  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    base = tmp_path_factory.mktemp("diagrams")
    project = reset_project()
    layer = _diagrams(_layer(base, "Point", "stations"), QgsPieDiagram())
    project.addMapLayer(layer)
    profile = PublicationProfile(title="Diagrams", slug="diagrams", locale="en")
    profile.view.min_zoom, profile.view.max_zoom = 11, 14
    profile.output.local_directory = str(base / "out")
    profile.layers = [LayerConfig(layer.id(), initially_visible=True)]
    profile.interaction.panel_closed = True
    result = export_local(project, profile, EXTENT)
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        yield server.url(f"{profile.slug}/index.html")


IMAGES = """const m = q2vtViewer.map;
  return m.listImages().filter((id) => id.startsWith('q2vt-diagram|')).sort()
    .map((id) => { const i = m.style.getImage(id); return [id, i.data.width / i.pixelRatio]; });"""

# Histogram geometry, drawn by the viewer's own module: fixed size 20 mm,
# bars 5 mm, QGIS's length = value * largest value / height.
HISTOGRAM = """const app = performance.getEntriesByType('resource').find((e) => /\\/assets\\/app\\.mjs$/.test(e.name));
  const d = await import(new URL('diagrams.mjs', app.name).href);
  const spec = {type: 'histogram', px: 2, linePx: 2, width: 20, height: 20, aspect: 1, barWidth: 5,
    spacing: 0, colors: ['red', 'lime', 'blue'], penColor: 'black', penWidth: 0, orientation: 'up',
    interpolated: false, scale: null};
  const box = d.layout(spec, 20, [10, 20, 40]);
  return [box.width, box.height, box.lengths];"""


def test_pies_are_drawn_in_the_category_colours(site, tmp_path):
    sys.path.insert(0, HERE)
    from test_web_viewer_views import _run  # pylint: disable=import-error
    shot = tmp_path / "map.png"
    images, histogram = _run(site, [{"wait": 1500}, {"eval": IMAGES}, {"eval": HISTOGRAM},
                                    {"screenshot": str(shot)}], tmp_path)
    names = [name.split("|", 2)[2] for name, _ in images]
    assert names == ["20.0|10;20;40", "20.0|5;5;0"], images
    for _, width in images:  # 20 mm, a pen and a margin
        assert width == pytest.approx(20 * 96 / 25.4 + 4, abs=1)  # 2 px margin a side
    assert histogram[0] == pytest.approx(30) and histogram[1] == pytest.approx(160)
    assert histogram[2] == pytest.approx([40, 80, 160])
    pixels = Image.open(shot).convert("RGB").getdata()

    def near(colour):
        return sum(1 for p in pixels if all(abs(a - b) < 12 for a, b in zip(p, colour)))
    red, green, blue = near((220, 0, 0)), near((0, 160, 0)), near((0, 0, 220))
    assert red > 200 and green > 200 and blue > 200, (red, green, blue)
    # Areas: red 1/7 + 1/2 of a pie, green 2/7 + 1/2, blue 4/7 (10;20;40 and 5;5;0).
    assert green > red > blue, (red, green, blue)
