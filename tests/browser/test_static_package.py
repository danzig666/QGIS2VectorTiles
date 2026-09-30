"""The static web package works from a sub-directory of a plain web server."""

import json
import os
import subprocess
import sys

import pytest
from qgis.core import QgsProcessingFeedback

from q2vt_fixtures import reset_project

HERE = os.path.dirname(os.path.abspath(__file__))
pytestmark = pytest.mark.browser


def test_static_package_renders_from_a_subdirectory(tmp_path):
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-error
    sys.path.insert(0, os.path.join(os.path.dirname(HERE), "integration"))
    from test_end_to_end import EXTENT, _hatched_labelled_layer  # pylint: disable=import-error
    layer = _hatched_labelled_layer(str(tmp_path / "zoning.gpkg"))
    reset_project(layer)
    out = tmp_path / "site" / "maps"
    out.mkdir(parents=True)
    export_dir = QGIS2VectorTiles(min_zoom=12, max_zoom=14, extent=EXTENT, output_dir=str(out),
                                  feedback=QgsProcessingFeedback(), serve=False,
                                  background_type=2, static_package=True
                                  ).convert_project_to_vector_tiles()
    web = os.path.join(export_dir, "web")
    style = json.load(open(os.path.join(web, "style.json"), encoding="utf-8"))
    assert all("localhost" not in json.dumps(v) for v in (style["sources"], style.get("sprite"),
                                                          style.get("glyphs")))
    assert not [n for n in os.listdir(export_dir) if ".tmp-" in n]  # atomic
    port = 9311
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(port), "--bind",
                               "127.0.0.1", "--directory", str(tmp_path / "site")],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        relative = os.path.relpath(web, tmp_path / "site").replace(os.sep, "/")
        run = subprocess.run(["node", os.path.join(HERE, "static_check.mjs"),
                              f"http://127.0.0.1:{port}/{relative}/index.html",
                              str(tmp_path / "static.png")],
                             capture_output=True, text=True, cwd=HERE, timeout=120)
    finally:
        server.kill()
    assert run.returncode == 0, run.stderr
    result = json.loads(run.stdout.strip().splitlines()[-1])
    assert result["rendered"] > 0
    # Tiles that were never generated are plain 404s, which MapLibre treats as empty.
    failed = [u for u in result["failed"] if not (u.startswith("404") and u.endswith(".pbf"))]
    assert not result["errors"] and not failed, result
