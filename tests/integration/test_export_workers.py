"""4.28: the rule export in worker processes (core.export_workers) gives the
tiles and the diagnostics of one process; a group whose expressions need the
project (another layer, a function only this QGIS knows) and the work of a
worker that stopped are done by the main process."""

import json
import os
import sys

import pytest
from qgis.core import QgsFeature, QgsGeometry, QgsLineSymbol, QgsRuleBasedRenderer, QgsVectorLayer

from publishing import mvt
from publishing.controller import export_local
from publishing.models import LayerConfig, PublicationProfile
from publishing.validation import open_pmtiles
from q2vt_fixtures import reset_project, to_geopackage

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_memory_chains import _lines, _outlined, _same  # noqa: E402  pylint: disable=import-error,wrong-import-position
from test_publishing_pipeline import EXTENT, _parcels  # noqa: E402  pylint: disable=import-error,wrong-import-position


def _needs_project(path, parcels):
    """Lines styled by an aggregate over the parcel layer: only the main
    process has that layer."""
    layer = QgsVectorLayer("LineString?crs=EPSG:3857&field=n:integer", "Mérések", "memory")
    feature = QgsFeature(layer.fields())
    feature.setAttributes([1])
    feature.setGeometry(QgsGeometry.fromWkt("LINESTRING(2119800 6019800, 2122200 6022200)"))
    layer.dataProvider().addFeatures([feature])
    saved = to_geopackage(layer, path)
    root = QgsRuleBasedRenderer.Rule(None)
    root.appendChild(QgsRuleBasedRenderer.Rule(
        QgsLineSymbol.createSimple({"color": "0,0,200"}),
        filterExp=f"aggregate('{parcels.id()}', 'count', \"hrsz\") > 0", label="Mérés"))
    saved.setRenderer(QgsRuleBasedRenderer(root))
    return saved


def _export(tmp_path, name, monkeypatch, workers):
    monkeypatch.setenv("Q2VT_WORKERS", str(workers))
    parcels = _parcels(str(tmp_path / f"{name}_parcels.gpkg"))
    _outlined(parcels)
    roads = _lines(str(tmp_path / f"{name}_roads.gpkg"))
    project = reset_project()
    project.addMapLayers([parcels, roads])
    measured = _needs_project(str(tmp_path / f"{name}_measured.gpkg"), parcels)
    project.addMapLayer(measured)
    profile = PublicationProfile(title="Munkások", slug="munkasok", locale="hu")
    profile.view.min_zoom, profile.view.max_zoom = 12, 14
    profile.output.local_directory = str(tmp_path / name)
    profile.output.reuse_unchanged = False
    profile.layers = [LayerConfig(parcels.id()), LayerConfig(roads.id()), LayerConfig(measured.id())]
    result = export_local(project, profile, EXTENT)
    tiles = {}
    with open_pmtiles(os.path.join(result.release.release_dir, "data", "map.pmtiles")) as archive:
        for address, data in archive.tiles():
            layers = mvt.decode(data, geometry=True)
            tiles[tuple(address)] = {
                name: [(f["type"], tuple(f["geometry"]), tuple(sorted((k, repr(v)) for k, v in f["properties"].items())))
                       for f in layer["features"]]
                for name, layer in layers.items()}
    with open(os.path.join(result.export_dir, "fidelity_report.json"), encoding="utf-8") as handle:
        report = json.load(handle)
    with open(os.path.join(result.export_dir, "export_log.txt"), encoding="utf-8") as handle:
        log = handle.read()
    return tiles, report.get("diagnostics"), log


def test_workers_give_the_tiles_and_diagnostics_of_one_process(plugin, tmp_path, monkeypatch):
    serial, serial_diagnostics, _ = _export(tmp_path, "one", monkeypatch, 0)
    parallel, parallel_diagnostics, log = _export(tmp_path, "workers", monkeypatch, 2)
    assert "2 worker processes export the datasets" in log
    _same(serial, parallel)
    assert parallel_diagnostics == serial_diagnostics
    assert any("l02" in name or "t00" in name for tile in parallel.values() for name in tile)


def test_the_work_of_a_stopped_worker_is_done_here(plugin, tmp_path, monkeypatch):
    from q2vt_plugin.src.core import export_workers  # pylint: disable=import-error
    serial, _, _ = _export(tmp_path, "one", monkeypatch, 0)
    original = export_workers.WorkerPool.submit
    sent = []

    def submit(self, number, message):
        sent.append(message[0])
        if message[0] == "groups" and sent.count("groups") == 2:
            self.workers[number].kill()  # dies with a task in hand
        return original(self, number, message)
    monkeypatch.setattr(export_workers.WorkerPool, "submit", submit)
    parallel, _, log = _export(tmp_path, "workers", monkeypatch, 2)
    assert "stopped" in log
    _same(serial, parallel)


@pytest.mark.parametrize("expression, missing, main", [
    ("\"kind\" = 'fo'", [], False),
    ("aggregate('parcels', 'sum', \"area\") > 10", [], True),
    ("get_feature('zones', 'code', \"zone\") IS NOT NULL", [], True),
    ("@layers IS NOT NULL", ["layers"], True),
    ("@project_title = 'x'", ["layers"], False),
])
def test_groups_that_need_the_main_process(plugin, expression, missing, main):
    from q2vt_plugin.src.core.export_workers import needs_main_process  # pylint: disable=import-error
    from q2vt_plugin.src.core.rules_exporter import _RuleGroupSnapshot  # pylint: disable=import-error
    group = _RuleGroupSnapshot(
        output_dataset="l00t00", layer_id="x", rule_type=0, filter_expression=expression,
        geometry_target=2, geometry_expression="@geometry", expression_fields=[], description="",
        include_required_fields_only=0, recipe=None, source_geometry=2, part_fields=[], flat_rules=[])
    assert needs_main_process(group, missing) is main
