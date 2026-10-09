"""4.28 export speed-ups that are not about the numbers in the tiles (those are
compared in test_memory_chains / test_marker_points): the datasets read and
pruned with SQLite give what QGIS / OGR give, a network output folder keeps
its work files on this computer (logs copied next to the output), and the
export log names the slowest layers."""

import os
import sqlite3
import sys
import tempfile

from osgeo import ogr
from qgis.core import (QgsCoordinateTransformContext, QgsFeature, QgsField, QgsFields, QgsGeometry,
                       QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes, QgsCoordinateReferenceSystem)
from qgis.PyQt.QtCore import QVariant

from q2vt_fixtures import reset_project

HERE = os.path.dirname(os.path.abspath(__file__))


def _gpkg(path):
    fields = QgsFields()
    for name, kind in (("name", QVariant.String), ("q2vt_property_a", QVariant.Double),
                       ("q2vt_property_b", QVariant.Int), ("keep", QVariant.Int)):
        fields.append(QgsField(name, kind))
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    writer = QgsVectorFileWriter.create(path, fields, QgsWkbTypes.Point,
                                        QgsCoordinateReferenceSystem("EPSG:3857"),
                                        QgsCoordinateTransformContext(), options)
    features = []
    for number in range(7):
        feature = QgsFeature(fields)
        feature.setAttributes([f"n{number}", number / 3, number, 10 * number])
        feature.setGeometry(QgsGeometry.fromWkt(f"POINT({number} {2 * number})"))
        features.append(feature)
    writer.addFeatures(features)
    del writer


def test_sqlite_dataset_info_and_pruning_match_ogr(plugin, tmp_path):
    from q2vt_plugin.src.core.datasets import ExportedDataset, drop_fields, gpkg_info  # pylint: disable=import-error
    path = str(tmp_path / "l00t00d01.gpkg")
    _gpkg(path)
    layer = QgsVectorLayer(path, "l", "ogr")
    info = gpkg_info(path)
    assert info.count == layer.featureCount() == 7
    assert info.fields == [f.name() for f in layer.fields() if f.name() != "fid"]
    assert drop_fields(path, info.table, ["q2vt_property_a", "q2vt_property_b"])
    dataset = ogr.Open(path)
    ogr_layer = dataset.GetLayer(0)
    definition = ogr_layer.GetLayerDefn()
    assert [definition.GetFieldDefn(i).GetName() for i in range(definition.GetFieldCount())] == ["name", "keep"]
    assert [(f.GetField("name"), f.GetField("keep"), f.GetGeometryRef().GetX()) for f in ogr_layer] == \
        [(f"n{n}", 10 * n, float(n)) for n in range(7)]
    dataset = None
    assert gpkg_info(str(tmp_path / "missing.gpkg")) is None
    handle = ExportedDataset(path, "l00t00d01", 7, lambda: QgsVectorLayer(path, "l00t00d01", "ogr"))
    assert (handle.source(), handle.name(), handle.featureCount()) == (path, "l00t00d01", 7)
    assert handle.crs().authid() == "EPSG:3857"  # anything else opens the layer


def test_a_network_output_folder_works_on_this_computer(plugin, tmp_path, monkeypatch):
    from q2vt_plugin.src.publishing import controller  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.models import LayerConfig, PublicationProfile  # pylint: disable=import-error
    sys.path.insert(0, HERE)
    from test_publishing_pipeline import EXTENT, _parcels  # pylint: disable=import-error
    monkeypatch.setenv("Q2VT_LOCAL_WORK", "1")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "local"))
    project = reset_project()
    parcels = _parcels(str(tmp_path / "parcels.gpkg"))
    project.addMapLayer(parcels)
    profile = PublicationProfile(title="Hálózat", slug="halozat", locale="hu")
    profile.view.min_zoom, profile.view.max_zoom = 12, 13
    output = tmp_path / "share"
    profile.output.local_directory = str(output)
    profile.layers = [LayerConfig(parcels.id())]
    publication_dir, work_dir = controller.publication_dirs(profile)
    assert work_dir.startswith(str(tmp_path / "local")) and publication_dir == str(output / "halozat")
    assert controller.cache_dir(profile).startswith(str(tmp_path / "local"))
    result = controller.export_local(project, profile, EXTENT)
    assert result.export_dir.startswith(work_dir)
    assert os.path.exists(os.path.join(result.release.release_dir, "data", "map.pmtiles"))
    copied = output / ".q2vt-work" / "halozat" / os.path.basename(result.export_dir)
    assert (copied / "export_log.txt").exists() and (copied / "fidelity_report.html").exists()
    assert not (copied / "tiles.mbtiles").exists()  # only the logs next to the output
    monkeypatch.setenv("Q2VT_LOCAL_WORK", "0")
    assert controller.publication_dirs(profile)[1] == str(output / ".q2vt-work" / "halozat")


def test_the_log_names_the_slowest_layers(plugin, tmp_path):
    from q2vt_plugin.src.qgis2vectortiles import QGIS2VectorTiles  # pylint: disable=import-error
    from qgis.core import QgsRectangle
    project = reset_project()
    slow = QgsVectorLayer("Point?crs=EPSG:3857", "Lassú réteg", "memory")
    project.addMapLayer(slow)
    exporter = QGIS2VectorTiles(extent=QgsRectangle(0, 0, 1, 1), output_dir=str(tmp_path), serve=False)
    messages = []
    exporter._log = messages.append  # pylint: disable=protected-access
    exporter.layer_seconds = {"datasets": {slow.id(): 12.4, "other": 0.2}, "tiles": {slow.id(): 3.0}}
    exporter._log_slowest_layers()  # pylint: disable=protected-access
    assert messages == [". Slowest layers: Lassú réteg 15 s (datasets 12 s, tiles 3 s)"]


def test_merge_reads_the_parts_in_tile_order(plugin, tmp_path):
    """A tile at one address in several parts: their layers in the parts'
    order; a tile of one part kept as it is."""
    import zlib
    from q2vt_plugin.src.core.tiles_generator import merge_mbtiles  # pylint: disable=import-error

    def part(path, layer, tiles):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE metadata (name text, value text)")
            conn.execute("CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, "
                         "tile_data blob, UNIQUE (zoom_level, tile_column, tile_row))")
            name = layer.encode()
            message = b"\x0a" + bytes([len(name)]) + name + b"\x28\x80\x20"
            body = b"\x1a" + bytes([len(message)]) + message
            for z, x, y in reversed(tiles):  # stored out of order
                compressor = zlib.compressobj(9, zlib.DEFLATED, 31)
                conn.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)",
                             (z, x, y, compressor.compress(body) + compressor.flush()))
        return body
    paths = [str(tmp_path / f"{n}.mbtiles") for n in "abc"]
    bodies = [part(paths[0], "first", [(2, 0, 0), (2, 1, 1)]), part(paths[1], "second", [(2, 1, 1), (3, 0, 0)]),
              part(paths[2], "third", [(2, 1, 1)])]
    out = str(tmp_path / "out.mbtiles")
    merge_mbtiles(paths, out, 2, 3, workers=3)
    with sqlite3.connect(out) as conn:
        tiles = {(z, x, y): d for z, x, y, d in conn.execute("SELECT * FROM tiles")}
    with sqlite3.connect(paths[0]) as conn:
        single = conn.execute("SELECT tile_data FROM tiles WHERE zoom_level = 2 AND tile_column = 0").fetchone()[0]
    assert set(tiles) == {(2, 0, 0), (2, 1, 1), (3, 0, 0)}
    assert tiles[(2, 0, 0)] == single  # copied, not gzipped again
    assert zlib.decompress(tiles[(2, 1, 1)], 47) == bodies[0] + bodies[1] + bodies[2]


def test_fast_marker_lines_are_placed_by_the_browser(plugin, tmp_path, monkeypatch):
    """The "fast marker lines" option: screen-size interval markers are not
    computed per zoom but drawn by MapLibre along the lines (reported)."""
    import json as _json  # pylint: disable=import-outside-toplevel
    from q2vt_plugin.src.publishing.controller import export_local  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.models import LayerConfig, PublicationProfile  # pylint: disable=import-error
    sys.path.insert(0, HERE)
    from test_memory_chains import _lines  # pylint: disable=import-error
    from test_publishing_pipeline import EXTENT  # pylint: disable=import-error
    monkeypatch.setenv("Q2VT_WORKERS", "0")

    def export(name, fast):
        project = reset_project()
        roads = _lines(str(tmp_path / f"{name}_roads.gpkg"))
        project.addMapLayer(roads)
        profile = PublicationProfile(title="Utak", slug="utak", locale="hu")
        profile.view.min_zoom, profile.view.max_zoom = 12, 14
        profile.output.local_directory = str(tmp_path / name)
        profile.output.reuse_unchanged = False
        profile.output.fast_markers = fast
        profile.layers = [LayerConfig(roads.id())]
        result = export_local(project, profile, EXTENT)
        with open(os.path.join(result.export_dir, "style", "style.json"), encoding="utf-8") as handle:
            style = _json.load(handle)
        with open(os.path.join(result.export_dir, "fidelity_report.json"), encoding="utf-8") as handle:
            report = _json.load(handle)
        return style, report
    exact_style, exact_report = export("exact", False)
    fast_style, fast_report = export("fast", True)
    def line_markers(style):
        return [layer for layer in style["layers"] if layer.get("type") == "symbol"
                and layer.get("layout", {}).get("symbol-placement") == "line"]
    def point_sources(style):  # the datasets (materialized markers: one per zoom band)
        return {layer.get("source-layer") for layer in style["layers"] if layer.get("source-layer")}
    # Exact: the browser places them only beyond the archive's last zoom.
    assert min(layer.get("minzoom", 0) for layer in line_markers(fast_style)) == 12
    assert all(layer.get("minzoom", 0) > 14 for layer in line_markers(exact_style))
    assert len(point_sources(exact_style)) > len(point_sources(fast_style))
    assert any("Fast marker lines" in d["message"] for d in fast_report["diagnostics"])
    assert not any("Fast marker lines" in d["message"] for d in exact_report["diagnostics"])


def test_fast_marker_option_round_trip(plugin):
    from q2vt_plugin.src.publishing.models import PublicationProfile  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.profile import dumps, load_profile  # pylint: disable=import-error
    profile = PublicationProfile(title="T", slug="t")
    profile.output.fast_markers = True
    assert '"fastMarkers": true' in dumps(profile)
    assert load_profile(dumps(profile)).output.fast_markers is True
    assert load_profile(dumps(PublicationProfile(title="T", slug="t"))).output.fast_markers is False


def test_zoom_band_pieces_give_the_tiles_of_one_run(plugin, tmp_path, monkeypatch):
    """A layer tiled in bands of zoom levels: the same tiles as one ogr2ogr
    run, also for a dataset whose last zoom falls inside a band (the writer
    simplifies a dataset's last zoom differently)."""
    import random as _random  # pylint: disable=import-outside-toplevel
    from qgis.core import QgsRectangle  # pylint: disable=import-outside-toplevel
    from q2vt_plugin.src.core import tiles_generator  # pylint: disable=import-error
    from q2vt_plugin.src.core.datasets import ExportedDataset  # pylint: disable=import-error
    rng = _random.Random(4)
    names = {"l00t00d01r00g00c00o03i09s00": (3, 9), "l00t00d01r00g00c00o05i05s00": (5, 5)}
    fields = QgsFields()
    fields.append(QgsField("k", QVariant.Int))

    def datasets(folder):
        os.makedirs(folder, exist_ok=True)
        out = []
        for name in names:
            path = os.path.join(folder, f"{name}.gpkg")
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            writer = QgsVectorFileWriter.create(path, fields, QgsWkbTypes.Polygon,
                                                QgsCoordinateReferenceSystem("EPSG:3857"),
                                                QgsCoordinateTransformContext(), options)
            features = []
            for number in range(300):
                x, y = 2110000 + rng.uniform(0, 30000), 6010000 + rng.uniform(0, 30000)
                size = rng.uniform(20, 400)
                feature = QgsFeature(fields)
                feature.setAttributes([number])
                feature.setGeometry(QgsGeometry.fromWkt(
                    f"POLYGON(({x} {y}, {x + size} {y + size * 0.1}, {x + size * 0.8} {y + size}, "
                    f"{x + size * 0.1} {y + size * 0.7}, {x} {y}))"))
                features.append(feature)
            writer.addFeatures(features)
            del writer
            out.append(ExportedDataset(path, name, 300, None))
        return out

    def tiles(folder, single):
        rng.seed(4)
        layers = datasets(folder)
        if single:
            monkeypatch.setenv("Q2VT_SINGLE_OGR2OGR", "1")
        else:
            monkeypatch.delenv("Q2VT_SINGLE_OGR2OGR", raising=False)
            monkeypatch.setattr(tiles_generator.GDALTilesGenerator, "_pieces",
                                lambda self, members, target: [(8, 9, 1.0), (5, 7, 1.0), (3, 4, 1.0)])
            monkeypatch.setattr(tiles_generator.GDALTilesGenerator, "_cpu_num", lambda self: 2)
        generator = tiles_generator.GDALTilesGenerator(
            layers, {"layers": []}, folder, QgsRectangle(2110000, 6010000, 2140000, 6040000), 100, None,
            layer_zooms=names, layer_groups={"one": list(names)})
        generator.generate()
        out = {}
        with sqlite3.connect(os.path.join(folder, "tiles.mbtiles")) as conn:
            for z, x, y, data in conn.execute("SELECT * FROM tiles"):
                from publishing import mvt  # pylint: disable=import-outside-toplevel
                out[(z, x, y)] = {name: [(f["type"], tuple(f["geometry"])) for f in layer["features"]]
                                  for name, layer in mvt.decode(data, geometry=True).items()}
        return out
    one = tiles(str(tmp_path / "one"), True)
    bands = tiles(str(tmp_path / "bands"), False)
    assert one == bands and any(z == 5 for z, _, _ in one)
