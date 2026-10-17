"""The viewer's expected MapLibre version, the one recorded in release.json
and the bundled build agree: a mismatch shows every visitor the "polygon
labels on the visible part are not available" warning."""

import json
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as handle:
        return handle.read()


def test_maplibre_version_is_the_same_everywhere():
    built = json.loads(_read("tools", "maplibre", "package.json"))["dependencies"]["maplibre-gl"]
    expected = re.search(r'EXPECTED_MAPLIBRE = "([^"]+)"', _read("resources", "web_viewer", "app.mjs"))
    recorded = re.search(r'MAPLIBRE_VERSION = "([^"]+)"', _read("src", "publishing", "web_builder.py"))
    assert expected.group(1) == recorded.group(1) == built
    assert f'"{built}"' in _read("resources", "ml_viewer", "maplibre-gl.mjs")
