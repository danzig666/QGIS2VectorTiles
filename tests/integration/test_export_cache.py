"""Export cache through the whole publication: a second export of an unchanged
project reuses every dataset and tile set; editing one layer's data (or its
labels) redoes only that layer; the result equals an export without the
cache (same tiles: same layers and features per tile), and the fidelity
diagnostics are the same; merging per-layer tile sets keeps every layer."""

import os
import sqlite3
import sys
import zlib

from qgis.core import QgsProcessingFeedback, QgsVectorLayerSimpleLabeling

from publishing.controller import cache_dir, export_local
from publishing.models import LayerConfig, PopupField, PublicationProfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_publishing_pipeline import EXTENT, _parcels  # noqa: E402  pylint: disable=wrong-import-position
from q2vt_fixtures import reset_project  # noqa: E402  pylint: disable=wrong-import-position


class Log(QgsProcessingFeedback):
    def __init__(self):
        super().__init__()
        self.lines = []

    def pushInfo(self, info):  # noqa: N802
        self.lines.append(info)

    def cache_line(self):
        return next((line for line in self.lines if "Export cache:" in line), "")


def _setup(tmp_path):
    project = reset_project()
    parcels = _parcels(str(tmp_path / "parcels.gpkg"))
    zones = _parcels(str(tmp_path / "zones.gpkg"))
    zones.setName("Övezetek")
    for layer in (parcels, zones):
        project.addMapLayer(layer)
    profile = PublicationProfile(title="Cache teszt", slug="cache-teszt", locale="hu")
    profile.view.min_zoom, profile.view.max_zoom = 11, 13
    profile.output.local_directory = str(tmp_path / "out")
    profile.layers = [LayerConfig(layer.id(), key_fields=["hrsz"], popup_fields=[PopupField("hrsz")])
                      for layer in (parcels, zones)]
    return project, profile, parcels, zones


def _tiles(result):
    """{(z, x, y): {layer name: sorted feature bytes}} of an export's MBTiles."""
    def varint(data, i):
        value = shift = 0
        while True:
            byte = data[i]
            i += 1
            value |= (byte & 0x7F) << shift
            shift += 7
            if byte < 0x80:
                return value, i

    def messages(data):
        i, out = 0, []
        while i < len(data):
            key, i = varint(data, i)
            if key & 7 == 2:
                size, i = varint(data, i)
                out.append((key >> 3, data[i:i + size]))
                i += size
            elif key & 7 == 0:
                value, i = varint(data, i)
                out.append((key >> 3, value))
        return out

    tiles = {}
    with sqlite3.connect(os.path.join(result.export_dir, "tiles.mbtiles")) as conn:
        for z, x, y, data in conn.execute("SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles"):
            layers = {}
            for field, layer in messages(zlib.decompress(data, 47)):
                if field != 3:
                    continue
                parts = messages(layer)
                name = next(v for f, v in parts if f == 1).decode()
                keys = [v for f, v in parts if f == 3]
                values = [v for f, v in parts if f == 4]
                features = []
                for f, body in parts:
                    if f == 2:
                        props = dict(messages(body))
                        tags = []
                        j, packed = 0, props.get(2, b"")
                        while j < len(packed):
                            v, j = varint(packed, j)
                            tags.append(v)
                        features.append((props.get(3), props.get(4), tuple(sorted(
                            (keys[tags[k]], values[tags[k + 1]]) for k in range(0, len(tags), 2)))))
                layers[name] = sorted(features)
            tiles[(z, x, y)] = layers
    return tiles


def _diagnostics(result):
    import json  # pylint: disable=import-outside-toplevel
    with open(os.path.join(result.export_dir, "fidelity_report.json"), encoding="utf-8") as handle:
        report = json.load(handle)
    return sorted((d["code"], d.get("component", "")) for d in report.get("diagnostics", []))


def test_unchanged_layers_are_reused_and_results_match(plugin, tmp_path):
    project, profile, parcels, zones = _setup(tmp_path)
    first_log = Log()
    first = export_local(project, profile, EXTENT, first_log)
    assert "Export cache: 0 of" in first_log.cache_line()
    assert os.path.isdir(cache_dir(profile))

    again_log = Log()
    again = export_local(project, profile, EXTENT, again_log)
    datasets = again_log.cache_line()
    assert " 0 of" not in datasets and "reused" in datasets
    hits, total = (int(v) for v in datasets.split("cache: ")[1].split(" datasets")[0].split(" of "))
    assert hits == total > 0
    assert _tiles(again) == _tiles(first)
    assert _diagnostics(again) == _diagnostics(first)
    assert again.feature_counts == first.feature_counts

    # Edit one feature of the zones layer (through QGIS, like a user would).
    zones.startEditing()
    feature = next(zones.getFeatures())
    zones.changeAttributeValue(feature.id(), zones.fields().indexOf("hrsz"), "EDITED-1")
    assert zones.commitChanges()
    # ... and the label expression of the parcels layer.
    settings = parcels.labeling().settings()
    settings.fieldName = "'Hrsz: ' || \"hrsz\""
    settings.isExpression = True
    parcels.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    edited_log = Log()
    edited = export_local(project, profile, EXTENT, edited_log)
    line = edited_log.cache_line()
    hits, total = (int(v) for v in line.split("cache: ")[1].split(" datasets")[0].split(" of "))
    assert 0 < hits < total                     # some redone, the rest reused
    assert any("Reading layer" in l and "Övezetek" in l for l in edited_log.lines)
    redone = [l for l in edited_log.lines if "Redone (" in l]
    assert any("layer data changed" in l and "Övezetek" in l for l in redone), redone
    assert any("style, labels or fields changed" in l for l in redone), redone

    profile.output.reuse_unchanged = False      # reference: no cache at all
    profile.output.local_directory = str(tmp_path / "fresh")
    fresh = export_local(project, profile, EXTENT, Log())
    assert _tiles(edited) == _tiles(fresh)
    assert _diagnostics(edited) == _diagnostics(fresh)
    flat = repr(_tiles(edited))
    assert "EDITED-1" in flat and "Hrsz: " in flat


def test_merge_keeps_every_layer(plugin, tmp_path):
    from q2vt_plugin.src.core.tiles_generator import merge_mbtiles  # pylint: disable=import-error

    def part(path, layer, tiles):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE metadata (name text, value text)")
            conn.execute("CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, "
                         "tile_data blob)")
            conn.execute("INSERT INTO metadata VALUES ('bounds', '1,2,3,4')")
            conn.execute("INSERT INTO metadata VALUES ('json', ?)",
                         ('{"vector_layers": [{"id": "%s"}], "tilestats": {"layers": [{"layer": "%s"}]}}'
                          % (layer, layer),))
            name = layer.encode()
            message = b"\x0a" + bytes([len(name)]) + name + b"\x28\x80\x20"  # name, extent 4096
            for z, x, y in tiles:
                body = b"\x1a" + bytes([len(message)]) + message
                compressor = zlib.compressobj(6, zlib.DEFLATED, 31)
                conn.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)",
                             (z, x, y, compressor.compress(body) + compressor.flush()))
    a, b, out = str(tmp_path / "a.mbtiles"), str(tmp_path / "b.mbtiles"), str(tmp_path / "out.mbtiles")
    part(a, "roads", [(1, 0, 0), (1, 1, 0)])
    part(b, "parcels", [(1, 1, 0), (1, 1, 1)])
    merge_mbtiles([a, b], out, 1, 1)
    with sqlite3.connect(out) as conn:
        tiles = {(z, x, y): zlib.decompress(d, 47) for z, x, y, d in conn.execute("SELECT * FROM tiles")}
        meta = dict(conn.execute("SELECT name, value FROM metadata"))
    assert set(tiles) == {(1, 0, 0), (1, 1, 0), (1, 1, 1)}
    assert b"roads" in tiles[(1, 1, 0)] and b"parcels" in tiles[(1, 1, 0)]
    assert b"parcels" not in tiles[(1, 0, 0)]
    assert '"roads"' in meta["json"] and '"parcels"' in meta["json"] and meta["bounds"].startswith("1.0")


def test_reused_dataset_under_a_new_zoom_range_keeps_its_features(plugin, tmp_path):
    """Owner report: after giving a layer's labels a scale range (1:4000 - 1:100)
    the web map had no labels. The label dataset was reused from the cache
    under its new name (its zoom range is in the name, not in its content),
    but the GeoPackage inside still had the old table name, and the tiles of
    that layer came out empty."""
    project, profile, parcels, _zones = _setup(tmp_path)
    export_local(project, profile, EXTENT, Log())

    settings = parcels.labeling().settings()
    settings.scaleVisibility = True
    settings.minimumScale, settings.maximumScale = 40000, 0  # labels from zoom 12 only
    parcels.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    log = Log()
    cached = export_local(project, profile, EXTENT, log)
    hits, total = (int(v) for v in log.cache_line().split("cache: ")[1].split(" datasets")[0].split(" of "))
    assert hits == total  # every dataset reused, the parcels' tiles redone

    profile.output.reuse_unchanged = False
    profile.output.local_directory = str(tmp_path / "fresh")
    fresh = export_local(project, profile, EXTENT, Log())
    tiles = _tiles(cached)
    assert tiles == _tiles(fresh)
    labels = {name for layers in tiles.values() for name, features in layers.items()
              if features and name.startswith(("l00t01", "l01t01"))}
    assert labels, sorted({name for layers in tiles.values() for name in layers})


def test_layers_sharing_a_geopackage_and_key_changes(plugin, tmp_path):
    """Two layers in one GeoPackage: editing one leaves the other cached (the
    edit stamps of the whole file made every layer of it look changed). A new
    feature key redoes only its layer, and says so."""
    from qgis.core import (QgsCoordinateTransformContext, QgsVectorFileWriter,  # pylint: disable=import-outside-toplevel
                           QgsVectorLayer)
    project, profile, parcels, zones = _setup(tmp_path)
    shared = str(tmp_path / "parcels.gpkg")  # parcels' file gets the zones table too
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName, options.layerName = "GPKG", "zones_table"
    options.actionOnExistingFile = QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
    assert QgsVectorFileWriter.writeAsVectorFormatV3(
        zones, shared, QgsCoordinateTransformContext(), options)[0] == QgsVectorFileWriter.NoError
    moved = QgsVectorLayer(f"{shared}|layername=zones_table", "Övezetek", "ogr")
    moved.setRenderer(zones.renderer().clone())
    project.removeMapLayer(zones.id())
    project.addMapLayer(moved)
    profile.layers[1].layer_id = moved.id()
    export_local(project, profile, EXTENT, Log())

    moved.startEditing()  # an edit in the zones table only
    feature = next(moved.getFeatures())
    moved.changeAttributeValue(feature.id(), moved.fields().indexOf("hrsz"), "EDITED-2")
    assert moved.commitChanges()
    profile.layers[0].key_fields = ["hrsz", "zone"]  # and a new key for the parcels
    log = Log()
    export_local(project, profile, EXTENT, log)
    redone = [l for l in log.lines if "Redone (" in l]
    assert any("layer data changed" in l and "Övezetek" in l and "Földrészletek" not in l for l in redone), redone
    assert any("feature key (unique id) changed" in l and "Földrészletek" in l
               and "Övezetek" not in l for l in redone), redone

    log = Log()  # nothing changed: everything reused
    export_local(project, profile, EXTENT, log)
    line = log.cache_line()
    hits, total = (int(v) for v in line.split("cache: ")[1].split(" datasets")[0].split(" of "))
    assert hits == total > 0, line



def test_memory_layer_reused_after_the_project_is_reopened(plugin, tmp_path):
    """Owner report: the cache was never used. QGIS gives a memory layer (e.g.
    one restored by the Memory Layer Saver plugin) a new random uid={...} in
    its source on every project load, and that uid was part of the key: every
    memory layer was redone after each restart. Its content decides now."""
    from qgis.core import QgsFeature, QgsProject, QgsVectorLayer  # pylint: disable=import-outside-toplevel
    project, profile, parcels, zones = _setup(tmp_path)
    fields = [f for f in parcels.fields() if f.name().lower() != "fid"]
    uri = f"Polygon?crs={parcels.crs().authid()}&" + "&".join(
        f"field={f.name()}:{'integer' if f.isNumeric() else 'string'}" for f in fields)
    memory = QgsVectorLayer(uri, "Memória", "memory")
    memory.setRenderer(parcels.renderer().clone())
    rows = [(f.geometry(), [f[x.name()] for x in fields]) for f in parcels.getFeatures()]

    def fill(layer):  # what the Memory Layer Saver plugin does after a load
        features = []
        for geometry, values in rows:
            feature = QgsFeature(layer.fields())
            feature.setGeometry(geometry)
            feature.setAttributes(values)
            features.append(feature)
        assert layer.dataProvider().addFeatures(features)[0]

    fill(memory)
    project.removeMapLayer(zones.id())
    project.addMapLayer(memory)
    profile.layers[1].layer_id = layer_id = memory.id()
    path = str(tmp_path / "terv.qgz")
    assert project.write(path)

    def reopen():  # same layer id, a new uid, no features until refilled
        project.clear()
        assert QgsProject.instance().read(path)
        layer = QgsProject.instance().mapLayer(layer_id)
        assert layer.featureCount() == 0
        fill(layer)
        return QgsProject.instance(), layer.source()

    project, source = reopen()
    export_local(project, profile, EXTENT, Log())
    project, again = reopen()
    assert again != source  # QGIS gave the memory layer a new uid
    log = Log()
    export_local(project, profile, EXTENT, log)
    line = log.cache_line()
    hits, total = (int(v) for v in line.split("cache: ")[1].split(" datasets")[0].split(" of "))
    assert hits == total > 0, [l for l in log.lines if "Redone (" in l]

    rows[0][1][[f.name() for f in fields].index("hrsz")] = "EDITED-3"  # a real change is still seen
    project, _ = reopen()
    log = Log()
    export_local(project, profile, EXTENT, log)
    assert any("layer data changed" in l and "Memória" in l for l in log.lines if "Redone (" in l), log.lines


def test_geopackage_stamps_read_where_the_uri_form_fails(plugin, tmp_path, monkeypatch):
    """Owner report: GeoPackage layers exported twice in a row were never
    reused, and the log said nothing. Their edit stamps were read through a
    hand-made ``file:`` URI; where SQLite refused it (Windows paths, network
    shares: "invalid uri authority") the layer was silently left uncached.
    Now a plain read-only connection is the fallback, and a layer that still
    cannot be cached is named in the log."""
    import sqlite3  # pylint: disable=import-outside-toplevel
    from q2vt_plugin.src.core import export_cache  # pylint: disable=import-error
    project, profile, parcels, _zones = _setup(tmp_path)
    expected = export_cache.source_fingerprint("ogr", parcels.source())
    assert expected
    connect = sqlite3.connect

    def refuse_uris(target, *args, **kwargs):
        if kwargs.get("uri"):
            raise sqlite3.OperationalError("invalid uri authority: naswork")
        return connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", refuse_uris)
    assert export_cache.source_fingerprint("ogr", parcels.source()) == expected
    export_local(project, profile, EXTENT, Log())
    log = Log()
    export_local(project, profile, EXTENT, log)
    hits, total = (int(v) for v in log.cache_line().split("cache: ")[1].split(" datasets")[0].split(" of "))
    assert hits == total > 0, [l for l in log.lines if "Redone (" in l]
    monkeypatch.setattr(sqlite3, "connect", connect)

    monkeypatch.setattr(export_cache, "source_fingerprint", lambda provider, uri: None)
    log = Log()
    export_local(project, profile, EXTENT, log)
    assert any("not cached: cannot read the file's change stamps" in l and "Földrészletek" in l
               for l in log.lines), [l for l in log.lines if "Redone (" in l]
