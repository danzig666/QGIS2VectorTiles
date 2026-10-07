"""Publish window, layer settings: raster layers in the tree, several rows
changed at once (a group applies to its layers), layers and groups that
cannot be switched off, QGIS map themes (select layers, publish as views),
raster and basemap settings — all saved in the project file and read back."""

import os
import sys

import pytest
from qgis.core import QgsLayerTreeModel, QgsMapThemeCollection, QgsProject, QgsRasterLayer
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QMessageBox

from q2vt_fixtures import reset_project

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_publishing_pipeline import _parcels  # noqa: E402  pylint: disable=wrong-import-position
from test_publishing_raster import make_raster  # noqa: E402  pylint: disable=wrong-import-position

CHECKED, UNCHECKED = Qt.CheckState.Checked, Qt.CheckState.Unchecked


@pytest.fixture
def setup(plugin, tmp_path, monkeypatch):
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: None)
    project = reset_project()
    parcels = _parcels(str(tmp_path / "parcels.gpkg"))
    zones = _parcels(str(tmp_path / "zones.gpkg"))
    zones.setName("Övezetek")
    ortho = QgsRasterLayer(make_raster(tmp_path / "ortho.tif"), "Ortofotó")
    root = project.layerTreeRoot()
    plan, background = root.addGroup("Szabályozás"), root.addGroup("Háttér")
    for layer in (parcels, zones, ortho):
        project.addMapLayer(layer, False)
    plan.addLayer(parcels)
    plan.addLayer(zones)
    background.addLayer(ortho)
    root.findLayer(zones.id()).setItemVisibilityChecked(False)
    project.mapThemeCollection().insert("Csak telkek", QgsMapThemeCollection.createThemeFromCurrentState(
        root, QgsLayerTreeModel(root)))
    project.setFileName(str(tmp_path / "terv.qgz"))
    from q2vt_plugin.src.gui.publish_dialog import PublishDialog  # pylint: disable=import-error
    dialog = PublishDialog(iface=None)
    dialog.profile.view.extent = [2119000, 6019000, 2123000, 6023000]
    dialog.o_dir.setText(str(tmp_path / "out"))
    yield dialog, project, parcels, zones, ortho
    dialog.close()


def _items(dialog):
    return {item.data(0, Qt.ItemDataRole.UserRole): item for item in dialog._tree_items()}  # pylint: disable=protected-access


def _groups(dialog):
    return {tuple(item.data(0, Qt.ItemDataRole.UserRole + 1)): item for item in dialog._group_items()}  # pylint: disable=protected-access


def test_raster_layers_are_listed_and_groups_apply_to_their_layers(setup):
    dialog, project, parcels, zones, ortho = setup
    items = _items(dialog)
    assert ortho.id() in items and "raster" in items[ortho.id()].text(0)
    groups = _groups(dialog)
    dialog.tree.selectAll()
    dialog.apply_to_selection(1, False)  # nothing published
    assert all(item.checkState(1) == UNCHECKED for item in items.values())
    # Select a group and publish: every layer inside is published; partial state shown.
    dialog.tree.clearSelection()
    groups[("Szabályozás",)].setSelected(True)
    dialog.apply_to_selection(1, True)
    assert items[parcels.id()].checkState(1) == CHECKED and items[zones.id()].checkState(1) == CHECKED
    assert groups[("Szabályozás",)].checkState(1) == CHECKED
    assert items[ortho.id()].checkState(1) == UNCHECKED and groups[("Háttér",)].checkState(1) == UNCHECKED
    # Several rows at once: always shown (cannot be switched off) => visible at start.
    dialog.tree.clearSelection()
    items[zones.id()].setSelected(True)
    items[ortho.id()].setSelected(True)
    dialog.apply_to_selection(3, False)
    for layer in (zones, ortho):
        assert items[layer.id()].checkState(3) == UNCHECKED and items[layer.id()].checkState(2) == CHECKED
        assert items[layer.id()].checkState(1) == CHECKED
    # Group checkbox in the Publish column publishes / unpublishes its layers.
    groups[("Háttér",)].setCheckState(1, UNCHECKED)
    assert items[ortho.id()].checkState(1) == UNCHECKED
    profile = dialog.collect()
    assert profile.layer(zones.id()).toggleable is False and profile.layer(zones.id()).initially_visible
    assert profile.layer(ortho.id()).included is False


def test_map_theme_selects_layers_and_becomes_a_view(setup):
    dialog, project, parcels, zones, ortho = setup
    items = _items(dialog)
    dialog.tree.selectAll()
    dialog.apply_to_selection(1, False)  # start with nothing published
    dialog.tree.clearSelection()
    dialog.theme_pick.setCurrentText("Csak telkek")
    dialog._apply_theme(publish=True)  # pylint: disable=protected-access
    assert items[parcels.id()].checkState(1) == CHECKED and items[ortho.id()].checkState(1) == CHECKED
    assert items[zones.id()].checkState(1) == UNCHECKED  # hidden in the theme: not published
    # Additive: a second theme publishes its layers too and unpublishes none.
    root = project.layerTreeRoot()
    for layer, shown in ((parcels, False), (zones, True), (ortho, False)):
        root.findLayer(layer.id()).setItemVisibilityChecked(shown)
    project.mapThemeCollection().insert("Csak övezetek", QgsMapThemeCollection.createThemeFromCurrentState(
        root, QgsLayerTreeModel(root)))
    dialog.theme_pick.addItem("Csak övezetek")
    dialog.theme_pick.setCurrentText("Csak övezetek")
    dialog._apply_theme(publish=True)  # pylint: disable=protected-access
    assert all(items[layer.id()].checkState(1) == CHECKED for layer in (parcels, zones, ortho))
    dialog.themes_list.item(0).setCheckState(CHECKED)
    dialog.theme_initial.setCurrentIndex(dialog.theme_initial.findData("Csak telkek"))
    profile = dialog.collect()
    assert profile.themes.names == ["Csak telkek"] and profile.themes.initial == "Csak telkek"


def test_group_lock_raster_and_basemap_settings_round_trip(setup):
    dialog, project, parcels, zones, ortho = setup
    items, groups = _items(dialog), _groups(dialog)
    for layer in (parcels, ortho):
        items[layer.id()].setCheckState(1, CHECKED)
    groups[("Szabályozás",)].setCheckState(3, UNCHECKED)  # the group cannot be switched off
    dialog._fill_interaction_layers()  # pylint: disable=protected-access
    rows = [dialog.i_layers.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dialog.i_layers.count())]
    dialog.i_layers.setCurrentRow(rows.index(ortho.id()))
    assert dialog.i_stack.currentIndex() == 1  # raster settings page
    dialog.r_format.setCurrentIndex(dialog.r_format.findData("webp"))
    dialog.r_detail.setCurrentIndex(dialog.r_detail.findData(15))
    dialog.i_layers.setCurrentRow(rows.index(parcels.id()))
    assert dialog.i_stack.currentIndex() == 0
    dialog.b_kind.setCurrentIndex(dialog.b_kind.findData("protomaps"))
    dialog.b_flavors["dark"].setChecked(True)
    dialog.b_initial.setCurrentIndex(dialog.b_initial.findData("dark"))
    dialog.b_custom.setChecked(True)
    dialog.b_source.setText("/data/hungary.pmtiles")
    assert dialog.save_settings()
    assert project.write()
    path = project.fileName()
    ortho_id, zones_id = ortho.id(), zones.id()
    project = reset_project()
    assert project.read(path)
    from q2vt_plugin.src.gui.publication_profiles import active_profile  # pylint: disable=import-error
    profile, _ = active_profile(project)
    assert profile.group(["Szabályozás"]).toggleable is False
    raster = profile.layer(ortho_id)
    assert (raster.raster_format, raster.raster_max_zoom, raster.raster_hidpi) == ("webp", 15, False)
    assert profile.basemap.kind == "protomaps" and profile.basemap.initial == "dark"
    assert "dark" in profile.basemap.flavors and profile.basemap.source == "/data/hungary.pmtiles"
    assert QgsProject.instance().layerTreeRoot().findLayer(zones_id) is not None


def test_scales_column_shows_the_qgis_range(setup):
    """No web limit set: the column shows the layer's own QGIS range, not just "(QGIS)"."""
    dialog, project, parcels, zones, ortho = setup
    parcels.setScaleBasedVisibility(True)
    parcels.setMinimumScale(2000)  # QGIS: visible from 1:2000 down to more detail
    parcels.setMaximumScale(0)
    item = _items(dialog)[parcels.id()]
    dialog._set_item_scales(item, 0, 0)  # pylint: disable=protected-access
    text = item.text(5)
    assert text.startswith("(QGIS 1:2") and "000 –" in text and text.endswith(")"), text
    dialog._set_item_scales(item, 5000, 0)  # pylint: disable=protected-access
    assert "QGIS" not in item.text(5) and "QGIS layer: 1:2" in item.toolTip(5)


def test_publish_the_visible_layers(setup):
    """"Publish the visible layers": exactly the layers visible in the QGIS
    Layers panel now are published (and visible at start); a layer in a
    hidden group counts as hidden. New raster layers default to WebP."""
    dialog, project, parcels, zones, ortho = setup
    items = _items(dialog)
    items[parcels.id()].setCheckState(1, UNCHECKED)
    items[zones.id()].setCheckState(1, CHECKED)
    project.layerTreeRoot().findGroup("Háttér").setItemVisibilityChecked(False)
    dialog.publish_visible_layers()
    assert items[parcels.id()].checkState(1) == CHECKED and items[parcels.id()].checkState(2) == CHECKED
    assert items[zones.id()].checkState(1) == UNCHECKED  # hidden in QGIS
    assert items[ortho.id()].checkState(1) == UNCHECKED  # in a hidden group
    project.layerTreeRoot().findGroup("Háttér").setItemVisibilityChecked(True)
    dialog.publish_visible_layers()
    assert items[ortho.id()].checkState(1) == CHECKED
    from q2vt_plugin.src.publishing.models import LayerConfig  # pylint: disable=import-error
    assert LayerConfig(ortho.id()).raster_format == "webp"


def test_raster_sharpest_detail_in_metres(setup):
    """Raster detail is one choice in ground metres per pixel: like the image
    (the default; make_raster: 10 Web Mercator m = 6.76 ground m per pixel at
    47.5° N, zoom 14), like the map, or a pixel size. The estimate compares it
    with the image. The removed "sharp on high-resolution screens" option
    (512 px tiles) becomes the same detail one zoom further."""
    dialog, project, parcels, zones, ortho = setup
    _items(dialog)[ortho.id()].setCheckState(1, CHECKED)
    dialog.e_max_zoom.setValue(12)
    dialog._fill_interaction_layers()  # pylint: disable=protected-access
    rows = [dialog.i_layers.item(i).data(Qt.ItemDataRole.UserRole) for i in range(dialog.i_layers.count())]
    dialog.i_layers.setCurrentRow(rows.index(ortho.id()))
    assert dialog.r_detail.currentData() == "image"
    assert dialog.r_detail.currentText() == "Like the image: 6.76 m per pixel (recommended)"
    text = dialog.r_estimate.text()
    assert "Image: 6.76 m per pixel" in text and "as sharp as the image" in text and "zooms" in text, text
    dialog.r_detail.setCurrentIndex(dialog.r_detail.findData("map"))
    assert "4× coarser than the image" in dialog.r_estimate.text()
    # Pixel sizes down from one step finer than the image (6.76 m: zoom 15).
    assert [dialog.r_detail.itemData(i) for i in range(dialog.r_detail.count())][:4] == [
        "image", "map", 15, 13]
    dialog.r_detail.setCurrentIndex(dialog.r_detail.findData(15))
    assert "finer than the image" in dialog.r_detail.currentText()
    assert "finer than the image" in dialog.r_estimate.text()
    dialog.i_layers.setCurrentRow(rows.index(parcels.id()))
    assert dialog.save_settings()
    saved = dialog.profile.layer(ortho.id())
    assert (saved.raster_max_zoom, saved.raster_match_native, saved.raster_hidpi) == (15, False, False)
    # An older setting with 512 px tiles: the same detail, one zoom further.
    dialog.layer_configs[ortho.id()].raster_max_zoom = 16
    dialog.layer_configs[ortho.id()].raster_hidpi = True
    dialog.i_layers.setCurrentRow(rows.index(ortho.id()))
    assert dialog.r_detail.currentData() == 17  # kept although finer than the list
