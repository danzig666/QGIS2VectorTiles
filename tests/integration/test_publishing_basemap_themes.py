"""Vector basemap bundled from a Protomaps-schema extract (local file),
glyphs generated for its labels, flavors as separate style files; QGIS map
themes as viewer presets; layers and groups that cannot be switched off."""

import json
import os
import sys

from qgis.core import QgsMapThemeCollection, QgsProject, QgsRectangle

from publishing.basemap import FONTSTACKS, SOURCE_ID
from publishing.controller import export_local
from publishing.models import GroupConfig, LayerConfig
from publishing.provenance import group_logical_id, layer_logical_id
from publishing.validation import validate_pmtiles, vector_only_violations
from publishing_fixtures import protomaps_planet
from q2vt_fixtures import reset_project

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_publishing_pipeline import _parcels, _profile  # noqa: E402  pylint: disable=wrong-import-position

EXTENT = QgsRectangle(2119000, 6019000, 2123000, 6023000)


def _setup(tmp_path):
    project = reset_project()
    parcels = _parcels(str(tmp_path / "parcels.gpkg"))
    second = _parcels(str(tmp_path / "zones.gpkg"))
    second.setName("Övezetek")
    root = project.layerTreeRoot()
    group = root.addGroup("Alaptérkép")
    project.addMapLayer(parcels, False)
    project.addMapLayer(second, False)
    group.addLayer(parcels)
    root.addLayer(second)
    profile = _profile(parcels, tmp_path)
    profile.layers[0].initially_visible = True
    profile.layers.append(LayerConfig(second.id(), toggleable=False))
    profile.groups = [GroupConfig(["Alaptérkép"], toggleable=False)]
    # Theme "Csak övezetek": parcels off, zones on.
    root.findLayer(parcels.id()).setItemVisibilityChecked(False)
    collection = project.mapThemeCollection()
    collection.insert("Csak övezetek", QgsMapThemeCollection.createThemeFromCurrentState(
        root, _model(project)))
    root.findLayer(parcels.id()).setItemVisibilityChecked(True)
    profile.themes.names, profile.themes.initial = ["Csak övezetek"], ""
    return project, profile, parcels, second


def _model(project):
    from qgis.core import QgsLayerTreeModel
    return QgsLayerTreeModel(project.layerTreeRoot())


def test_basemap_themes_and_locked_layers(tmp_path):
    project, profile, parcels, second = _setup(tmp_path)
    planet = protomaps_planet(str(tmp_path / "planet.pmtiles"))
    profile.basemap.kind, profile.basemap.source = "protomaps", planet
    profile.basemap.flavors, profile.basemap.initial, profile.basemap.max_zoom = ["light", "dark"], "dark", 14
    result = export_local(project, profile, EXTENT)
    rel = result.release.release_dir
    manifest = json.load(open(os.path.join(rel, "manifest.json"), encoding="utf-8"))
    style = json.load(open(os.path.join(rel, "style.json"), encoding="utf-8"))
    # Basemap: its own archive, flavor files, generated glyphs; not in style.json.
    bm = manifest["basemap"]
    assert bm["initial"] == "dark" and [f["id"] for f in bm["flavors"]] == ["light", "dark"]
    assert bm["flavors"][1]["title"] == "Sötét" and "OpenStreetMap" in bm["attribution"]
    validate_pmtiles(os.path.join(rel, bm["source"]["href"]), sample=0)
    assert SOURCE_ID not in style["sources"] and not vector_only_violations(style)
    for flavor in bm["flavors"]:
        doc = json.load(open(os.path.join(rel, flavor["style"]), encoding="utf-8"))
        assert doc["layers"] and all(l.get("source", SOURCE_ID) == SOURCE_ID for l in doc["layers"])
    for stack in FONTSTACKS.values():
        assert os.listdir(os.path.join(rel, "glyphs", stack))
    # Themes: parcels off in the preset, zones (locked) on.
    themes = manifest["themes"]
    assert [p["title"] for p in themes["presets"]] == ["Csak övezetek"] and themes["initial"] is None
    preset = themes["presets"][0]
    assert preset["layers"][layer_logical_id(parcels.id())] is False
    assert preset["layers"][layer_logical_id(second.id())] is True
    # Locked layer and group.
    entry = {l["id"]: l for l in manifest["layers"]}
    assert entry[layer_logical_id(second.id())]["toggleable"] is False
    assert entry[layer_logical_id(parcels.id())]["toggleable"] is True
    group = next(g for g in manifest["groups"] if g["id"] == group_logical_id(("Alaptérkép",)))
    assert group["toggleable"] is False and manifest["ui"]["accent"].startswith("#")
    # The project's layer tree and themes are untouched.
    assert project.layerTreeRoot().findLayer(parcels.id()).isVisible()
    assert project.mapThemeCollection().mapThemes() == ["Csak övezetek"]
