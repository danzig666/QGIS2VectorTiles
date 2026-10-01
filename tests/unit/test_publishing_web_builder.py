"""Immutable local web releases (PUB-06): layout, inventory, transport-only
style, atomic activation, failure safety (A18), retention, recovery, ZIP."""

import hashlib
import json
import os
import zipfile

import pytest

from publishing.bundle import style_semantic_diff
from publishing.errors import PublishingError
from publishing.models import PublicationProfile
from publishing.validation import compare_archives, vector_only_violations
from publishing.web_builder import (activate_release, apply_retention, build_release,
                                    list_releases, read_current, recover, write_zip)
from publishing_fixtures import fixture_bundle

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def bundle(tmp_path):
    return fixture_bundle(str(tmp_path / "export"))


@pytest.fixture
def profile():
    return PublicationProfile(title="Teszt <b>térkép</b> & ő", slug="teszt")


def _pub(tmp_path):
    return str(tmp_path / "web" / "Térkép mappa")


def test_release_layout_and_inventory(tmp_path, bundle, profile):
    result = build_release(bundle, profile, _pub(tmp_path))
    rel = result.release_dir
    assert result.activated and read_current(_pub(tmp_path))["releaseId"] == result.release_id
    for path in ("index.html", "manifest.json", "style.json", "release.json", "data/map.pmtiles",
                 "assets/app.mjs", "assets/transport.mjs", "assets/maplibre-gl.mjs",
                 "assets/maplibre-gl-worker.mjs", "assets/maplibre-gl-shared.mjs",
                 "assets/visible_labels.mjs", "assets/vendor/pmtiles.js", "assets/locales/hu.json",
                 "sprite/sprite.json", "sprite/sprite@2x.png", "glyphs/Noto Sans Regular/0-255.pbf",
                 "licenses/MAPLIBRE-LICENSE.txt", "licenses/PMTILES-LICENSE.txt",
                 "public-diagnostics.json"):
        assert os.path.exists(os.path.join(rel, *path.split("/"))), path
    release = json.load(open(os.path.join(rel, "release.json"), encoding="utf-8"))
    listed = {item["path"]: item for item in release["files"]}
    assert "release.json" not in listed and "data/map.pmtiles" in listed
    for path, item in listed.items():
        data = open(os.path.join(rel, *path.split("/")), "rb").read()
        assert item["size"] == len(data) and item["sha256"] == hashlib.sha256(data).hexdigest()
    assert listed["data/map.pmtiles"]["contentType"] == "application/vnd.pmtiles"
    assert listed["assets/app.mjs"]["contentType"].startswith("text/javascript")
    assert listed["index.html"]["cacheControl"].endswith("immutable")
    # Only allowlisted files: no source data, logs, reports or MBTiles.
    assert not [p for p in listed if p.endswith((".mbtiles", ".gpkg", ".log", ".qgs", ".txt"))
                and not p.startswith("licenses/")]
    # Same tiles as the MBTiles (A02 through the bundle).
    compare_archives(bundle.mbtiles_path, os.path.join(rel, "data", "map.pmtiles"))
    # The stable entry and bootstrap exist at the publication root.
    root_files = os.listdir(_pub(tmp_path))
    assert "index.html" in root_files and any(n.startswith("bootstrap.") for n in root_files)


def test_style_is_transport_only_and_vector_only(tmp_path, bundle, profile):
    result = build_release(bundle, profile, _pub(tmp_path))
    style = json.load(open(os.path.join(result.release_dir, "style.json"), encoding="utf-8"))
    assert style_semantic_diff(bundle.style, style, "q2vt_tiles") == []
    assert vector_only_violations(style) == [] and "osm" not in style["sources"]
    source = style["sources"]["q2vt_tiles"]
    assert "tiles" not in source and "url" not in source  # bound by the viewer from the manifest
    assert (source["minzoom"], source["maxzoom"]) == (0, 4)
    assert style["sprite"] == "sprite/sprite" and style["glyphs"] == "glyphs/{fontstack}/{range}.pbf"
    label = next(l for l in style["layers"] if l["id"] == "polygons_label")
    assert label["metadata"]["q2vt:visible-polygons"] == "polygons"
    # A changed paint expression is caught by the guard.
    broken = json.loads(json.dumps(style))
    broken["layers"][1]["paint"]["fill-pattern"] = "other"
    assert style_semantic_diff(bundle.style, broken, "q2vt_tiles")


def test_manifest_matches_schema_and_escapes_title(tmp_path, bundle, profile):
    jsonschema = pytest.importorskip("jsonschema")
    result = build_release(bundle, profile, _pub(tmp_path))
    manifest = json.load(open(os.path.join(result.release_dir, "manifest.json"), encoding="utf-8"))
    schemas = os.path.join(ROOT, "schemas", "publishing")
    for name, doc in (("manifest", manifest),
                      ("release", json.load(open(os.path.join(result.release_dir, "release.json"),
                                                 encoding="utf-8"))),
                      ("current", read_current(_pub(tmp_path)))):
        with open(os.path.join(schemas, f"{name}-v1.schema.json"), encoding="utf-8") as handle:
            jsonschema.validate(doc, json.load(handle))
    assert manifest["sources"][0] == {**manifest["sources"][0], "kind": "pmtiles", "tileType": "mvt",
                                      "href": "data/map.pmtiles", "minTileZoom": 0, "maxTileZoom": 4}
    page = open(os.path.join(result.release_dir, "index.html"), encoding="utf-8").read()
    assert "Teszt &lt;b&gt;térkép&lt;/b&gt; &amp; ő" in page and "<b>" not in page
    assert "Content-Security-Policy" in page and "unsafe-eval" not in page


def test_xyz_transport_uses_the_same_viewer(tmp_path, bundle, profile):
    result = build_release(bundle, profile, _pub(tmp_path), transport="xyz")
    manifest = json.load(open(os.path.join(result.release_dir, "manifest.json"), encoding="utf-8"))
    assert manifest["sources"][0]["kind"] == "xyz"
    assert os.path.exists(os.path.join(result.release_dir, "data", "tiles", "4", "3", "9.pbf"))
    assert not os.path.exists(os.path.join(result.release_dir, "data", "map.pmtiles"))


def test_failed_build_leaves_the_previous_release_current(tmp_path, bundle, profile):
    """A18 locally: validation failure after assembly -> nothing activated,
    no staging leftovers, old release intact."""
    first = build_release(bundle, profile, _pub(tmp_path))
    before = {p: open(os.path.join(first.release_dir, p), "rb").read()
              for p in ("index.html", "manifest.json", "style.json")}
    with pytest.raises(PublishingError) as error:
        build_release(bundle, profile, _pub(tmp_path), canaries=["00123/4"])  # in every tile
    assert error.value.code == "Q2VT_PUB_SECRET_LEAK"
    assert read_current(_pub(tmp_path))["releaseId"] == first.release_id
    assert list_releases(_pub(tmp_path)) == [first.release_id]
    assert not [n for n in os.listdir(os.path.join(_pub(tmp_path), "releases")) if n.startswith(".")]
    for path, data in before.items():
        assert open(os.path.join(first.release_dir, path), "rb").read() == data


def test_cancelled_build_leaves_nothing(tmp_path, bundle, profile):
    first = build_release(bundle, profile, _pub(tmp_path))
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3

    from publishing.progress import Progress
    from publishing.errors import Cancelled
    with pytest.raises(Cancelled):
        build_release(bundle, profile, _pub(tmp_path), feedback=Progress(cancel=cancel))
    assert list_releases(_pub(tmp_path)) == [first.release_id]
    assert read_current(_pub(tmp_path))["releaseId"] == first.release_id


def test_activation_conflict_and_rollback(tmp_path, bundle, profile):
    pub = _pub(tmp_path)
    first = build_release(bundle, profile, pub)
    second = build_release(bundle, profile, pub, activate=False)
    assert read_current(pub)["releaseId"] == first.release_id  # built, not active
    with pytest.raises(PublishingError) as error:
        activate_release(pub, profile.publication_id, second.release_id, expected_release="r-other")
    assert error.value.code == "Q2VT_PUB_ACTIVATION_CONFLICT"
    activate_release(pub, profile.publication_id, second.release_id, expected_release=first.release_id)
    assert read_current(pub)["releaseId"] == second.release_id
    activate_release(pub, profile.publication_id, first.release_id)  # rollback, no rebuild
    assert read_current(pub)["releaseId"] == first.release_id


def test_retention_never_removes_the_current_release(tmp_path, bundle, profile):
    pub = _pub(tmp_path)
    ids = [build_release(bundle, profile, pub, activate=(i == 0)).release_id for i in range(3)]
    removed = apply_retention(pub, keep=1)
    assert ids[0] not in removed and read_current(pub)["releaseId"] == ids[0]
    assert list_releases(pub) == [ids[0]]


def test_recovery_removes_interrupted_builds_only(tmp_path, bundle, profile):
    pub = _pub(tmp_path)
    first = build_release(bundle, profile, pub)
    os.makedirs(os.path.join(pub, "releases", ".staging-r-crashed"))
    open(os.path.join(pub, "current.json.tmp-1-abcd"), "w").close()
    removed = recover(pub)
    assert sorted(removed) == ["current.json.tmp-1-abcd", "releases/.staging-r-crashed"]
    assert read_current(pub)["releaseId"] == first.release_id


def test_offline_zip_has_the_release_at_its_root(tmp_path, bundle, profile):
    result = build_release(bundle, profile, _pub(tmp_path))
    path = write_zip(result.release_dir, str(tmp_path / "teszt.zip"))
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        assert "index.html" in names and "data/map.pmtiles" in names
        assert archive.getinfo("data/map.pmtiles").compress_type == zipfile.ZIP_STORED
    assert not os.path.exists(os.path.join(result.release_dir, "teszt.zip"))


def test_tampered_pointer_is_rejected(tmp_path, bundle, profile):
    pub = _pub(tmp_path)
    build_release(bundle, profile, pub)
    with open(os.path.join(pub, "current.json"), "w", encoding="utf-8") as handle:
        json.dump({"schemaVersion": 1, "releaseId": "r-20260101T000000Z-aaaaaaaa",
                   "entry": "../../etc/index.html", "manifest": "x"}, handle)
    with pytest.raises(PublishingError):
        read_current(pub)
