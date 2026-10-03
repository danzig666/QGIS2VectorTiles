"""hu-hesz variant: the Hungarian zoning plan preset fills the parcel report
from the usual layer and field names (Földrészletek/hrsz, szab_ov, Szabályozási
vonal, Övezethatár, protected areas and safety zones), keeps what the user
already wrote and leaves the report off when the plan's layers are missing."""

import os

from qgis.core import QgsFeature, QgsGeometry, QgsProject, QgsVectorLayer
from qgis.PyQt.QtWidgets import QMessageBox

FIXTURE_PROJECT = os.environ.get("Q2VT_HESZ_PROJECT", "")  # optional real plan (not in the repo)


def _layer(project, name, geometry, fields, wkt):
    layer = QgsVectorLayer(f"{geometry}?crs=EPSG:23700&" + "&".join(f"field={f}" for f in fields), name, "memory")
    feature = QgsFeature(layer.fields())
    feature.setGeometry(QgsGeometry.fromWkt(wkt))
    layer.dataProvider().addFeatures([feature])
    project.addMapLayer(layer)
    return layer


def _plan(project):
    square = "POLYGON((650000 230000, 650100 230000, 650100 230100, 650000 230100, 650000 230000))"
    line = "LINESTRING(650050 229990, 650050 230110)"
    layers = {
        "parcels": _layer(project, "Földrészletek", "Polygon",
                          ["hrsz:string", "kozter_nev:string", "kivett:string", "fekves:string"], square),
        "codes": _layer(project, "Szabályozás övezetkódok", "Polygon",
                        ["szab_ov:string", "p_beepszaz:double", "p_beepmag:double", "p_zold:double"], square),
        "colours": _layer(project, "Szabályozás színek", "Polygon",
                          ["szab_ov:string", "p_beepszaz:double", "p_beepmag:double", "p_zold:double"], square),
        "reg": _layer(project, "Szabályozási vonal", "LineString", ["lszerk_ov:string", "rszerk_ov:string"], line),
        "zone": _layer(project, "Övezethatár", "LineString", ["lszerk_ov:string", "rszerk_ov:string"], line),
        "natura": _layer(project, "Natura 2000 terület SCI (SAC)", "Polygon", ["NEV:string"], square),
        "lap": _layer(project, "Ex lege védett láp", "Polygon", ["NEV:string"], square),
        "kv": _layer(project, "Térségi ellátást biztosító 132 kV-os elosztó hálózat", "LineString",
                     ["TELEP:string"], line),
        "tajertek": _layer(project, "Egyedi tájérték", "Point", ["NEV:string"], "POINT(650050 230050)"),
        "monument_env": _layer(project, "Műemléki környezet", "Polygon", ["azon:string"], square),
        "contours": _layer(project, "Szintvonal", "LineString", ["MAGASSAG:double"], line),
        "labels": _layer(project, "Alrészlet feliratok", "Polygon", ["hrsz:string"], square),
        "admin": _layer(project, "Közigazgatási határ", "Polygon", ["hrsz:string"], square),
    }
    from qgis.core import QgsNullSymbolRenderer  # the code layer only carries labels
    layers["codes"].setRenderer(QgsNullSymbolRenderer())
    return layers


def _preset(plugin):
    from q2vt_plugin.src.publishing import presets  # pylint: disable=import-error
    return next(m for m in presets.available() if m.PRESET_ID == "hu-hesz")


def test_preset_fills_the_parcel_report(plugin):
    from q2vt_plugin.src.publishing.models import PublicationProfile, RestrictionConfig  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.profile import validate  # pylint: disable=import-error
    project = QgsProject()
    layers = _plan(project)
    profile = PublicationProfile()
    # A restriction the user already described keeps its text.
    profile.parcel_info.restrictions = [RestrictionConfig(layers["natura"].id(), "Saját cím", "Saját megjegyzés")]
    notes = _preset(plugin).apply(project, profile)
    info = profile.parcel_info
    assert info.enabled and profile.locale == "hu"
    assert info.parcel_layer_id == layers["parcels"].id() and info.key_field == "hrsz"
    assert [f.field for f in info.fields] == ["kozter_nev", "kivett", "fekves"]
    assert info.zoning_layer_id == layers["colours"].id() and info.zoning_code_field == "szab_ov"
    assert [(f.field, f.type) for f in info.zoning_fields] == [
        ("p_beepszaz", "number"), ("p_beepmag", "number"), ("p_zold", "number")]
    assert [(c.layer_id, c.title) for c in info.cut_lines] == [
        (layers["reg"].id(), "Szabályozási vonal"), (layers["zone"].id(), "Övezethatár")]
    by_layer = {r.layer_id: r for r in info.restrictions}
    assert set(by_layer) == {layers[k].id() for k in ("natura", "lap", "kv", "tajertek", "monument_env")}
    assert by_layer[layers["natura"].id()].title == "Saját cím"
    assert by_layer[layers["natura"].id()].note == "Saját megjegyzés"
    assert "Kötv." in by_layer[layers["monument_env"].id()].reference
    assert by_layer[layers["monument_env"].id()].name_field == "azon"
    assert by_layer[layers["tajertek"].id()].buffer_m == 10.0 and by_layer[layers["lap"].id()].name_field == "NEV"
    assert info.disclaimer.startswith("Tájékoztató")
    assert profile.layer(layers["parcels"].id()).included
    # Every parcel number shown (smaller where it does not fit) and searchable.
    assert profile.layer(layers["parcels"].id()).label_always
    assert profile.layer(layers["parcels"].id()).search_fields == ["hrsz"]
    # Measurements snap to the parcels and the cut lines.
    assert profile.layer(layers["parcels"].id()).snap
    assert all(profile.layer(layers[k].id()).snap for k in ("reg", "zone"))
    assert not [e for e in validate(profile) if "parcelInfo" in e]
    assert any("javaslatok" in note for note in notes)


def test_preset_without_plan_layers_leaves_report_off(plugin):
    from q2vt_plugin.src.publishing.models import PublicationProfile  # pylint: disable=import-error
    project = QgsProject()
    _layer(project, "Utak", "LineString", ["name:string"], "LINESTRING(0 0, 1 1)")
    profile = PublicationProfile()
    notes = _preset(plugin).apply(project, profile)
    assert not profile.parcel_info.enabled and notes and "Nem található" in notes[0]


def test_preset_from_the_publish_window(plugin, monkeypatch):
    from q2vt_plugin.src.gui.publish_dialog import PublishDialog  # pylint: disable=import-error
    project = QgsProject.instance()
    layers = _plan(project)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    try:
        dialog = PublishDialog(iface=None)
        assert dialog.btn_preset.isVisibleTo(dialog)
        dialog.apply_preset(next(m for m in dialog.presets if m.PRESET_ID == "hu-hesz"))
        info = dialog.collect().parcel_info
        assert info.enabled and info.zoning_code_field == "szab_ov" and len(info.cut_lines) == 2
        dialog.close()
    finally:
        project.removeMapLayers([layer.id() for layer in layers.values()])


def test_preset_on_a_real_plan(plugin):
    """Opt-in: Q2VT_HESZ_PROJECT=/path/plan.qgs (a real settlement plan)."""
    import pytest
    if not os.path.isfile(FIXTURE_PROJECT):
        pytest.skip("set Q2VT_HESZ_PROJECT to a real plan project")
    from q2vt_plugin.src.publishing.models import PublicationProfile  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.profile import validate  # pylint: disable=import-error
    project = QgsProject()
    assert project.read(FIXTURE_PROJECT)
    profile = PublicationProfile()
    notes = _preset(plugin).apply(project, profile)
    print("\n".join(notes))
    for item in profile.parcel_info.restrictions:
        print(" -", item.title, "|", item.name_field, item.buffer_m, "|", item.reference)
    assert profile.parcel_info.enabled and profile.parcel_info.cut_lines
    assert not [e for e in validate(profile) if "parcelInfo" in e]
