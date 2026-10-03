"""Web-only visible scale range of layers: set in the Publish window's
layer list (one layer, or many at once through a group or the selection),
saved in the profile, and applied by the export (style zoom range, no
tiles where hidden, the viewer model's zoom range) on top of the layer's
own QGIS scale range - without changing the project."""

import json
import os
import sqlite3
import sys

import pytest
from qgis.PyQt.QtWidgets import QMessageBox

from q2vt_fixtures import reset_project

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_publishing_pipeline import EXTENT, _parcels  # noqa: E402  pylint: disable=wrong-import-position


def test_combine_scale_ranges(plugin):
    from q2vt_plugin.src.core.rules_flattener import combine_scale_ranges  # pylint: disable=import-error
    assert combine_scale_ranges(0, 0, 25000, 0) == (25000, 0)
    assert combine_scale_ranges(100000, 1000, 25000, 0) == (25000, 1000)   # the narrower wins
    assert combine_scale_ranges(10000, 0, 25000, 500) == (10000, 500)


def test_scales_column_sets_many_layers_and_is_saved(plugin, monkeypatch, tmp_path):
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: None)
    project = reset_project()
    group = project.layerTreeRoot().addGroup("Részletek")
    layers = []
    for index in range(2):
        layer = _parcels(str(tmp_path / f"p{index}.gpkg"))
        layer.setName(f"Réteg {index}")
        project.addMapLayer(layer, False)
        group.addLayer(layer)
        layers.append(layer)
    from q2vt_plugin.src.gui.publish_dialog import PublishDialog  # pylint: disable=import-error
    dialog = PublishDialog(iface=None)
    group_item = next(dialog._group_items())  # pylint: disable=protected-access
    group_item.setSelected(True)               # the group: both layers
    assert dialog.edit_scales((25000.0, 0.0))
    texts = [item.text(5) for item in dialog._tree_items()]  # pylint: disable=protected-access
    assert texts == ["1:25 000 –", "1:25 000 –"]
    profile = dialog.collect()
    assert [(c.min_scale, c.max_scale) for c in profile.layers] == [(25000.0, 0.0)] * 2
    from q2vt_plugin.src.publishing.profile import dumps, load_profile  # pylint: disable=import-error
    assert load_profile(dumps(profile)).layers[0].min_scale == 25000.0
    assert not layers[0].hasScaleBasedVisibility()  # the project is not changed
    dialog.close()


def test_export_hides_and_does_not_tile_beyond_the_limit(plugin, tmp_path):
    from publishing.controller import export_local
    from publishing.models import LayerConfig, PopupField, PublicationProfile
    project = reset_project()
    parcels = _parcels(str(tmp_path / "parcels.gpkg"))
    project.addMapLayer(parcels)
    profile = PublicationProfile(title="Lépték", slug="lepteket", locale="hu")
    profile.view.min_zoom, profile.view.max_zoom = 11, 14
    profile.output.local_directory = str(tmp_path / "out")
    config = LayerConfig(parcels.id(), key_fields=["hrsz"], popup_fields=[PopupField("hrsz")])
    config.min_scale = 40000.0   # hidden when zoomed out beyond 1:40 000 (~zoom 12.9)
    profile.layers = [config]
    result = export_local(project, profile, EXTENT)
    style = json.load(open(os.path.join(result.export_dir, "style", "style.json"), encoding="utf-8"))
    zooms = [layer.get("minzoom") for layer in style["layers"] if layer.get("source-layer")]
    assert zooms and min(zooms) >= 12.5
    with sqlite3.connect(os.path.join(result.export_dir, "tiles.mbtiles")) as conn:
        levels = {z for (z,) in conn.execute("SELECT DISTINCT zoom_level FROM tiles")}
    assert levels and min(levels) >= 12      # no tiles where it is hidden
    assert not parcels.hasScaleBasedVisibility()


def test_legend_column_and_interaction_checkbox_are_one_setting(plugin, monkeypatch, tmp_path):
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: None)
    project = reset_project()
    group = project.layerTreeRoot().addGroup("Csoport")
    layers = []
    for index in range(2):
        layer = _parcels(str(tmp_path / f"l{index}.gpkg"))
        layer.setName(f"L{index}")
        project.addMapLayer(layer, False)
        group.addLayer(layer)
        layers.append(layer)
    from qgis.PyQt.QtCore import Qt  # pylint: disable=import-outside-toplevel
    from q2vt_plugin.src.gui.publish_dialog import PublishDialog  # pylint: disable=import-error
    dialog = PublishDialog(iface=None)
    items = list(dialog._tree_items())  # pylint: disable=protected-access
    assert all(item.checkState(4) == Qt.CheckState.Checked for item in items)  # default: shown
    next(dialog._group_items()).setSelected(True)  # pylint: disable=protected-access
    dialog.apply_to_selection(4, False)            # bulk: hide the group's layers from the legend
    assert [c.legend for c in dialog.collect().layers] == [False, False]
    # The Interaction tab shows it, and its checkbox changes the column.
    dialog.tabs.setCurrentIndex(1)
    dialog._fill_interaction_layers()               # pylint: disable=protected-access
    dialog.i_layers.setCurrentRow(0)
    assert not dialog.i_legend.isChecked()
    dialog.i_legend.setChecked(True)
    assert items[0].checkState(4) == Qt.CheckState.Checked
    assert [c.legend for c in dialog.collect().layers] == [True, False]
    dialog.close()
