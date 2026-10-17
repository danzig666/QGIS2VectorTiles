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


def test_low_memory_is_the_default(site, tmp_path):
    """The browser keeps the tiles of about one zoom level out of view
    (MapLibre's default: five); a city's parcels took over 1 GB."""
    sys.path.insert(0, HERE)
    from test_web_viewer_views import _run  # pylint: disable=import-error
    (levels,) = _run(site, [{"eval": "return q2vtViewer.map._maxTileCacheZoomLevels;"}], tmp_path)
    assert levels == 1


def test_low_memory_caps_the_pixel_ratio(site, tmp_path, monkeypatch):
    """A 3x screen: the map draws at 2 device pixels per CSS pixel (its
    drawing buffer 4/9 of the screen's) and the hairlines follow the map's
    ratio (half of one of its pixels), again when that ratio changes."""
    sys.path.insert(0, HERE)
    from test_web_viewer_views import _run  # pylint: disable=import-error
    monkeypatch.setenv("Q2VT_DSF", "3")
    out = _run(site, [{"eval": "const m = q2vtViewer.map; return [m.getPixelRatio(), m.getCanvas().width];"},
                      {"eval": WIDTHS},
                      {"eval": "q2vtViewer.map.setPixelRatio(1); return q2vtViewer.map.getCanvas().width;"},
                      {"eval": WIDTHS}], tmp_path)
    (ratio, canvas), before, canvas_1x, after = out
    assert ratio == 2 and canvas == 1200 * 2 and canvas_1x == 1200
    assert before and len(before) == len(after)
    for (width, device), (later, _) in zip(before, after):
        assert device == 3
        assert json.loads(width)[4] == pytest.approx(0.35 / 2) and json.loads(later)[4] == pytest.approx(0.35)


def test_moving_and_switching_layers_do_not_copy_the_style(site, tmp_path):
    """map.getStyle() copies every style layer (823 of them in a city's
    map): the viewer reads MapLibre's own layers instead while the map is
    moved, layers are switched and features clicked, for the boxes of
    MapLibre's own labels (every label update of line and "around point"
    labels) and the original paint of the layer switches."""
    sys.path.insert(0, HERE)
    from test_web_viewer_views import _run  # pylint: disable=import-error
    (out,) = _run(site, [{"eval": """const v = q2vtViewer, m = v.map, s = v.controls.state;
      const asset = (name) => import(new URL(`assets/${name}`, v.releaseUrl).href);
      const [labels, styles, maplibregl] = await Promise.all(
        ['visible_labels.mjs', 'style_control.mjs', 'maplibre-gl.mjs'].map(asset));
      let copies = 0;
      const own = m.getStyle;
      m.getStyle = function (...args) { copies += 1; return own.apply(this, args); };
      const settle = () => new Promise((r) => setTimeout(r, 700));
      const id = v.manifest.layers[0].id, styleId = v.manifest.components[0].styleLayerIds[0];
      const original = new styles.StyleControl(m, v.manifest).originals.get(styleId);
      labels.renderedLabelBoxes(m, maplibregl, new Map(), m.getZoom(), 1e-6);
      m.jumpTo({ center: m.getCenter(), zoom: m.getZoom() + 1 }); await settle();
      s.setIn('layers', id, false); await settle();
      const hidden = m.getLayoutProperty(styleId, 'visibility');
      s.setIn('layers', id, true); await settle();
      s.setIn('opacity', id, 0.5); await settle();
      m.panBy([150, 80], { duration: 0 }); await settle();
      m.fire('click', { point: m.project(m.getCenter()), lngLat: m.getCenter(), originalEvent: new MouseEvent('click') });
      await settle();
      m.getStyle = own;
      const def = m.getStyle().layers.find((l) => l.id === styleId);
      return { copies, hidden, type: original.type === def.type,
               zooms: [original.minzoom, original.maxzoom], filter: JSON.stringify(original.filter ?? null) };"""}],
                  tmp_path)
    assert out["copies"] == 0 and out["hidden"] == "none" and out["type"], out
    assert out["zooms"][0] < out["zooms"][1] and out["filter"] != "", out
