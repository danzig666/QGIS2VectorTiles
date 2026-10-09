"""4.27 export speed-up: inside a rule group the Processing steps hand memory
layers to each other (no temporary GeoPackage per step, no second parameter
check) and a step identical to one already run (the same filter, the same
outline at another zoom) is not run again. The tiles must be the same as
with files (Q2VT_FILE_CHAINS=1, the former behaviour); a group whose memory
chain fails is redone with files."""

import os
import sys

import pytest
from qgis.core import (QgsFeature, QgsFillSymbol, QgsGeometry, QgsLineSymbol, QgsMarkerLineSymbolLayer,
                       QgsRuleBasedRenderer, QgsUnitTypes, QgsVectorLayer)

from publishing import mvt
from publishing.controller import export_local
from publishing.models import LayerConfig, PublicationProfile
from publishing.validation import open_pmtiles
from q2vt_fixtures import reset_project, to_geopackage

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_publishing_pipeline import EXTENT, _parcels  # noqa: E402  pylint: disable=import-error,wrong-import-position


def _lines(path):
    """A road layer: two kinds (rule filters), screen-unit interval markers
    (positions per zoom) on one of them."""
    layer = QgsVectorLayer("LineString?crs=EPSG:3857&field=kind:string", "Utak", "memory")
    for kind, wkt in (("fo", "LINESTRING(2119500 6019500, 2120800 6020400, 2122600 6022700)"),
                      ("mellek", "LINESTRING(2119200 6022800, 2121500 6021100, 2122800 6019300)"),
                      ("fo", "LINESTRING(2120100 6019100, 2120200 6022900)")):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([kind])
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        layer.dataProvider().addFeatures([feature])
    saved = to_geopackage(layer, path)
    main = QgsLineSymbol.createSimple({"color": "200,0,0", "width": "0.8"})
    markers = QgsMarkerLineSymbolLayer(True, 6)
    markers.setIntervalUnit(QgsUnitTypes.RenderPoints)
    markers.setOffsetAlongLine(2)
    main.appendSymbolLayer(markers)
    root = QgsRuleBasedRenderer.Rule(None)
    root.appendChild(QgsRuleBasedRenderer.Rule(main, filterExp="\"kind\" = 'fo'", label="Főút"))
    root.appendChild(QgsRuleBasedRenderer.Rule(QgsLineSymbol.createSimple({"color": "90,90,90"}),
                                               filterExp="\"kind\" = 'mellek'", label="Mellékút"))
    saved.setRenderer(QgsRuleBasedRenderer(root))
    return saved


def _outlined(parcels):
    """The parcels' outline as interval markers too (polygon → lines step)."""
    renderer = parcels.renderer()
    for index, category in enumerate(renderer.categories()):
        symbol = category.symbol().clone()
        outline = QgsMarkerLineSymbolLayer(True, 5)
        outline.setIntervalUnit(QgsUnitTypes.RenderPoints)
        symbol.appendSymbolLayer(outline)
        renderer.updateCategorySymbol(index, symbol)


def _export(tmp_path, name, monkeypatch, file_chains, stats=None):
    if file_chains:
        monkeypatch.setenv("Q2VT_FILE_CHAINS", "1")
    else:
        monkeypatch.delenv("Q2VT_FILE_CHAINS", raising=False)
    parcels = _parcels(str(tmp_path / f"{name}_parcels.gpkg"))
    _outlined(parcels)
    roads = _lines(str(tmp_path / f"{name}_roads.gpkg"))
    project = reset_project()
    project.addMapLayers([parcels, roads])
    profile = PublicationProfile(title="Láncok", slug="lancok", locale="hu")
    profile.view.min_zoom, profile.view.max_zoom = 12, 14
    profile.output.local_directory = str(tmp_path / name)
    profile.output.reuse_unchanged = False
    profile.layers = [LayerConfig(parcels.id()), LayerConfig(roads.id())]
    if stats is not None:
        from q2vt_plugin.src.core import rules_exporter  # pylint: disable=import-error
        release = rules_exporter.RulesExporter._release_all_layers

        def record(self):
            stats.update(self.step_stats)
            release(self)
        monkeypatch.setattr(rules_exporter.RulesExporter, "_release_all_layers", record)
    result = export_local(project, profile, EXTENT)
    tiles = {}
    with open_pmtiles(os.path.join(result.release.release_dir, "data", "map.pmtiles")) as archive:
        for address, data in archive.tiles():
            layers = mvt.decode(data, geometry=True)
            tiles[tuple(address)] = {
                name: [(f["type"], tuple(f["geometry"]), tuple(sorted((k, repr(v)) for k, v in f["properties"].items())))
                       for f in layer["features"]]
                for name, layer in layers.items()}
    return tiles


def _same(a, b):
    """Equal tiles; label layers (t01, whose order already varies from run to
    run) compared as sets of features, every other layer in drawing order."""
    assert set(a) == set(b)
    for address in a:
        assert set(a[address]) == set(b[address]), address
        for name, features in a[address].items():
            other = b[address][name]
            if "t01" in name:
                assert sorted(features) == sorted(other), (address, name)
            else:
                assert features == other, (address, name)


def test_memory_chains_give_the_same_tiles_and_share_steps(plugin, tmp_path, monkeypatch):
    stats = {}
    files = _export(tmp_path, "files", monkeypatch, True)
    memory = _export(tmp_path, "memory", monkeypatch, False, stats)
    _same(files, memory)
    assert stats["run"] > 0 and stats["shared"] > 0, stats  # the same filter / outline at other zooms
    marker_layers = {name for tile in memory.values() for name in tile if "t00" in name}
    assert len(marker_layers) > 3  # fills, roads and per-zoom marker datasets were exported


def test_a_failing_memory_chain_is_redone_with_files(plugin, tmp_path, monkeypatch):
    from q2vt_plugin.src.core import rules_exporter  # pylint: disable=import-error
    files = _export(tmp_path, "files", monkeypatch, True)
    original = rules_exporter.RulesExporter._run_in_memory
    failed = []

    def flaky(self, full_name, params, context, feedback):
        if full_name == "native:refactorfields" and len(failed) < 3:
            failed.append(full_name)
            raise RuntimeError("simulated memory step failure")
        return original(self, full_name, params, context, feedback)
    monkeypatch.setattr(rules_exporter.RulesExporter, "_run_in_memory", flaky)
    memory = _export(tmp_path, "memory", monkeypatch, False)
    assert len(failed) == 3
    _same(files, memory)
