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
