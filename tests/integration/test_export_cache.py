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
