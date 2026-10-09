"""Street search: the named OpenStreetMap streets (Protomaps ``roads``)
inside the extent layer's polygon are added to the publication's search
(one search box: fields and streets), from the bundled basemap or, without
a basemap, read from its source; only names, a point and bounds."""

import json
import os
import sys

from qgis.core import QgsFeature, QgsGeometry, QgsPointXY, QgsVectorLayer

from publishing.basemap import STREETS_LAYER
from publishing.controller import export_local
from publishing.shard_pack import read_shard
from publishing_fixtures import protomaps_planet

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_publishing_basemap_themes import EXTENT, _setup  # noqa: E402  pylint: disable=wrong-import-position


def _area(project):
    """A triangle in the west half of the export extent (EPSG:3857)."""
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "Település", "memory")
    feature = QgsFeature()
    x0, y0, x1, y1 = EXTENT.xMinimum(), EXTENT.yMinimum(), EXTENT.xMaximum(), EXTENT.yMaximum()
    mid = (x0 + x1) / 2
    feature.setGeometry(QgsGeometry.fromPolygonXY([[QgsPointXY(x0, y0), QgsPointXY(mid, y0),
                                                   QgsPointXY(x0, y1), QgsPointXY(x0, y0)]]))
    layer.dataProvider().addFeatures([feature])
    project.addMapLayer(layer, False)
    return layer


def _search(result):
    folder = os.path.join(result.release.release_dir, "search")
    manifest = json.load(open(os.path.join(folder, "manifest.json"), encoding="utf-8"))
    entries = []
    for shard in manifest["shards"]:
        data = read_shard(folder, shard)
        entries += [dict(zip(("layer", "key", "label", "terms", "anchor", "bounds", "zoom"), e),
                         layer=data["layers"][e[0]]) for e in data["entries"]]
    return manifest, entries


def _lonlat(x, y):
    import math  # pylint: disable=import-outside-toplevel
    return math.degrees(x / 6378137.0), math.degrees(2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2)


def test_streets_inside_the_extent_layer_join_the_search(tmp_path):
    project, profile, _parcels, _second = _setup(tmp_path)
    area = _area(project)
    planet = protomaps_planet(str(tmp_path / "planet.pmtiles"))
    profile.basemap.kind, profile.basemap.source, profile.basemap.max_zoom = "protomaps", planet, 14
    profile.view.extent_layer = area.id()
    profile.interaction.street_search = True
    result = export_local(project, profile, EXTENT)
    manifest, entries = _search(result)
    assert STREETS_LAYER in manifest["layers"]
    streets = [e for e in entries if e["layer"] == STREETS_LAYER]
    others = [e for e in entries if e["layer"] != STREETS_LAYER]
    assert streets and others  # one index: fields and streets
    assert {s["label"] for s in streets} == {"Fő utca"}
    west, south = _lonlat(EXTENT.xMinimum(), EXTENT.yMinimum())
    east, north = _lonlat(EXTENT.xMaximum(), EXTENT.yMaximum())
    middle = (west + east) / 2
    for street in streets:
        lon, lat = street["anchor"]
        # Inside the triangle (west of its hypotenuse), not just the extent.
        assert west - 1e-6 <= lon <= middle + 1e-6 and south - 1e-6 <= lat <= north + 1e-6
        assert (lon - west) / (middle - west) + (lat - south) / (north - south) <= 1.0 + 1e-3
        b = street["bounds"]
        assert b[0] >= west - 1e-6 and b[2] <= middle + 1e-6
    # No geometry in the index: names, a point and bounds only.
    assert all(len(json.dumps(s)) < 400 for s in streets)


def test_without_a_basemap_the_streets_come_from_its_source(tmp_path):
    project, profile, _parcels, _second = _setup(tmp_path)
    planet = protomaps_planet(str(tmp_path / "planet.pmtiles"))
    profile.basemap.kind, profile.basemap.source = "none", planet
    profile.interaction.street_search = True
    result = export_local(project, profile, EXTENT)
    _manifest, entries = _search(result)
    streets = [e for e in entries if e["layer"] == STREETS_LAYER]
    assert streets and all(e["label"] == "Fő utca" for e in streets)
    assert not os.path.exists(os.path.join(result.release.release_dir, "data", "basemap.pmtiles"))


def test_a_missing_source_is_a_warning(tmp_path):
    project, profile, _parcels, _second = _setup(tmp_path)
    profile.basemap.kind, profile.basemap.source = "none", str(tmp_path / "missing.pmtiles")
    profile.interaction.street_search = True
    result = export_local(project, profile, EXTENT)
    assert any("Street search" in w for w in result.warnings)
    manifest, _entries = _search(result)
    assert STREETS_LAYER not in manifest["layers"]
