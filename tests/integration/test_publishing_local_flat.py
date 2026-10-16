"""Local export without versions (the default): the site goes straight into
<folder>/<slug>/, the next export replaces it (removing only files the
previous export wrote), and "keep versions" still builds releases/<id>/."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_publishing_pipeline import EXTENT, _parcels, _profile  # noqa: E402  pylint: disable=wrong-import-position


def test_local_export_is_one_site_replaced_each_time(tmp_path):
    from publishing.controller import FLAT_STATE, export_local
    from q2vt_fixtures import reset_project
    project = reset_project()
    parcels = _parcels(str(tmp_path / "parcels.gpkg"))
    project.addMapLayer(parcels)
    profile = _profile(parcels, tmp_path)
    assert not profile.output.local_versions  # the default
    first = export_local(project, profile, EXTENT)
    folder = first.publication_dir
    assert first.release.release_dir == folder and first.entry_path == os.path.join(folder, "index.html")
    assert not os.path.exists(os.path.join(folder, "releases"))
    assert not [n for n in os.listdir(folder) if n.startswith("bootstrap.")]
    manifest = json.load(open(os.path.join(folder, "manifest.json"), encoding="utf-8"))
    assert manifest["releaseId"] == first.release.release_id
    listed = json.load(open(os.path.join(folder, FLAT_STATE), encoding="utf-8"))["files"]
    assert "index.html" in listed and "data/map.pmtiles" in listed
    # A file of the previous export that the next one does not write is
    # removed; the user's own file stays.
    stale = os.path.join(folder, "assets", "old-export-file.js")
    open(stale, "w", encoding="utf-8").write("x")
    state = json.load(open(os.path.join(folder, FLAT_STATE), encoding="utf-8"))
    state["files"].append("assets/old-export-file.js")
    json.dump(state, open(os.path.join(folder, FLAT_STATE), "w", encoding="utf-8"))
    mine = os.path.join(folder, "notes.txt")
    open(mine, "w", encoding="utf-8").write("mine")
    second = export_local(project, profile, EXTENT)
    assert second.release.release_id != first.release.release_id
    assert not os.path.exists(stale) and os.path.exists(mine)
    manifest = json.load(open(os.path.join(folder, "manifest.json"), encoding="utf-8"))
    assert manifest["releaseId"] == second.release.release_id


def test_keep_versions_builds_releases(tmp_path):
    from publishing.controller import export_local
    from q2vt_fixtures import reset_project
    project = reset_project()
    parcels = _parcels(str(tmp_path / "parcels.gpkg"))
    project.addMapLayer(parcels)
    profile = _profile(parcels, tmp_path)
    profile.output.local_versions = True
    result = export_local(project, profile, EXTENT)
    assert os.path.isdir(os.path.join(result.publication_dir, "releases", result.release.release_id))
    assert os.path.exists(os.path.join(result.publication_dir, "current.json"))
