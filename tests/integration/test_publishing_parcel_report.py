"""Parcel report (telekinformáció): parcel area, parts cut by the zoning and
the regulation / zone boundary lines (exact areas in EOV), the lines bounding
each part inside the parcel, zone and restriction legend graphics as QGIS
draws them, restriction overlaps (polygons, line and point protection
distances), slivers ignored, keys identical to the tiles' feature keys,
only approved values public."""

import json
import math
import os
import sys

import pytest
from qgis.core import (QgsCategorizedSymbolRenderer, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsFeature, QgsField, QgsFillSymbol, QgsGeometry, QgsProject, QgsRectangle,
                       QgsRendererCategory, QgsVectorLayer)
from qgis.PyQt.QtCore import QVariant

from publishing import mvt
from publishing.controller import export_local
from publishing.feature_index import shard_key
from publishing.models import CutLineConfig, LayerConfig, PopupField, PublicationProfile, RestrictionConfig
from publishing.parcel_report import split_by_lines
from publishing.validation import open_pmtiles
from q2vt_fixtures import reset_project, to_geopackage

X, Y = 650000, 240000   # EOV (EPSG:23700), Budapest
EOV = "EPSG:23700"


def box(x0, y0, x1, y1):
    return QgsGeometry.fromRect(QgsRectangle(X + x0, Y + y0, X + x1, Y + y1))


def line(*points):
    return QgsGeometry.fromWkt("LINESTRING(" + ", ".join(f"{X + a} {Y + b}" for a, b in points) + ")")


def layer(kind, name, fields, rows, path=None):
    memory = QgsVectorLayer(f"{kind}?crs={EOV}", name, "memory")
    memory.dataProvider().addAttributes([QgsField(f, QVariant.String) for f in fields])
    memory.updateFields()
    for values, geometry in rows:
        feature = QgsFeature(memory.fields())
        feature.setAttributes(list(values))
        feature.setGeometry(geometry)
        memory.dataProvider().addFeatures([feature])
    return to_geopackage(memory, str(path)) if path else memory


@pytest.fixture
def site(plugin, tmp_path):
    return build_site(tmp_path)


def build_site(tmp_path, **profile_changes):
    """Two parcels in EOV, three zones, regulation line, zone boundary and
    four restriction layers, published with the parcel report."""
    project = reset_project()
    parcels = layer("Polygon", "Földrészletek", ["hrsz", "kivett", "tulaj"],
                    [(("100/1", "lakóház", "Kovács János"), box(0, 0, 100, 40)),
                     (("100/2", "", "Szabó Éva"), box(100, 0, 150, 40))], tmp_path / "parcels.gpkg")
    zoning = layer("Polygon", "Szabályozás színek", ["szab_ov", "p_beepszaz"],
                   [(("Lke-1", "30"), box(0, 8, 70, 40)), (("Gksz", "50"), box(70, 8, 150, 40)),
                    (("Köu", None), box(0, 0, 150, 8))])
    zoning.setRenderer(QgsCategorizedSymbolRenderer("szab_ov", [
        QgsRendererCategory("Lke-1", QgsFillSymbol.createSimple({"color": "255,200,0"}), "Lke-1 – Kertvárosias", True),
        QgsRendererCategory("Gksz", QgsFillSymbol.createSimple({"color": "150,0,150"}), "Gksz – Gazdasági", True),
        QgsRendererCategory("Köu", QgsFillSymbol.createSimple({"color": "200,200,200"}), "Köu – Közút", True)]))
    regulation = layer("LineString", "Szabályozási vonal", [], [((), line((0, 8), (150, 8)))])
    boundary = layer("LineString", "Övezethatár", [], [((), line((70, 8), (70, 40)))])
    site_poly = layer("Polygon", "Régészeti lelőhely", ["NEV", "titok"],
                      [(("Kő-domb", "SECRET-NOTE"), box(50, 20, 120, 60))])
    gas = layer("LineString", "Gázvezeték", [], [((), line((145, -100), (145, 140)))])
    monument = layer("Point", "Műemlék", ["NEV"], [(("Kápolna",), QgsGeometry.fromWkt(f"POINT({X + 10} {Y + 41})"))])
    neighbour = layer("Polygon", "Szomszéd terület", [], [((), box(0, 40, 100, 60))])
    root = project.layerTreeRoot()
    for item in (parcels, zoning, regulation, boundary, site_poly, gas, monument, neighbour):
        project.addMapLayer(item)
    profile = PublicationProfile(title="Telekinfó", slug="telekinfo", locale="hu")
    profile.view.min_zoom, profile.view.max_zoom = 14, 15
    profile.output.local_directory = str(tmp_path / "out")
    profile.layers = [LayerConfig(parcels.id())]
    info = profile.parcel_info
    info.enabled, info.parcel_layer_id, info.key_field = True, parcels.id(), "hrsz"
    info.fields = [PopupField("kivett", "Kivett megnevezés")]
    info.zoning_layer_id, info.zoning_code_field = zoning.id(), "szab_ov"
    info.zoning_fields = [PopupField("p_beepszaz", "Max. beépítettség %")]
    info.cut_lines = [CutLineConfig(regulation.id(), "Szabályozási vonal"), CutLineConfig(boundary.id(), "Övezethatár")]
    info.restrictions = [
        RestrictionConfig(site_poly.id(), "Régészeti lelőhely", "Földmunka előtt egyeztetni kell.", "Kötv. 7. §", "NEV"),
        RestrictionConfig(gas.id(), "Gázvezeték biztonsági övezete", buffer_m=10),
        RestrictionConfig(monument.id(), "Műemlék környezete", name_field="NEV", buffer_m=5),
        RestrictionConfig(neighbour.id(), "Szomszéd terület")]
    info.min_share = 0.1
    for key, value in profile_changes.items():
        setattr(profile.interaction, key, value)
    to_web = QgsCoordinateTransform(QgsCoordinateReferenceSystem(EOV), QgsCoordinateReferenceSystem("EPSG:3857"),
                                    project.transformContext())
    extent = to_web.transformBoundingBox(QgsRectangle(X - 50, Y - 50, X + 200, Y + 100))
    result = export_local(project, profile, extent)
    rel = result.release.release_dir
    manifest = json.load(open(os.path.join(rel, "manifest.json"), encoding="utf-8"))
    pm = json.load(open(os.path.join(rel, "parcels", "manifest.json"), encoding="utf-8"))
    records = {}
    for shard in pm["shards"]:
        for record in json.load(open(os.path.join(rel, "parcels", shard["path"]), encoding="utf-8")):
            records[record["k"]] = record
    catalog = json.load(open(os.path.join(rel, "parcels", "catalog.json"), encoding="utf-8"))
    return {"rel": rel, "manifest": manifest, "pm": pm, "records": records, "catalog": catalog,
            "parcels": parcels, "project": project}


def test_parts_and_areas(site):
    first, second = site["records"]["100/1"], site["records"]["100/2"]
    assert first["a"] == pytest.approx(4000, abs=0.01) and second["a"] == pytest.approx(2000, abs=0.01)
    parts = [(p["n"], p["c"], p["a"], p["s"]) for p in first["p"]]
    assert parts == [(1, "Lke-1", pytest.approx(2240, abs=0.01), pytest.approx(56.0)),
                     (2, "Gksz", pytest.approx(960, abs=0.01), pytest.approx(24.0)),
                     (3, "Köu", pytest.approx(800, abs=0.01), pytest.approx(20.0))]
    by_code = {p["c"]: p for p in first["p"]}
    assert by_code["Köu"]["b"] == ["Szabályozási vonal"]           # cut off by the regulation line
    assert sorted(by_code["Lke-1"]["b"]) == ["Szabályozási vonal", "Övezethatár"]
    assert by_code["Lke-1"]["z"] == {"p_beepszaz": "30"}
    assert by_code["Lke-1"]["zl"] == "Lke-1 – Kertvárosias"
    assert [p["c"] for p in second["p"]] == ["Gksz", "Köu"]
    assert second["p"][0]["b"] == ["Szabályozási vonal"]           # its own edge on x=100 is not a cut
    for record in (first, second):
        assert sum(p["a"] for p in record["p"]) == pytest.approx(record["a"], abs=0.05)
        for part in record["p"]:
            assert part["x"] and 18 < part["x"][0] < 20 and 47 < part["x"][1] < 48
            assert os.path.exists(os.path.join(site["rel"], part["sw"]))
    assert first["f"] == {"kivett": "lakóház"}


def test_restrictions(site):
    titles = {r["i"]: r["title"] for r in site["catalog"]["restrictions"]}
    first = {titles[r["i"]]: r for r in site["records"]["100/1"]["r"]}
    second = {titles[r["i"]]: r for r in site["records"]["100/2"]["r"]}
    assert first["Régészeti lelőhely"]["a"] == pytest.approx(1000, abs=0.01)
    assert first["Régészeti lelőhely"]["s"] == pytest.approx(25.0) and first["Régészeti lelőhely"]["n"] == ["Kő-domb"]
    assert second["Régészeti lelőhely"]["a"] == pytest.approx(400, abs=0.01)
    assert second["Gázvezeték biztonsági övezete"]["a"] == pytest.approx(600, rel=0.01)
    assert "Gázvezeték biztonsági övezete" not in first
    expected = 25 * math.acos(0.2) - 1 * math.sqrt(24)               # circle segment below the edge
    assert first["Műemlék környezete"]["a"] == pytest.approx(expected, rel=0.05)
    assert first["Műemlék környezete"]["n"] == ["Kápolna"]
    assert "Szomszéd terület" not in first                           # touches along an edge only
    for hit in first.values():
        label, path = hit["k"][0]
        assert label and os.path.exists(os.path.join(site["rel"], path))
    meta = next(r for r in site["catalog"]["restrictions"] if r["title"] == "Régészeti lelőhely")
    assert meta["note"] == "Földmunka előtt egyeztetni kell." and meta["reference"] == "Kötv. 7. §"
    assert site["catalog"]["disclaimer"].startswith("Tájékoztató")


def test_keys_match_the_tiles_and_nothing_private_is_public(site):
    manifest, pm = site["manifest"], site["pm"]
    assert manifest["parcelInfo"]["layerId"] == pm["layerId"] and manifest["parcelInfo"]["records"] == 2
    for shard in pm["shards"]:
        for record in json.load(open(os.path.join(site["rel"], "parcels", shard["path"]), encoding="utf-8")):
            assert shard_key(pm["layerId"], record["k"], pm["prefixLength"]) == shard["key"]
    keys = set()
    with open_pmtiles(os.path.join(site["rel"], "data", "map.pmtiles")) as archive:
        for _, data in archive.tiles():
            for layer in mvt.decode(data).values():
                keys.update(f["properties"].get("q2vt_feature_key") for f in layer["features"])
    assert {"100/1", "100/2"} <= keys
    text = "".join(open(os.path.join(root, name), encoding="utf-8", errors="ignore").read()
                   for root, _, names in os.walk(site["rel"]) for name in names
                   if name.endswith((".json", ".mjs", ".html")))
    assert "Kovács" not in text and "SECRET-NOTE" not in text      # not approved: never published
    from publishing.validation import open_pmtiles as _open  # noqa
    schema = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                          "schemas", "publishing", "manifest-v1.schema.json")
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(manifest, json.load(open(schema, encoding="utf-8")))


def test_split_by_lines_cuts_inside_only():
    polygon = box(0, 0, 100, 40)
    faces = split_by_lines(polygon, [line((35, -10), (35, 50)), line((200, 0), (300, 0))])
    assert sorted(round(f.area(), 3) for f in faces) == [1400.0, 2600.0]
    assert split_by_lines(polygon, [])[0].area() == pytest.approx(4000)
