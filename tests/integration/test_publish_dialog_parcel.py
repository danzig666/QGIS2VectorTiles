"""Publish window, Parcel report tab: choosing an optional zone regulations
table picks its zone code field by itself, and a table without one gives a
plain explanation instead of an internal message."""

from qgis.core import QgsFeature, QgsVectorLayer
from qgis.PyQt.QtWidgets import QMessageBox

from q2vt_fixtures import reset_project


def _layer(uri, name, rows):
    layer = QgsVectorLayer(uri, name, "memory")
    for values in rows:
        feature = QgsFeature(layer.fields())
        feature.setAttributes(values)
        layer.dataProvider().addFeatures([feature])
    return layer


def test_regulation_table_code_field(plugin, monkeypatch):
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: None)
    project = reset_project()
    zones = _layer("Polygon?crs=EPSG:23700&field=szab_ov:string", "Övezetek", [["Lke-1"]])
    table = _layer("None?field=SZAB_OV:string&field=max_magassag:double", "HÉSZ táblázat", [["Lke-1", 7.5]])
    other = _layer("None?field=nev:string", "Más tábla", [["x"]])
    for layer in (zones, table, other):
        project.addMapLayer(layer)
    from q2vt_plugin.src.gui.publish_dialog import PublishDialog  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.profile import validate  # pylint: disable=import-error
    dialog = PublishDialog(iface=None)
    dialog.p_zoning.setLayer(zones)
    dialog.p_code.setField("szab_ov")
    dialog.p_regulation.setLayer(table)
    assert dialog.p_regulation_code.currentField() == "SZAB_OV"  # same name, any case
    dialog.p_regulation.setLayer(other)
    assert dialog.p_regulation_code.currentField() == ""          # nothing to guess
    profile = dialog.collect()
    profile.parcel_info.enabled = True
    message = next(e for e in validate(profile) if "regulations table" in e)
    assert "zone code field" in message and "optional" in message and "parcelInfo" not in message
    dialog.p_regulation.setLayer(None)                             # no table: fine
    profile = dialog.collect()
    profile.parcel_info.enabled = True
    assert not [e for e in validate(profile) if "regulations table" in e]
    dialog.close()
