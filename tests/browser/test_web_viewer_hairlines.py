""""Thin hairlines": a zero-width outline is half a device pixel on every
screen. At 0.5 CSS px it was a whole device pixel on a 2x (4K) screen -
QGIS's hairline, no thinner. The viewer divides the exported width by the
screen's pixel ratio; the line stays visible (lighter, not gone)."""

import json
import os
import sys

import pytest
from PIL import Image
from qgis.PyQt.QtGui import QColor

from publishing.controller import export_local
from publishing.preview_server import PreviewServer

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_publishing_pipeline import EXTENT, _parcels, _profile  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    base = tmp_path_factory.mktemp("hairlines")
    project = reset_project()
    parcels = _parcels(str(base / "parcels.gpkg"))
    renderer = parcels.renderer()
    for index, category in enumerate(renderer.categories()):
        symbol = category.symbol().clone()
        symbol.symbolLayer(0).setStrokeWidth(0)  # hairline outlines
        symbol.symbolLayer(0).setColor(QColor(255, 255, 255, 0))  # no fill: only the lines
        renderer.updateCategorySymbol(index, symbol)
    project.addMapLayer(parcels)
    profile = _profile(parcels, base)
    assert profile.output.thin_hairlines  # on by default
    profile.layers[0].initially_visible = True
    profile.interaction.panel_closed = True  # the map alone
    result = export_local(project, profile, EXTENT)
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        yield server.url(f"{profile.slug}/index.html")


WIDTHS = """const m = q2vtViewer.map;
  return m.getStyle().layers.filter((l) => l.metadata && l.metadata['q2vt:hairline'])
    .map((l) => [JSON.stringify(m.getPaintProperty(l.id, 'line-width')), window.devicePixelRatio]);"""


@pytest.mark.parametrize("ratio", [1, 2])
def test_hairlines_are_half_a_device_pixel_on_every_screen(site, tmp_path, monkeypatch, ratio):
    sys.path.insert(0, HERE)
    from test_web_viewer_views import _run  # pylint: disable=import-error
    monkeypatch.setenv("Q2VT_DSF", str(ratio))
    shot = tmp_path / f"map_{ratio}.png"
    (widths,) = _run(site, [{"eval": WIDTHS}, {"screenshot": str(shot)}], tmp_path)
    assert widths, "no hairline layer"
    for width, device in widths:
        curve = json.loads(width)
        assert device == ratio
        assert curve[:3] == ["interpolate", ["linear"], ["zoom"]]
        assert curve[4] == pytest.approx(0.35 / ratio) and curve[6] == pytest.approx(0.6 / ratio)
    # Still drawn: dark pixels where the outlines are (no fills).
    image = Image.open(shot).convert("L")
    assert image.size[0] == 1200 * ratio
    middle = image.crop((300 * ratio, 200 * ratio, 900 * ratio, 600 * ratio))
    dark = sum(1 for value in middle.getdata() if value < 200)
    assert dark > 100 * ratio, dark
    middle.save(tmp_path / f"middle_{ratio}.png")
