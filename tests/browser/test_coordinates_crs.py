"""Coordinate readout in the project's CRS: EOV (own transformation) only
for EOV projects; any other projected CRS through proj4js (loaded only
then), checked against QGIS's own transformation; a WGS 84 project shows
WGS 84 only."""

import json
import os
import subprocess

import pytest
from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsPointXY, QgsProject

from publishing.controller import export_local
from publishing.preview_server import PreviewServer

HERE = os.path.dirname(os.path.abspath(__file__))

READ = """
document.getElementById('q2vt-tab-tools').click();
for (let i = 0; i < 50; i++) {
  const n = document.querySelectorAll('#q2vt-pane-tools .q2vt-card')[0].querySelectorAll('.q2vt-field-label').length;
  if (n >= 2 || i > 20) break;
  await new Promise((r) => setTimeout(r, 100));
}
const card = document.querySelectorAll('#q2vt-pane-tools .q2vt-card')[0];
const c = q2vtViewer.map.getCenter();
return { labels: [...card.querySelectorAll('.q2vt-field-label')].map((e) => e.textContent),
         values: [...card.querySelectorAll('.q2vt-tool-output')].map((e) => e.textContent),
         center: [c.lng, c.lat], proj4: typeof window.proj4 };
"""


def _numbers(text):
    return [float(part.replace(" ", "").replace(" ", "").replace(" ", "").replace(",", "."))
            for part in text.split("; ")]


@pytest.fixture(scope="module")
def publish(tmp_path_factory):
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_publishing_pipeline import EXTENT, CANARY, _parcels, _profile  # pylint: disable=import-error
    from q2vt_fixtures import reset_project

    def run(authid):
        base = tmp_path_factory.mktemp(authid.replace(":", "_"))
        parcels = _parcels(str(base / "parcels.gpkg"))
        project = reset_project()
        project.addMapLayer(parcels)
        project.setCrs(QgsCoordinateReferenceSystem(authid))
        profile = _profile(parcels, base)
        profile.layers[0].initially_visible = True
        result = export_local(project, profile, EXTENT, canaries=[CANARY])
        manifest = json.load(open(os.path.join(result.release.release_dir, "manifest.json"), encoding="utf-8"))
        server = PreviewServer(os.path.dirname(result.publication_dir)).start()
        path = tmp_path_factory.mktemp("actions") / "read.json"
        path.write_text(json.dumps([{"eval": READ}]))
        out = subprocess.run(["node", "interact.mjs", server.url(f"{profile.slug}/index.html"), str(path),
                              "1000", "700"], capture_output=True, text=True, cwd=HERE, timeout=300)
        server.stop()
        assert out.returncode == 0, out.stderr[-3000:]
        page = json.loads(out.stdout.strip().splitlines()[-1])
        assert not page["pageErrors"], page
        return manifest, page["results"][0]
    return run


def test_eov_project_shows_eov(publish):
    manifest, card = publish("EPSG:23700")
    assert manifest["crs"]["authid"] == "EPSG:23700" and "+proj=somerc" in manifest["crs"]["proj"]
    assert len(card["labels"]) == 2 and "EOV" in card["labels"][1]
    assert card["proj4"] == "undefined"  # own transformation, proj4js not loaded


def test_other_projected_crs_uses_its_own_coordinates(publish):
    manifest, card = publish("EPSG:32634")  # UTM zone 34N
    assert manifest["crs"]["authid"] == "EPSG:32634" and not manifest["crs"]["geographic"]
    assert len(card["labels"]) == 2 and "EPSG:32634" in card["labels"][1] and "EOV" not in card["labels"][1]
    assert card["proj4"] == "function"
    transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:4326"),
                                       QgsCoordinateReferenceSystem("EPSG:32634"), QgsProject.instance())
    expected = transform.transform(QgsPointXY(*card["center"]))
    easting, northing = _numbers(card["values"][1])
    assert easting == pytest.approx(expected.x(), abs=0.05) and northing == pytest.approx(expected.y(), abs=0.05)


def test_wgs84_project_shows_wgs84_only(publish):
    manifest, card = publish("EPSG:4326")
    assert manifest["crs"]["geographic"] and len(card["labels"]) == 1
    assert card["proj4"] == "undefined"
