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
from qgis.PyQt.QtCore import Qt
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


def test_labels_only_limit_keeps_the_features(plugin, tmp_path):
    """Labels hidden when zoomed out beyond 1:40 000 (~zoom 12.9): their
    style layers start there and no tile below it has them; the parcels
    themselves are still drawn and tiled from the first zoom."""
    from publishing import mvt
    from publishing.controller import export_local
    from publishing.models import LayerConfig, PopupField, PublicationProfile
    project = reset_project()
    parcels = _parcels(str(tmp_path / "parcels.gpkg"))
    project.addMapLayer(parcels)
    profile = PublicationProfile(title="Feliratok", slug="feliratok", locale="hu")
    profile.view.min_zoom, profile.view.max_zoom = 11, 14
    profile.output.local_directory = str(tmp_path / "out")
    config = LayerConfig(parcels.id(), key_fields=["hrsz"], popup_fields=[PopupField("hrsz")])
    config.labels_min_scale = 40000.0
    profile.layers = [config]
    result = export_local(project, profile, EXTENT)
    style = json.load(open(os.path.join(result.export_dir, "style", "style.json"), encoding="utf-8"))
    tiled = [layer for layer in style["layers"] if layer.get("source-layer")]
    labels = [layer for layer in tiled if "text-field" in layer.get("layout", {})]
    features = [layer for layer in tiled if layer["type"] == "fill"]
    assert labels and features
    assert min(layer.get("minzoom", 0) for layer in labels) >= 12.5
    assert max(layer.get("minzoom", 0) for layer in features) < 12
    label_sources = {layer["source-layer"] for layer in labels}
    with sqlite3.connect(os.path.join(result.export_dir, "tiles.mbtiles")) as conn:
        rows = conn.execute("SELECT zoom_level, tile_data FROM tiles").fetchall()
    by_zoom = {}
    for zoom, data in rows:
        by_zoom.setdefault(zoom, set()).update(mvt.layer_names(mvt.payload(bytes(data))))
    assert min(by_zoom) == 11                       # the parcels from the first zoom on
    assert not by_zoom[11] & label_sources          # no labels where they are hidden
    assert by_zoom[max(by_zoom)] & label_sources    # and they are there zoomed in
    assert not parcels.hasScaleBasedVisibility()


def test_zoomed_out_load_suggests_feature_and_label_zooms(plugin, tmp_path):
    """A dense layer of small parcels with labels: zoomed out it is heavy and
    its features are specks, so a feature zoom is suggested; its labels mostly
    cannot be placed until zoomed in further, so a later label zoom is
    suggested. A light layer gets no suggestion."""
    from qgis.core import QgsFeature, QgsField, QgsGeometry, QgsVectorLayer
    from qgis.PyQt.QtCore import QVariant
    from publishing import zoom_load
    from publishing.models import LayerConfig, PublicationProfile
    project = reset_project()
    dense = QgsVectorLayer("Polygon?crs=EPSG:3857", "Sűrű", "memory")
    dense.dataProvider().addAttributes([QgsField("hrsz", QVariant.String)])
    dense.updateFields()
    features = []
    x0, y0, step = 2119000.0, 6019000.0, 20.0     # 200 × 200 parcels of 18 m in 4 × 4 km
    for i in range(200):
        for j in range(200):
            feature = QgsFeature(dense.fields())
            feature.setAttributes([f"{i * 200 + j}/{j % 7}"])
            x, y = x0 + i * step, y0 + j * step
            feature.setGeometry(QgsGeometry.fromWkt(
                f"POLYGON(({x} {y},{x + 18} {y},{x + 18} {y + 18},{x} {y + 18},{x} {y}))"))
            features.append(feature)
    dense.dataProvider().addFeatures(features)
    from qgis.core import (Qgis, QgsPalLayerSettings, QgsTextFormat,  # pylint: disable=import-outside-toplevel
                           QgsVectorLayerSimpleLabeling)
    settings = QgsPalLayerSettings()
    settings.fieldName = "hrsz"
    settings.placement = Qgis.LabelPlacement.OverPoint
    fmt = QgsTextFormat()
    fmt.setSize(9)
    settings.setFormat(fmt)
    dense.setLabeling(QgsVectorLayerSimpleLabeling(settings))
    dense.setLabelsEnabled(True)
    light = _parcels(str(tmp_path / "light.gpkg"))
    project.addMapLayer(dense)
    project.addMapLayer(light)
    profile = PublicationProfile(title="Terhelés", slug="terheles", locale="hu")
    profile.view.min_zoom, profile.view.max_zoom = 8, 18
    profile.layers = [LayerConfig(dense.id()), LayerConfig(light.id())]
    loads, factor = zoom_load.analyse(project, profile, EXTENT)
    by_id = {load.layer_id: load for load in loads}
    assert loads[0].layer_id == dense.id()          # the heaviest first
    load = by_id[dense.id()]
    assert load.features == 40000 and load.labels and load.label_share == 1.0
    assert load.shown_from == 8 and load.heavy(8)
    assert load.tile_features[8] == 40000           # zoomed out: all in one tile
    # 18 m parcels are 2 px wide around zoom 14 (at 47.5° latitude, Web Mercator m).
    assert load.suggested_from is not None and 12 <= load.suggested_from <= 15
    assert load.suggested_labels_from is not None and load.suggested_labels_from > load.suggested_from
    assert by_id[light.id()].suggested_from is None
    assert by_id[light.id()].suggested_labels_from is None  # 4 labels: no load
    # The suggested zoom's scale: rounded so the tiles start at that zoom.
    scale = zoom_load.scale_for_zoom(factor, load.suggested_from)
    assert int(zoom_load.zoom_for_scale(factor, scale)) == load.suggested_from
    # A labels-only limit already set: its labels start there.
    profile.layers[0].labels_min_scale = zoom_load.scale_for_zoom(factor, 17)
    again = {l.layer_id: l for l in zoom_load.analyse(project, profile, EXTENT)[0]}[dense.id()]
    assert again.labels_from == 17 and again.shown_from == 8


def test_zoomed_out_load_dialog_applies_the_checked_limits(plugin, monkeypatch, tmp_path):
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: None)
    project = reset_project()
    parcels = _parcels(str(tmp_path / "parcels.gpkg"))
    project.addMapLayer(parcels)
    from q2vt_plugin.src.gui.publish_dialog import PublishDialog  # pylint: disable=import-error
    from q2vt_plugin.src.gui.zoom_load_dialog import ZoomLoadDialog, _ZoomPick  # pylint: disable=import-error
    from q2vt_plugin.src.publishing import zoom_load  # pylint: disable=import-error
    dialog = PublishDialog(iface=None)
    dialog.profile.view.extent = [EXTENT.xMinimum(), EXTENT.yMinimum(), EXTENT.xMaximum(), EXTENT.yMaximum()]
    item = next(dialog._tree_items())  # pylint: disable=protected-access
    item.setCheckState(1, Qt.CheckState.Checked)
    # The analysis itself is covered above; here a heavy result is offered.
    load = zoom_load.LayerLoad(parcels.id(), parcels.name(), "polygon", 50000, 11,
                               tile_features={z: 50000 for z in range(11, 17)},
                               tile_bytes={z: 2_000_000 for z in range(11, 17)},
                               suggested_from=13, labels=True, labels_from=11,
                               label_bytes={z: 900_000 for z in range(11, 17)},
                               suggested_labels_from=15)
    monkeypatch.setattr(zoom_load, "analyse", lambda *a: ([load], 1.1e8))
    seen = {}

    def accept(window):
        seen["window"] = window
        assert isinstance(window, ZoomLoadDialog)
        assert "(zoom 11, all layers together)" in window.summary.text()
        picks = window.picks[parcels.id()]
        assert isinstance(picks["features"], _ZoomPick) and picks["features"].spin.value() == 13
        picks["labels"].spin.setValue(16)          # the user moves the labels one zoom further
        return True

    assert dialog.analyse_zoom_load(accept)
    low, high, labels_low = dialog._item_scales(item)  # pylint: disable=protected-access
    assert (low, high) == (zoom_load.scale_for_zoom(1.1e8, 13), 0.0)
    assert labels_low == zoom_load.scale_for_zoom(1.1e8, 16)
    assert "labels 1:" in item.text(5)
    config = dialog.collect().layers[0]
    assert (config.min_scale, config.labels_min_scale) == (low, labels_low)
    from q2vt_plugin.src.publishing.profile import dumps, load_profile  # pylint: disable=import-error
    assert load_profile(dumps(dialog.collect())).layers[0].labels_min_scale == labels_low
    # Unchecking a suggestion leaves that limit as it is.
    window = ZoomLoadDialog(None, [load], 1.1e8, (11, 16))
    window.picks[parcels.id()]["features"].check.setChecked(False)
    assert window.chosen() == {parcels.id(): (None, zoom_load.scale_for_zoom(1.1e8, 15))}
    # The Visible scales dialog shows and returns the labels limit too.
    from q2vt_plugin.src.gui.scale_range import ScaleRangeDialog  # pylint: disable=import-error
    scales = ScaleRangeDialog(None, 25000.0, 0.0, 1, "", None, 4000.0)
    assert scales.labels_check.isChecked() and scales.values() == (25000.0, 0.0, 4000.0)
    scales.labels_check.setChecked(False)
    assert scales.values() == (25000.0, 0.0, 0.0)
    dialog.close()
