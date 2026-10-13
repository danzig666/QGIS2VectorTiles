"""The "my location" button is an option, off by default; a position outside
the map's extent only shows a message (the map does not fly off to an empty
place), one inside goes there."""

import json
import os
import sys

import pytest

from publishing.controller import export_local
from publishing.preview_server import PreviewServer

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_publishing_pipeline import EXTENT, _parcels, _profile  # pylint: disable=import-error
    from q2vt_fixtures import reset_project
    base = tmp_path_factory.mktemp("locate")
    project = reset_project()
    parcels = _parcels(str(base / "parcels.gpkg"))
    project.addMapLayer(parcels)
    profile = _profile(parcels, base)
    assert profile.interaction.geolocation is False  # off by default
    profile.interaction.geolocation = True
    result = export_local(project, profile, EXTENT)
    manifest = next(os.path.join(root, "manifest.json") for root, _, files
                    in os.walk(result.publication_dir) if "manifest.json" in files)
    with PreviewServer(os.path.dirname(result.publication_dir)) as server:
        yield server.url(f"{profile.slug}/index.html"), manifest


def _run(url, actions, tmp_path):
    sys.path.insert(0, HERE)
    from test_web_viewer_views import _run as run  # pylint: disable=import-error
    return run(url, actions, tmp_path)


LOCATE = """const v = q2vtViewer, m = v.map, b = v.manifest.view.bounds;
  const at = %s;
  navigator.geolocation.getCurrentPosition = (ok) => ok({ coords: { longitude: at[0], latitude: at[1] } });
  const before = m.getCenter();
  document.querySelector('.maplibregl-ctrl-geolocate').click();
  await new Promise((r) => setTimeout(r, 2500));
  const after = m.getCenter(), bar = document.getElementById('q2vt-warning');
  return { moved: Math.abs(after.lng - before.lng) + Math.abs(after.lat - before.lat) > 1e-6,
           near: Math.abs(after.lng - at[0]) < 1e-4 && Math.abs(after.lat - at[1]) < 1e-4,
           message: !bar.hidden && bar.innerText.trim() };"""


def test_outside_the_extent_only_a_message_inside_the_map_goes_there(site, tmp_path):
    url, _ = site
    outside, inside = _run(url, [
        {"eval": LOCATE % "[b[2] + 1, b[3] + 1]"},
        {"eval": LOCATE % "[(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]"},
    ], tmp_path)
    assert not outside["moved"] and outside["message"], outside
    assert inside["moved"] and inside["near"], inside


def test_no_button_when_the_option_is_off(site, tmp_path):
    url, manifest_path = site
    original = open(manifest_path, encoding="utf-8").read()
    manifest = json.loads(original)
    manifest["interaction"]["geolocation"] = False
    try:
        json.dump(manifest, open(manifest_path, "w", encoding="utf-8"))
        (count,) = _run(url, [{"eval": "return document.querySelectorAll('.maplibregl-ctrl-geolocate').length;"}],
                        tmp_path)
    finally:
        open(manifest_path, "w", encoding="utf-8").write(original)
    assert count == 0
