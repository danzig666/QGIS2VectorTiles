"""Vector-only contract (A01), path safety and leak scanning (PUB-01/04)."""

import json

import pytest

from publishing.errors import PublishingError
from publishing.validation import (assert_vector_only, check_public_file, required_source_layers,
                                   safe_relative_path, scan_bundle, scan_text_for_leaks,
                                   strip_raster_background, vector_only_violations,
                                   visible_polygon_helpers)

STYLE = {
    "version": 8,
    "sources": {
        "q2vt_tiles": {"type": "vector", "tiles": ["tiles/{z}/{x}/{y}.pbf"]},
        "osm": {"type": "raster", "tiles": ["https://tile.example/{z}/{x}/{y}.png"]},
    },
    "layers": [
        {"id": "osm-background", "type": "raster", "source": "osm"},
        {"id": "fill", "type": "fill", "source": "q2vt_tiles", "source-layer": "a",
         "paint": {"fill-pattern": "hatch_1"}},
        {"id": "label", "type": "symbol", "source": "q2vt_tiles", "source-layer": "a_lbl",
         "metadata": {"q2vt:visible-polygons": "a_lbl_vp"}},
    ],
}


@pytest.mark.parametrize("kind", ["raster", "raster-dem", "image", "video", "canvas", "geojson"])
def test_non_vector_sources_are_refused(kind):
    style = {"sources": {"s": {"type": kind}}, "layers": []}
    assert vector_only_violations(style)
    with pytest.raises(PublishingError) as error:
        assert_vector_only(style)
    assert error.value.code == "Q2VT_PUB_RASTER_SOURCE"


def test_patterns_sprites_and_runtime_helpers_are_allowed():
    style = json.loads(json.dumps(STYLE))
    removed = strip_raster_background(style)
    assert removed == ["osm"] and [l["id"] for l in style["layers"]] == ["fill", "label"]
    assert vector_only_violations(style) == []  # fill-pattern sprites are styling assets
    style["sources"]["q2vt_visible_a_lbl_vp"] = {"type": "geojson", "data": {}}
    assert vector_only_violations(style, runtime=True) == []
    assert vector_only_violations(style)  # never in a published file


def test_visible_polygon_helper_datasets_are_required():
    assert visible_polygon_helpers(STYLE) == {"a_lbl_vp": ["label"]}
    assert required_source_layers(STYLE) == ["a", "a_lbl", "a_lbl_vp"]


@pytest.mark.parametrize("path", ["../x", "/etc/passwd", "a/../../b", "C:/x", "a//b", "a\\b",
                                  "https://x/y", "a/\x01", "", "./a"])
def test_unsafe_paths(path):
    with pytest.raises(PublishingError):
        safe_relative_path(path)


def test_safe_paths_keep_unicode_and_spaces():
    assert safe_relative_path("glyphs/Noto Sans Bold/0-255.pbf") == "glyphs/Noto Sans Bold/0-255.pbf"
    assert safe_relative_path("legend/ő ű.png") == "legend/ő ű.png"


@pytest.mark.parametrize("name", ["project.qgs", "data/x.gpkg", "export_log.txt", "a.shp",
                                  "fidelity_report.json", "tiles.mbtiles", "x.geojson"])
def test_private_and_source_files_are_forbidden(name):
    with pytest.raises(PublishingError) as error:
        check_public_file(name)
    assert error.value.code == "Q2VT_PUB_FORBIDDEN_FILE"


def test_leak_scan(tmp_path):
    assert scan_text_for_leaks('{"x": "/home/user/data.gpkg"}')
    assert scan_text_for_leaks("C:\\Users\\me\\map.qgz")
    assert scan_text_for_leaks("key=AKIAEXAMPLESECRET", ["AKIAEXAMPLESECRET"])
    assert not scan_text_for_leaks('{"title": "Tájkép / 00123/4"}')
    (tmp_path / "manifest.json").write_text('{"t": "CANARY-777"}', encoding="utf-8")
    (tmp_path / "ok.json").write_text('{"t": "fine"}', encoding="utf-8")
    problems = scan_bundle(str(tmp_path), ["manifest.json", "ok.json"], canaries=["CANARY-777"])
    assert problems == ["manifest.json: canary value"]
