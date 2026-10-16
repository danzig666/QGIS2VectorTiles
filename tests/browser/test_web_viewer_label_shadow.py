"""Label shadows in the viewer: the shadow copy joins its label's placement
group, so it is drawn at the same point as the label, under it, moved by the
shadow's offset."""

import os
import sys

import pytest

from publishing.controller import export_local
from publishing.preview_server import PreviewServer

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_label_shadows import _labelled  # pylint: disable=import-error
    from test_publishing_pipeline import EXTENT, _parcels, _profile  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    base = tmp_path_factory.mktemp("shadows")
    project = reset_project()
    parcels = _parcels(str(base / "parcels.gpkg"))
    _labelled(parcels)
    project.addMapLayer(parcels)
    profile = _profile(parcels, base)
    profile.layers[0].initially_visible = True
    profile.interaction.panel_closed = True
    result = export_local(project, profile, EXTENT)
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        yield server.url(f"{profile.slug}/index.html")


PLACED = """const m = q2vtViewer.map;
  await new Promise((r) => setTimeout(r, 1500));
  const shadows = m.getStyle().layers.filter((l) => l.metadata && l.metadata['q2vt:shadow-of']);
  const out = [];
  for (const s of shadows) {
    const label = s.metadata['q2vt:shadow-of'];
    const ids = m.getStyle().layers.map((l) => l.id);
    const key = (f) => JSON.stringify(f.geometry.coordinates.map((c) => c.toFixed(6)));
    const a = m.queryRenderedFeatures({ layers: [s.id] }).map(key).sort();
    const b = m.queryRenderedFeatures({ layers: [label] }).map(key).sort();
    out.push({ below: ids.indexOf(s.id) < ids.indexOf(label), same: JSON.stringify(a) === JSON.stringify(b),
               count: b.length, source: m.getLayer(s.id).source === m.getLayer(label).source });
  }
  return out;"""


def test_shadows_are_drawn_where_their_labels_are(site, tmp_path):
    sys.path.insert(0, HERE)
    from test_web_viewer_views import _run  # pylint: disable=import-error
    (placed,) = _run(site, [{"eval": PLACED}], tmp_path)
    assert placed, "no shadow layers"
    assert any(p["count"] for p in placed)
    for p in placed:
        assert p["below"] and p["source"] and p["same"], p


@pytest.fixture(scope="module")
def towns(tmp_path_factory):
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_label_shadows import _towns  # pylint: disable=import-error
    from test_publishing_pipeline import EXTENT  # pylint: disable=import-error
    from publishing.models import LayerConfig, PublicationProfile
    from q2vt_fixtures import reset_project
    base = tmp_path_factory.mktemp("town_shadows")
    project = reset_project()
    # Three towns: two at the same point, so their labels collide (MapLibre hides one).
    layer = _towns(base, names=("Alpha town", "Beta town", "Gamma"), spacing=0)
    from qgis.core import QgsGeometry  # pylint: disable=import-outside-toplevel
    layer.startEditing()
    for feature, x in zip(layer.getFeatures(), (2120500, 2120500, 2122000)):
        layer.changeGeometry(feature.id(), QgsGeometry.fromWkt(f"POINT({x} 6021000)"))
    layer.commitChanges()
    project.addMapLayer(layer)
    profile = PublicationProfile(title="Towns", slug="towns", locale="en")
    profile.view.min_zoom, profile.view.max_zoom = 11, 14
    profile.output.local_directory = str(base / "out")
    profile.layers = [LayerConfig(layer.id(), initially_visible=True)]
    profile.interaction.panel_closed = True
    result = export_local(project, profile, EXTENT)
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        yield server.url(f"{profile.slug}/index.html")


SYNCED = """const m = q2vtViewer.map;
  await new Promise((r) => setTimeout(r, 2000));
  const shadow = m.getStyle().layers.find((l) => l.metadata && l.metadata['q2vt:shadow-state']);
  const label = shadow.metadata['q2vt:shadow-of'];
  const state = shadow.metadata['q2vt:shadow-state'];
  const shown = m.queryRenderedFeatures({ layers: [label] }).map((f) => String(f.id)).sort();
  const lit = m.queryRenderedFeatures({ layers: [shadow.id] })
    .filter((f) => m.getFeatureState({ source: shadow.source, sourceLayer: shadow['source-layer'], id: f.id })[state])
    .map((f) => String(f.id)).sort();
  const all = m.queryRenderedFeatures({ layers: [shadow.id] }).map((f) => String(f.id)).sort();
  m.setLayoutProperty(label, 'visibility', 'none');
  await new Promise((r) => setTimeout(r, 800));
  const litHidden = m.queryRenderedFeatures({ layers: [shadow.id] })
    .filter((f) => m.getFeatureState({ source: shadow.source, sourceLayer: shadow['source-layer'], id: f.id })[state]).length;
  return { shown, lit, all, litHidden };"""


def test_point_label_shadows_follow_their_labels(towns, tmp_path):
    sys.path.insert(0, HERE)
    from test_web_viewer_views import _run  # pylint: disable=import-error
    (out,) = _run(towns, [{"eval": SYNCED}], tmp_path)
    assert len(set(out["all"])) == 3          # a shadow copy at every town
    assert 1 <= len(out["shown"]) < 3, out    # the colliding pair: one label hidden
    assert out["lit"] == out["shown"]         # shadows only where labels are shown
    assert out["litHidden"] == 0              # label switched off: no shadow left
