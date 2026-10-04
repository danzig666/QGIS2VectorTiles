"""Local publication pipeline (PUB-03/04/10/11 data side): provenance and
logical model, stable keys through derived datasets, disclosure (canary
absent from every public file), search/feature indexes, legend swatches,
project unchanged."""

import json
import os

import pytest
from qgis.core import (QgsCategorizedSymbolRenderer, QgsFeature, QgsField, QgsFillSymbol,
                       QgsGeometry, QgsPalLayerSettings, QgsProject, QgsRectangle,
                       QgsRendererCategory, QgsTextFormat, QgsVectorLayer,
                       QgsVectorLayerSimpleLabeling, Qgis)
from qgis.PyQt.QtCore import QVariant

from publishing import mvt
from publishing.controller import export_local
from publishing.errors import PublishingError
from publishing.models import FilterField, LayerConfig, PopupField, PublicationProfile
from publishing.provenance import layer_logical_id
from publishing.validation import open_pmtiles
from q2vt_fixtures import SQUARES, reset_project, to_geopackage

EXTENT = QgsRectangle(2119000, 6019000, 2123000, 6023000)
CANARY = "CANARY-7f3a-SECRET"
PARCELS = ["00123/4", "00123/5", "0099", "12345678901234567890"]  # leading zeros, 64-bit+
# Square C has a NULL zone: no category draws it, so QGIS neither draws nor
# labels it and it is not part of the publication.
PUBLISHED = ["00123/4", "00123/5", "12345678901234567890"]


def _parcels(path):
    layer = QgsVectorLayer("Polygon?crs=EPSG:3857", "Földrészletek", "memory")
    provider = layer.dataProvider()
    provider.addAttributes([QgsField("hrsz", QVariant.String), QgsField("zone", QVariant.String),
                            QgsField("terulet", QVariant.Double), QgsField("tulaj", QVariant.String),
                            QgsField("note", QVariant.String)])
    layer.updateFields()
    for (fid, zone, wkt), hrsz in zip(SQUARES, PARCELS):
        feature = QgsFeature(layer.fields())
        feature.setAttributes([hrsz, zone, 1000.5, CANARY, "<script>alert(1)</script>"])
        feature.setGeometry(QgsGeometry.fromWkt(wkt))
        provider.addFeatures([feature])
    saved = to_geopackage(layer, path)
    categories = [QgsRendererCategory(value, QgsFillSymbol.createSimple({"color": color}), label, True)
                  for value, color, label in (("K1", "255,0,0", "Kertvárosi"),
                                              ("K2", "0,200,0", "Kisvárosi"),
                                              ("Lk", "0,0,255", "Kertes"))]
    saved.setRenderer(QgsCategorizedSymbolRenderer("zone", categories))
    settings = QgsPalLayerSettings()
    settings.fieldName = "hrsz"
    settings.placement = Qgis.LabelPlacement.OverPoint
    settings.centroidWhole = False
    fmt = QgsTextFormat()
    fmt.setSize(10)
    settings.setFormat(fmt)
    saved.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    saved.setLabelsEnabled(True)
    return saved


def _profile(parcels, tmp_path, **layer_kwargs):
    profile = PublicationProfile(title="Arló teszt", slug="arlo-teszt", locale="hu")
    profile.view.min_zoom, profile.view.max_zoom = 11, 13
    profile.output.local_directory = str(tmp_path / "Kimenet mappa")
    config = LayerConfig(parcels.id(), initially_visible=False,
                         popup_fields=[PopupField("hrsz", "Helyrajzi szám"), PopupField("terulet", "Terület", "number"),
                                       PopupField("note", "Megjegyzés")],
                         search_fields=["hrsz"], key_fields=["hrsz"],
                         filter_fields=[FilterField("zone", "values", "Övezet")])
    for key, value in layer_kwargs.items():
        setattr(config, key, value)
    profile.layers = [config]
    return profile


@pytest.fixture
def project(plugin, tmp_path):
    parcels = _parcels(str(tmp_path / "parcels.gpkg"))
    project = reset_project()
    group = project.layerTreeRoot().addGroup("Szabályozás")
    project.addMapLayer(parcels, False)
    group.addLayer(parcels)
    project.layerTreeRoot().findLayer(parcels.id()).setItemVisibilityChecked(False)  # hidden
    return project, parcels


def _public_files(release_dir):
    for folder, _, files in os.walk(release_dir):
        for name in files:
            yield os.path.join(folder, name)


def test_local_publication_end_to_end(project, tmp_path):
    project, parcels = project
    profile = _profile(parcels, tmp_path)
    tree_before = [(n.layer().id(), n.isVisible()) for n in project.layerTreeRoot().findLayers()]
    layers_before = set(project.mapLayers())
    result = export_local(project, profile, EXTENT, canaries=[CANARY])
    assert result.state.value == "LOCAL_READY"
    # A22: the project is untouched (visibility, layers).
    assert [(n.layer().id(), n.isVisible()) for n in project.layerTreeRoot().findLayers()] == tree_before
    assert set(project.mapLayers()) == layers_before
    rel = result.release.release_dir
    manifest = json.load(open(os.path.join(rel, "manifest.json"), encoding="utf-8"))
    lid = layer_logical_id(parcels.id())
    # Logical model: group, layer (hidden but included), categories as rules.
    assert manifest["groups"] == [{"id": manifest["groups"][0]["id"], "title": "Szabályozás",
                                   "parentId": None, "order": 0, "expanded": True,
                                   "toggleable": True, "initialVisibility": True}]
    [layer] = manifest["layers"]
    assert layer["id"] == lid and layer["initialVisibility"] is False
    assert layer["groupId"] == manifest["groups"][0]["id"] and layer["title"] == "Földrészletek"
    assert [r["title"] for r in manifest["rules"]] == ["Kertvárosi", "Kisvárosi", "Kertes"]
    assert all(r["swatch"] and os.path.exists(os.path.join(rel, r["swatch"])) for r in manifest["rules"])
    roles = {c["role"] for c in manifest["components"]}
    assert {"geometry", "label"} <= roles
    for rule in manifest["rules"]:  # every category owns its fill component
        assert rule["componentIds"], rule
    label = next(c for c in manifest["components"] if c["role"] == "label")
    assert label["dependsOnSourceLayers"] and label["dependsOnSourceLayers"][0].endswith("_vp")
    style_ids = {l["id"] for l in json.load(open(os.path.join(rel, "style.json"), encoding="utf-8"))["layers"]}
    assert all(set(c["styleLayerIds"]) <= style_ids for c in manifest["components"])
    assert layer["filterFields"][0]["values"] == [["K1", 1], ["K2", 1], ["Lk", 1]]
    assert layer["filterFields"][0]["nulls"] == 0
    # Tiles: feature key (exact text) and the approved filter field only.
    keys, props = set(), set()
    with open_pmtiles(os.path.join(rel, "data", "map.pmtiles")) as archive:
        for _, data in archive.tiles():
            for _, info in mvt.decode(data).items():
                for feature in info["features"]:
                    props.update(feature["properties"])
                    if "q2vt_feature_key" in feature["properties"]:
                        keys.add(feature["properties"]["q2vt_feature_key"])
    assert keys == set(PUBLISHED)
    assert {p for p in props if not p.startswith("q2vt_")} == {"zone"}
    # A14: the canary (unapproved "tulaj" field) is in no public file.
    for path in _public_files(result.publication_dir):
        with open(path, "rb") as handle:
            assert CANARY.encode() not in handle.read(), path
    # Search and feature lookup: complete, exact keys, approved fields only.
    search = json.load(open(os.path.join(rel, "search", "manifest.json"), encoding="utf-8"))
    assert search["records"] == 3 and search["coverage"][lid]["records"] == 3
    entries = json.load(open(os.path.join(rel, "search", search["shards"][0]["path"]), encoding="utf-8"))
    assert sorted(e[1] for e in entries["entries"]) == sorted(PUBLISHED)
    features = json.load(open(os.path.join(rel, "features", "manifest.json"), encoding="utf-8"))
    shard = json.load(open(os.path.join(rel, "features", features["shards"][0]["path"]), encoding="utf-8"))
    record = next(r for r in shard if r["k"] == "00123/4")
    assert record["a"] == {"hrsz": "00123/4", "terulet": 1000.5, "note": "<script>alert(1)</script>"}
    assert record["p"] and record["b"] and record["l"] == lid
    # No private files in the publication.
    names = [os.path.basename(p) for p in _public_files(result.publication_dir)]
    assert not [n for n in names if n.endswith((".gpkg", ".mbtiles", ".jsonl", ".qgs"))
                or n in ("export_log.txt", "fidelity_report.json")]


def test_duplicate_keys_stop_the_publication(project, tmp_path):
    project, parcels = project
    profile = _profile(parcels, tmp_path, key_fields=["terulet"])  # 1000.5 everywhere
    stages = []
    with pytest.raises(PublishingError) as error:
        export_local(project, profile, EXTENT, stage_callback=stages.append)
    assert error.value.code == "Q2VT_PUB_IDENTITY"
    assert stages == ["PLAN"]  # reported at once, before the tile export
    assert "(terulet) is not unique, e.g. 1000.5" in error.value.message
    assert "Interaction tab" in error.value.message
    assert not os.path.exists(os.path.join(profile.output.local_directory, "arlo-teszt", "current.json"))


def test_key_check_counts_only_drawn_features(project, tmp_path):
    """The early key check looks at the features the export writes: square C
    (NULL zone) is drawn by no category, so its NULL key is no problem."""
    from publishing import qgis_model
    project, parcels = project
    profile = _profile(parcels, tmp_path, key_fields=["zone"])
    assert qgis_model.check_keys(project, profile, EXTENT) == []
    profile.layers[0].key_fields = ["tulaj"]  # the canary everywhere
    problems = qgis_model.check_keys(project, profile, EXTENT)
    assert "(tulaj) is not unique" in problems[0] and problems[-1] == qgis_model.KEY_ADVICE


def test_export_scoped_identity_without_key(project, tmp_path):
    project, parcels = project
    profile = _profile(parcels, tmp_path, key_fields=[])
    result = export_local(project, profile, EXTENT)
    manifest = json.load(open(os.path.join(result.release.release_dir, "manifest.json"), encoding="utf-8"))
    assert manifest["layers"][0]["identityScope"] == "export"
    keys = set()
    with open_pmtiles(os.path.join(result.release.release_dir, "data", "map.pmtiles")) as archive:
        for _, data in archive.tiles():
            for info in mvt.decode(data).values():
                keys.update(f["properties"].get("q2vt_feature_key") for f in info["features"])
    keys.discard(None)
    assert keys == {f"e{feature.id()}" for feature in parcels.getFeatures() if feature["zone"]}


def test_unapproved_field_in_tiles_is_refused(project, tmp_path):
    """Legacy "All fields" without the explicit include_all_fields approval
    cannot leak: the disclosure validator rejects the release."""
    project, parcels = project
    profile = _profile(parcels, tmp_path)
    from publishing import qgis_model
    original = qgis_model.tile_fields
    qgis_model.tile_fields = lambda p: {parcels.id(): ["zone", "tulaj"]}  # simulate a bug
    try:
        with pytest.raises(PublishingError) as error:
            export_local(project, profile, EXTENT)
    finally:
        qgis_model.tile_fields = original
    assert error.value.code == "Q2VT_PUB_FIELD_DISCLOSURE"
