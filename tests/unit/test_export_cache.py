"""Export cache (core/export_cache.py): keys that only change when an input
changes, source fingerprints (files, GeoPackage edit stamps), stable
expression variables and style XML, the entries (datasets, tiles, bundles),
diagnostics replay and pruning."""

import json
import os
import sqlite3
import time

import pytest

import export_cache as ec
from fidelity.diagnostics import DiagnosticCollector


def _gpkg(path, stamp):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS gpkg_contents (table_name TEXT, last_change TEXT)")
        conn.execute("DELETE FROM gpkg_contents")
        conn.execute("INSERT INTO gpkg_contents VALUES ('parcels', ?)", (stamp,))


def test_keys_follow_their_inputs():
    a = ec.make_key("dataset", {"extent": [0, 0, 1, 1]}, ("x", 1), {"b": 2, "a": 1})
    assert a == ec.make_key("dataset", {"extent": [0, 0, 1, 1]}, ["x", 1], {"a": 1, "b": 2})
    assert a != ec.make_key("dataset", {"extent": [0, 0, 1, 2]}, ("x", 1), {"b": 2, "a": 1})


def test_source_fingerprint(tmp_path):
    shp = tmp_path / "zones.shp"
    for ext in (".shp", ".shx", ".dbf", ".prj"):
        (tmp_path / f"zones{ext}").write_bytes(b"x")
    first = ec.source_fingerprint("ogr", f"{shp}|layername=zones")
    assert first == ec.source_fingerprint("ogr", f"{shp}|layername=zones|subset=a")  # same files
    os.utime(tmp_path / "zones.dbf", ns=(1, 1))  # an attribute edit touches the .dbf only
    assert ec.source_fingerprint("ogr", str(shp)) != first
    assert ec.source_fingerprint("memory", "Point?crs=EPSG:4326") is None
    assert ec.source_fingerprint("postgres", "dbname='x' table=y") is None
    assert ec.source_fingerprint("ogr", str(tmp_path / "missing.gpkg")) is None
    gpkg = tmp_path / "parcels.gpkg"
    _gpkg(gpkg, "2026-01-01T00:00:00.000Z")
    stat = os.stat(gpkg)
    one = ec.source_fingerprint("ogr", f"{gpkg}|layername=parcels")
    (tmp_path / "parcels.gpkg-wal").write_bytes(b"")  # QGIS opened it: no change
    assert ec.source_fingerprint("ogr", str(gpkg)) == one
    _gpkg(gpkg, "2026-01-02T00:00:00.000Z")  # an edit (even if still in the -wal)
    os.utime(gpkg, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert ec.source_fingerprint("ogr", str(gpkg)) != one
    two = ec.source_fingerprint("ogr", str(gpkg))
    # QGIS opening and closing it rewrites the file (time, size) without an
    # edit: the same fingerprint (else every layer was redone every time).
    with sqlite3.connect(gpkg) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS qgis_scratch (x)")
        conn.execute("INSERT INTO qgis_scratch VALUES (1)")
    os.utime(gpkg, None)
    assert ec.source_fingerprint("ogr", str(gpkg)) == two


def test_miss_reasons():
    before = {"code": "a", "context": "c", "source": "s", "rule": "r"}
    assert ec.miss_reasons({}, before, {}, {}) == ["first export into this folder (or a new layer / rule)"]
    assert ec.miss_reasons(before, {**before, "source": "s2"}, {}, {}) == ["layer data changed"]
    # After an update only that is said (owner report: the settings of the
    # older version were listed as changes, e.g. variables it no longer keeps).
    assert ec.miss_reasons(before, {**before, "rule": "r2", "code": "b", "context": "c2"},
                           {"variables": {"abel_terv": "x"}}, {"variables": {}}) == [
        "plugin, QGIS or GDAL updated"]
    reasons = ec.miss_reasons(before, {**before, "context": "c2"},
                              {"extent": [0, 0, 1, 1], "variables": {"x": 1}},
                              {"extent": [0, 0, 2, 2], "variables": {"x": 2}})
    assert reasons == ["export settings changed (extent, @x)"]


def test_stable_values_and_canonical_xml():
    class Layer:
        def id(self):
            return "parcels_1"
    assert ec.stable_value([1, "a", None, Layer()]) == [1, "a", None, "id:parcels_1"]
    assert ec.stable_value(object()) is ec.UNSTABLE
    assert ec.stable_value({"k": [object()]}) is ec.UNSTABLE
    one = '<s b="1" a="2"><o v="&lt;symbol y=&quot;1&quot; x=&quot;2&quot;/&gt;"/></s>'
    two = '<s a="2" b="1"><o v="&lt;symbol x=&quot;2&quot; y=&quot;1&quot;/&gt;"/></s>'
    assert ec.canonical_xml(one) == ec.canonical_xml(two)
    assert ec.canonical_xml(one) != ec.canonical_xml(one.replace('b="1"', 'b="3"'))


def test_dataset_tiles_and_bundle_entries(tmp_path):
    cache = ec.ExportCache(str(tmp_path / "cache"))
    source = tmp_path / "out.gpkg"
    source.write_bytes(b"dataset")
    cache.put_dataset("k1", str(source), [{"code": "Q2VT_DDP_NO_EMITTER", "severity": "warning",
                                           "message": "m", "component": "old_name"}])
    cache.put_dataset("k2", None, [])
    target = tmp_path / "copy.gpkg"
    meta = cache.get_dataset("k1", str(target))
    assert meta["empty"] is False and target.read_bytes() == b"dataset"
    assert cache.get_dataset("k2", str(tmp_path / "none.gpkg"))["empty"] is True
    assert not (tmp_path / "none.gpkg").exists()
    assert cache.get_dataset("k3", str(tmp_path / "x.gpkg")) is None
    collector = DiagnosticCollector()
    ec.replay_diagnostics(collector, meta["diagnostics"], "old_name", "new_name")
    assert [d.component for d in collector.items] == ["new_name"]
    tiles = tmp_path / "t.mbtiles"
    tiles.write_bytes(b"tiles")
    assert cache.get_tiles("t1") is None
    stored = cache.put_tiles("t1", str(tiles))
    assert cache.get_tiles("t1") == stored
    shard = tmp_path / "p-00.json"
    shard.write_text("[]")
    cache.put_bundle("parcels", "b1", {"records": 3}, {"parcels/p-00.json": str(shard)})
    meta, folder = cache.get_bundle("parcels", "b1")
    assert meta["records"] == 3 and meta["_files"] == ["parcels/p-00.json"]
    assert os.path.exists(os.path.join(folder, "parcels", "p-00.json"))
    os.remove(os.path.join(folder, "parcels", "p-00.json"))  # partly pruned: a miss
    assert cache.get_bundle("parcels", "b1") is None
    assert cache.hits == {"datasets": 2, "tiles": 1} and cache.misses == {"datasets": 1, "tiles": 1}


def test_prune_by_age_and_size(tmp_path):
    cache = ec.ExportCache(str(tmp_path / "cache"))
    blob = tmp_path / "blob"
    blob.write_bytes(b"x" * 1000)
    for key in ("old", "mid", "new"):
        cache.put_tiles(key, str(blob))
    now = time.time()
    for key, age_days in (("old", 40), ("mid", 2), ("new", 0)):
        path = cache._path("tiles", key, ".mbtiles")  # pylint: disable=protected-access
        os.utime(path, (now - age_days * 86400, now - age_days * 86400))
    assert cache.prune(max_bytes=10_000) == 1          # unused for 30+ days
    assert cache.get_tiles("old") is None and cache.get_tiles("mid")
    assert cache.prune(max_bytes=1500) == 1            # over budget: least recently used
    assert cache.size() == 1000
    cache.clear()
    assert cache.size() == 0
