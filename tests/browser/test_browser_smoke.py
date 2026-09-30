"""Export a fixture project and render it in a pinned headless browser.

Checks (separately from any image comparison): style-spec validity, browser
console errors, MapLibre errors, missing sprite images and whether each
vector style layer actually rendered features. A screenshot is kept in
``tests/browser/artifacts`` for manual inspection.
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

import pytest
from qgis.core import (QgsProcessingFeedback, QgsRectangle, QgsCoordinateReferenceSystem,
                       QgsCoordinateTransform, QgsProject)

from q2vt_fixtures import reset_project

HERE = os.path.dirname(os.path.abspath(__file__))
ARTIFACTS = os.path.join(HERE, "artifacts")
PORT = 9000  # the style's URLs use the plugin's configured port (_PORT)
EXTENT = QgsRectangle(2119000, 6019000, 2123000, 6023000)

pytestmark = pytest.mark.browser


def _export(tmp_path, layer):
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-error
    reset_project(layer)
    out = tmp_path / "out"
    out.mkdir()
    exporter = QGIS2VectorTiles(min_zoom=10, max_zoom=14, extent=EXTENT, output_dir=str(out),
                                feedback=QgsProcessingFeedback(), serve=False,
                                background_type=2)  # offline background
    return exporter.convert_project_to_vector_tiles()


def _serve(export_dir):
    proc = subprocess.Popen([sys.executable, os.path.join(export_dir, "utils", "tiles_server.py"),
                             "--port", str(PORT)], stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://localhost:{PORT}/style/style.json", timeout=1)
            return proc
        except OSError:
            time.sleep(0.1)
    proc.kill()
    raise RuntimeError("tile server did not start")


def test_exported_package_renders_in_browser(tmp_path):
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_end_to_end import _hatched_labelled_layer  # pylint: disable=import-error
    export_dir = _export(tmp_path, _hatched_labelled_layer(str(tmp_path / "zoning.gpkg")))
    style_path = os.path.join(export_dir, "style", "style.json")

    validation = subprocess.run(["node", os.path.join(HERE, "validate_style.mjs"), style_path],
                                capture_output=True, text=True, cwd=HERE)
    assert validation.returncode == 0, validation.stdout + validation.stderr

    transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:3857"),
                                       QgsCoordinateReferenceSystem("EPSG:4326"),
                                       QgsProject.instance())
    center = transform.transform(EXTENT.center())
    os.makedirs(ARTIFACTS, exist_ok=True)
    server = _serve(export_dir)
    try:
        results = {}
        # 13: inside the archive; 15.5: overzoom above the archive's max zoom 14.
        for zoom in ("13", "15.5"):
            shot = os.path.join(ARTIFACTS, f"smoke_z{zoom}.png")
            run = subprocess.run(["node", os.path.join(HERE, "smoke.mjs"), export_dir, str(PORT),
                                  str(center.x()), str(center.y()), zoom, shot],
                                 capture_output=True, text=True, cwd=HERE, timeout=120)
            assert run.returncode == 0, run.stderr
            results[zoom] = json.loads(run.stdout.strip().splitlines()[-1])
    finally:
        server.kill()
    for result in results.values():
        _check_render(result)


def _check_render(result):
    assert result["mapErrors"] == [], result
    assert result["missingImages"] == [], result
    # The browser's automatic favicon request is the only tolerated 404.
    errors = [e for e in result["consoleErrors"] if not e.endswith("/favicon.ico]")]
    assert errors == [], result
    # Every vector layer (fill, outline, hatch, label) drew something.
    assert set(result["layers"]) <= set(result["rendered"]), result
