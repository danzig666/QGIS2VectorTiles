"""Publish window: the extent from a layer (combo; follows the layer's
extent), a fixed extent, a readable summary instead of raw coordinates, the
setting saved in the profile; the extent drawn on the map canvas by clicking
two corners; and room for the layer list on the Interaction tab."""

import os
import sys

from qgis.core import QgsFeature, QgsGeometry
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QMessageBox

from q2vt_fixtures import reset_project

CHECKED = Qt.CheckState.Checked


def _dialog(plugin, monkeypatch, tmp_path):
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: None)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_publishing_pipeline import _parcels  # pylint: disable=import-error,import-outside-toplevel
    os.makedirs(str(tmp_path), exist_ok=True)
    parcels = _parcels(str(tmp_path / "parcels.gpkg"))
    project = reset_project()
    project.addMapLayer(parcels)
    from q2vt_plugin.src.gui.publish_dialog import PublishDialog  # pylint: disable=import-error
    return PublishDialog(iface=None), parcels


def test_extent_from_a_layer_follows_it(plugin, monkeypatch, tmp_path):
    dialog, parcels = _dialog(plugin, monkeypatch, tmp_path)
    assert dialog.e_extent_layer.currentLayer() is None
    assert "canvas" in dialog.extent_label.text()
    dialog.e_extent_layer.setLayer(parcels)
    view = dialog.collect().view
    assert view.extent_layer == parcels.id()
    box = parcels.extent()  # EPSG:3857 already
    assert view.extent == [box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()]
    text = dialog.extent_label.text()
    assert text.startswith("Layer extent") and parcels.name() in dialog.extent_label.toolTip() and " km" in text and "\nN " in text and "EPSG" not in text
    # The layer grows: the published extent follows it.
    parcels.startEditing()
    feature = QgsFeature(parcels.fields())
    feature.setGeometry(QgsGeometry.fromWkt(
        "POLYGON((2125000 6025000, 2125100 6025000, 2125100 6025100, 2125000 6025000))"))
    parcels.addFeature(feature)
    assert parcels.commitChanges()
    parcels.updateExtents()
    assert dialog._extent().xMaximum() >= 2125100 - 1e-6  # pylint: disable=protected-access
    # Saved and read back.
    from q2vt_plugin.src.publishing.profile import dumps, load_profile  # pylint: disable=import-error
    again = load_profile(dumps(dialog.collect()))
    assert again.view.extent_layer == parcels.id()
    dialog.close()


def test_fixed_extent_and_a_removed_layer(plugin, monkeypatch, tmp_path):
    dialog, parcels = _dialog(plugin, monkeypatch, tmp_path)
    dialog.profile.view.extent = [2119000, 6019000, 2123000, 6023000]
    dialog.e_extent_layer.setLayer(parcels)
    dialog.e_extent_layer.setLayer(None)            # back to the fixed extent
    assert dialog.profile.view.extent_layer == ""
    assert dialog.extent_label.text().startswith("Fixed extent")
    dialog.e_extent_layer.setLayer(parcels)
    profile = dialog.collect()
    from qgis.core import QgsProject  # pylint: disable=import-outside-toplevel
    QgsProject.instance().removeMapLayer(parcels.id())
    dialog._populate(profile)                       # pylint: disable=protected-access
    assert dialog.profile.view.extent_layer == "" and dialog.profile.view.extent  # last extent kept
    dialog.close()


def test_interaction_layer_list_has_room(plugin, monkeypatch, tmp_path):
    dialog, parcels = _dialog(plugin, monkeypatch, tmp_path)
    dialog.show()
    dialog.tabs.setCurrentIndex(1)
    assert dialog.i_layers.width() >= 220
    assert dialog.i_split.sizes()[0] >= 220
    dialog.close()


def test_settings_file_export_and_import_into_another_project(plugin, monkeypatch, tmp_path):
    dialog, parcels = _dialog(plugin, monkeypatch, tmp_path)
    dialog.e_title.setText("Arló terv")
    dialog.e_slug.setText("arlo-terv")
    dialog.e_extent_layer.setLayer(parcels)
    item = next(dialog._tree_items())  # pylint: disable=protected-access
    item.setCheckState(1, CHECKED)
    path = str(tmp_path / "arlo.q2vt.json")
    assert dialog.export_settings_file(path)
    original = dialog.collect()
    text = open(path, encoding="utf-8").read()
    assert parcels.name() in text and "secret" not in text.lower()
    old_id = parcels.id()
    dialog.close()

    # Another project: the same layer under a new id.
    other, copy = _dialog(plugin, monkeypatch, tmp_path / "other")
    assert copy.id() != old_id
    assert other.import_settings_file(path, same_map=True)
    profile = other.collect()
    assert profile.title == "Arló terv" and profile.slug == "arlo-terv"
    assert profile.view.extent_layer == copy.id()
    assert profile.layer(copy.id()) is not None and profile.layer(copy.id()).included
    assert profile.publication_id == original.publication_id
    assert other.import_settings_file(path, same_map=False)
    assert other.collect().publication_id != original.publication_id
    # Not a settings file: refused, nothing changed.
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]", encoding="utf-8")
    assert not other.import_settings_file(str(bad), same_map=True)
    assert other.collect().title == "Arló terv"
    other.close()


def test_window_remembers_its_size_and_position(plugin, monkeypatch, tmp_path):
    from qgis.core import QgsSettings  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtGui import QGuiApplication  # pylint: disable=import-outside-toplevel
    dialog, _parcels = _dialog(plugin, monkeypatch, tmp_path)
    QgsSettings().remove(dialog.GEOMETRY_KEY)
    screen = QGuiApplication.primaryScreen().availableGeometry()
    dialog.resize(700, 520)
    dialog.move(screen.left() + 40, screen.top() + 30)
    dialog.close()  # the Close button
    assert QgsSettings().value(dialog.GEOMETRY_KEY)
    again, _ = _dialog(plugin, monkeypatch, tmp_path / "again")
    assert (again.width(), again.height()) == (700, 520)
    assert abs(again.pos().x() - (screen.left() + 40)) <= 1 and abs(again.pos().y() - (screen.top() + 30)) <= 1
    again.resize(760, 600)
    again.reject()  # Escape
    third, _ = _dialog(plugin, monkeypatch, tmp_path / "third")
    assert (third.width(), third.height()) == (760, 600)
    QgsSettings().remove(dialog.GEOMETRY_KEY)


def test_settings_file_carries_the_object_storage_keys(plugin, monkeypatch, tmp_path):
    """At the owner's request the settings file also holds the R2/S3 keys
    (secret in plain text); importing them makes them usable at once."""
    import json  # pylint: disable=import-outside-toplevel
    dialog, _parcels = _dialog(plugin, monkeypatch, tmp_path)
    dialog.e_title.setText("Arló terv")
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("r2"))
    dialog.d_account.setText("0123456789abcdef0123456789abcdef")
    dialog.d_bucket.setText("maps")
    dialog.d_public.setText("https://maps.example.hu")
    dialog.d_session_key.setText("AKIAEXAMPLEKEY")
    dialog.d_session_secret.setText("s3cr3t/EXAMPLE+key")
    path = str(tmp_path / "arlo.q2vt.json")
    assert dialog.export_settings_file(path)
    data = json.load(open(path, encoding="utf-8"))
    assert data["credentials"] == {"accessKeyId": "AKIAEXAMPLEKEY", "secretAccessKey": "s3cr3t/EXAMPLE+key"}
    assert "credentials" not in data["profile"]  # the profile itself never holds them
    assert "secret included" in dialog.status.text()
    dialog.close()

    other, _ = _dialog(plugin, monkeypatch, tmp_path / "other")
    assert other.import_settings_file(path, same_map=True)
    keys = other._credentials()  # pylint: disable=protected-access
    assert (keys.access_key_id, keys.secret_access_key) == ("AKIAEXAMPLEKEY", "s3cr3t/EXAMPLE+key")
    assert other.collect().destination.kind == "r2"
    other.close()


def test_web_basemaps_saved_in_qgis_and_in_the_settings_file(plugin, monkeypatch, tmp_path):
    """XYZ addresses: kept in the profile, written as QGIS XYZ connections
    (Browser -> XYZ Tiles), carried by the settings file and offered back."""
    import json  # pylint: disable=import-outside-toplevel
    from qgis.core import QgsSettings  # pylint: disable=import-outside-toplevel
    from q2vt_plugin.src.gui.xyz_connections import qgis_xyz_connections  # pylint: disable=import-error
    from q2vt_plugin.src.publishing.models import XyzBasemap  # pylint: disable=import-error
    QgsSettings().remove("connections/xyz/items/Teszt műhold")
    dialog, _parcels = _dialog(plugin, monkeypatch, tmp_path)
    dialog.e_title.setText("Arló terv")
    dialog._add_xyz_row(XyzBasemap("Teszt műhold", "https://{s}.tiles.example.hu/{z}/{x}/{y}.jpg",  # pylint: disable=protected-access
                                   "© Példa Kft.", 0, 20))
    dialog.b_initial.setCurrentIndex(dialog.b_initial.findData("xyz-1"))
    basemap = dialog.collect().basemap
    assert [(x.title, x.max_zoom) for x in basemap.xyz] == [("Teszt műhold", 20)] and basemap.initial == "xyz-1"
    assert "Teszt műhold" not in {x.title for x in qgis_xyz_connections()}  # not on every read
    dialog.save_settings(quiet=True)
    saved = {x.title: x for x in qgis_xyz_connections()}
    assert saved["Teszt műhold"].url == "https://{s}.tiles.example.hu/{z}/{x}/{y}.jpg"
    assert saved["Teszt műhold"].attribution == "© Példa Kft." and saved["Teszt műhold"].max_zoom == 20
    path = str(tmp_path / "arlo.q2vt.json")
    assert dialog.export_settings_file(path)
    assert json.load(open(path, encoding="utf-8"))["profile"]["basemap"]["xyz"][0]["title"] == "Teszt műhold"
    dialog.close()

    QgsSettings().remove("connections/xyz/items/Teszt műhold")  # another computer
    other, _ = _dialog(plugin, monkeypatch, tmp_path / "other")
    assert other.import_settings_file(path, same_map=True)
    assert [x.url for x in other.collect().basemap.xyz] == ["https://{s}.tiles.example.hu/{z}/{x}/{y}.jpg"]
    assert "Teszt műhold" in {x.title for x in qgis_xyz_connections()}  # saved in QGIS on import
    other.close()
    QgsSettings().remove("connections/xyz/items/Teszt műhold")



def test_web_basemap_table_lists_the_qgis_xyz_connections(plugin, monkeypatch, tmp_path):
    """Every XYZ connection saved in QGIS is listed (not ticked); ticking one
    offers it in the web map; an http:// one cannot be ticked."""
    from qgis.core import QgsSettings  # pylint: disable=import-outside-toplevel
    settings = QgsSettings()
    settings.setValue("connections/xyz/items/QGIS ortó/url", "https://ortho.example.hu/{z}/{x}/{y}.jpg")
    settings.setValue("connections/xyz/items/QGIS ortó/zmax", 20)
    settings.setValue("connections/xyz/items/Régi http/url", "http://old.example.hu/{z}/{x}/{y}.png")
    try:
        dialog, _parcels = _dialog(plugin, monkeypatch, tmp_path)
        rows = {dialog.b_xyz.item(r, 1).text(): r for r in range(dialog.b_xyz.rowCount())}
        assert "QGIS ortó" in rows and "Régi http" in rows
        ortho, old = rows["QGIS ortó"], rows["Régi http"]
        assert dialog.b_xyz.item(ortho, 0).checkState() == Qt.CheckState.Unchecked
        assert not dialog.b_xyz.item(ortho, 1).flags() & Qt.ItemFlag.ItemIsEditable  # QGIS's name stays
        assert not dialog.b_xyz.item(old, 0).flags() & Qt.ItemFlag.ItemIsEnabled      # http: not offered
        assert dialog.collect().basemap.xyz == []
        dialog.b_xyz.item(ortho, 0).setCheckState(CHECKED)
        dialog.b_xyz.item(ortho, 3).setText("© Lechner")
        xyz = dialog.collect().basemap.xyz
        assert [(x.title, x.url, x.max_zoom, x.attribution) for x in xyz] == [
            ("QGIS ortó", "https://ortho.example.hu/{z}/{x}/{y}.jpg", 20, "© Lechner")]
        assert dialog.b_initial.findData("xyz-1") >= 0
        dialog.save_settings(quiet=True)  # the QGIS connection is updated when the settings are saved
        assert settings.value("connections/xyz/items/QGIS ortó/q2vt-attribution") == "© Lechner"
        dialog.close()
    finally:
        settings.remove("connections/xyz/items/QGIS ortó")
        settings.remove("connections/xyz/items/Régi http")


# The extent drawn on the map canvas (ExtentTool), driven by synthetic
# QgsMapMouseEvents on an offscreen canvas, and by mouse and key events sent
# through the canvas itself (as Qt delivers them).

def _drawing(previous=None):
    """An offscreen canvas with the pan tool (or ``previous(canvas)``), the
    extent tool on it, what it reported (``done``), what it told the user
    (``hints``) and the number of canvas items before drawing."""
    from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle  # pylint: disable=import-outside-toplevel
    from qgis.gui import QgsMapCanvas, QgsMapToolPan  # pylint: disable=import-outside-toplevel
    from q2vt_plugin.src.gui.extent_tool import ExtentTool  # pylint: disable=import-error,import-outside-toplevel
    canvas = QgsMapCanvas()
    canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    canvas.resize(400, 300)
    canvas.show()
    canvas.setExtent(QgsRectangle(2100000, 6000000, 2140000, 6030000))
    pan = (previous or QgsMapToolPan)(canvas)
    canvas.setMapTool(pan)
    done, hints = [], []
    items = len(canvas.scene().items())
    tool = ExtentTool(canvas, done.append, hints.append)
    canvas.setMapTool(tool)
    return canvas, pan, tool, done, hints, items


def _recorder(canvas):
    """A map tool that only notes the mouse events it gets."""
    from qgis.gui import QgsMapTool  # pylint: disable=import-outside-toplevel

    class Recorder(QgsMapTool):
        def __init__(self, on):
            super().__init__(on)
            self.events = []

        def canvasPressEvent(self, e): self.events.append("press")  # noqa: E704,N802
        def canvasDoubleClickEvent(self, e): self.events.append("dblclick")  # noqa: E704,N802
        def canvasReleaseEvent(self, e): self.events.append("release")  # noqa: E704,N802

        def canvasMoveEvent(self, e):  # noqa: N802
            if e.buttons():
                self.events.append("drag")

    return Recorder(canvas)


def _send(canvas, kind, x, y, button=Qt.MouseButton.LeftButton, held=None):
    """A mouse event sent to the canvas as Qt does: the canvas passes it to
    the map tool of the moment. ``held``: the buttons down after it."""
    from qgis.PyQt.QtCore import QEvent, QPointF  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtGui import QMouseEvent  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtWidgets import QApplication  # pylint: disable=import-outside-toplevel
    event_type = {"press": QEvent.Type.MouseButtonPress, "dblclick": QEvent.Type.MouseButtonDblClick,
                  "move": QEvent.Type.MouseMove, "release": QEvent.Type.MouseButtonRelease}[kind]
    if held is None:
        held = Qt.MouseButton.NoButton if kind in ("release", "move") else button
    if kind == "move":
        button = Qt.MouseButton.NoButton
    QApplication.sendEvent(canvas.viewport(), QMouseEvent(event_type, QPointF(x, y), button, held,
                                                          Qt.KeyboardModifier.NoModifier))


def _press_key(canvas, key=Qt.Key.Key_Escape):
    from qgis.PyQt.QtCore import QEvent  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtGui import QKeyEvent  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtWidgets import QApplication  # pylint: disable=import-outside-toplevel
    QApplication.sendEvent(canvas, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


def _sent_click(canvas, x, y, release_at=None):
    _send(canvas, "press", x, y)
    _send(canvas, "release", *(release_at or (x, y)))


def _mouse(tool, kind, x, y, button=Qt.MouseButton.LeftButton, held=None):
    """A QgsMapMouseEvent handed to the tool. ``held``: the buttons down
    after it (none after a move or a release unless given)."""
    from qgis.gui import QgsMapMouseEvent  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtCore import QEvent, QPoint  # pylint: disable=import-outside-toplevel
    kinds = {"press": (QEvent.Type.MouseButtonPress, tool.canvasPressEvent, button, button),
             "move": (QEvent.Type.MouseMove, tool.canvasMoveEvent, Qt.MouseButton.NoButton,
                      Qt.MouseButton.NoButton),
             "release": (QEvent.Type.MouseButtonRelease, tool.canvasReleaseEvent, button,
                         Qt.MouseButton.NoButton)}
    event_type, handler, which, default = kinds[kind]
    held = default if held is None else held
    handler(QgsMapMouseEvent(tool.canvas(), event_type, QPoint(x, y), which, held,
                             Qt.KeyboardModifier.NoModifier))


def _click(tool, x, y, button=Qt.MouseButton.LeftButton):
    _mouse(tool, "press", x, y, button)
    _mouse(tool, "release", x, y, button)


def _map_rect(canvas, x0, y0, x1, y1):
    """The rectangle between two pixels, in map (canvas CRS) coordinates."""
    from qgis.core import QgsRectangle  # pylint: disable=import-outside-toplevel
    to_map = canvas.getCoordinateTransform()
    return QgsRectangle(to_map.toMapCoordinates(x0, y0), to_map.toMapCoordinates(x1, y1))


def _bands(canvas):
    from qgis.gui import QgsRubberBand  # pylint: disable=import-outside-toplevel
    return [item for item in canvas.scene().items() if isinstance(item, QgsRubberBand)]


def test_extent_drawn_by_clicking_two_corners(plugin):
    canvas, pan, tool, done, hints, items = _drawing()
    assert "first corner" in hints[-1]
    _click(tool, 50, 40)
    assert canvas.mapTool() is tool and done == []  # a click: one corner, not a rectangle
    assert "opposite corner" in hints[-1]
    _mouse(tool, "move", 250, 200)  # the rectangle follows the mouse
    bands = _bands(canvas)
    assert len(bands) == 1 and bands[0].asGeometry().boundingBox() == _map_rect(canvas, 50, 40, 250, 200)
    _click(tool, 250, 200)
    assert len(done) == 1 and done[0] == _map_rect(canvas, 50, 40, 250, 200)
    assert done[0].width() > 0 and done[0].height() > 0
    assert canvas.mapTool() is pan and hints[-1] == ""
    assert _bands(canvas) == [] and len(canvas.scene().items()) == items  # nothing left on the canvas
    from qgis.PyQt import sip  # pylint: disable=import-outside-toplevel
    assert sip.isdeleted(bands[0])  # not just taken off the canvas: deleted


def test_extent_drawn_by_dragging(plugin):
    canvas, pan, tool, done, _hints, items = _drawing()
    _mouse(tool, "press", 300, 220)
    _mouse(tool, "move", 200, 150, held=Qt.MouseButton.LeftButton)
    _mouse(tool, "move", 60, 30, held=Qt.MouseButton.LeftButton)
    _mouse(tool, "release", 60, 30)
    assert done == [_map_rect(canvas, 60, 30, 300, 220)]
    assert canvas.mapTool() is pan and len(canvas.scene().items()) == items


def test_extent_second_click_on_the_first_corner_is_ignored(plugin):
    canvas, _pan, tool, done, hints, _items = _drawing()
    _click(tool, 100, 100)
    _click(tool, 101, 101)   # (almost) the same pixel
    _click(tool, 300, 100)   # a line, no area
    assert done == [] and canvas.mapTool() is tool and "opposite corner" in hints[-1]
    assert len(_bands(canvas)) == 1
    _click(tool, 300, 250)
    assert done == [_map_rect(canvas, 100, 100, 300, 250)]  # from the first corner


def test_extent_drawing_cancelled_by_right_click_esc_or_another_tool(plugin):
    from qgis.gui import QgsMapToolZoom  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtCore import QEvent  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtGui import QKeyEvent  # pylint: disable=import-outside-toplevel
    # Right click after the first corner.
    canvas, pan, tool, done, hints, items = _drawing()
    _click(tool, 50, 40)
    _mouse(tool, "move", 200, 150)
    _click(tool, 200, 150, Qt.MouseButton.RightButton)
    assert done == [None] and canvas.mapTool() is pan and hints[-1] == ""
    assert _bands(canvas) == [] and len(canvas.scene().items()) == items
    # Right click before any corner.
    canvas, pan, tool, done, _hints, _items = _drawing()
    _click(tool, 200, 150, Qt.MouseButton.RightButton)
    assert done == [None] and canvas.mapTool() is pan
    # Esc.
    canvas, pan, tool, done, _hints, items = _drawing()
    _click(tool, 50, 40)
    _mouse(tool, "move", 200, 150)
    tool.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    assert done == [None] and canvas.mapTool() is pan
    assert _bands(canvas) == [] and len(canvas.scene().items()) == items
    # Another map tool chosen while drawing: that tool stays.
    canvas, _pan, tool, done, hints, items = _drawing()
    _click(tool, 50, 40)
    _mouse(tool, "move", 200, 150)
    zoom = QgsMapToolZoom(canvas, False)
    canvas.setMapTool(zoom)
    assert done == [None] and canvas.mapTool() is zoom and hints[-1] == ""
    assert _bands(canvas) == [] and len(canvas.scene().items()) == items


def test_extent_corners_snap_when_snapping_is_on(plugin):
    from qgis.core import (Qgis, QgsFeature, QgsGeometry, QgsPointXY,  # pylint: disable=import-outside-toplevel
                           QgsSnappingConfig, QgsSnappingUtils, QgsTolerance, QgsVectorLayer)
    canvas, _pan, tool, done, _hints, _items = _drawing()
    layer = QgsVectorLayer("Point?crs=EPSG:3857", "corners", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(2110000, 6020000)))
    layer.dataProvider().addFeatures([feature])
    canvas.setLayers([layer])
    utils = QgsSnappingUtils(canvas)
    utils.setIndexingStrategy(QgsSnappingUtils.IndexingStrategy.IndexAlwaysFull)
    utils.setMapSettings(canvas.mapSettings())
    config = QgsSnappingConfig()
    config.setEnabled(True)
    config.setMode(Qgis.SnappingMode.AllLayers)
    config.setTypeFlag(Qgis.SnappingType.Vertex)
    config.setTolerance(10)
    config.setUnits(QgsTolerance.UnitType.Pixels)
    utils.setConfig(config)
    canvas.setSnappingUtils(utils)
    vertex = canvas.getCoordinateTransform().transform(QgsPointXY(2110000, 6020000))
    x, y = round(vertex.x()) + 4, round(vertex.y()) - 3  # near the point, not on it
    _mouse(tool, "move", x, y)
    _click(tool, x, y)
    _click(tool, x + 150, y + 100)
    corner = canvas.getCoordinateTransform().toMapCoordinates(x + 150, y + 100)
    assert len(done) == 1
    assert (done[0].xMinimum(), done[0].yMaximum()) == (2110000, 6020000)  # snapped
    assert (done[0].xMaximum(), done[0].yMinimum()) == (corner.x(), corner.y())  # nothing to snap to


def test_extent_double_click_on_the_last_corner_does_not_reach_the_next_tool(plugin):
    """The drawing ends at the release of the last click; the rest of a
    double click there (the double click and its release) goes nowhere: the
    Pan tool would zoom in and re-centre the map."""
    from qgis.PyQt.QtTest import QTest  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtWidgets import QApplication  # pylint: disable=import-outside-toplevel
    canvas, pan, _tool, done, _hints, _items = _drawing()
    before = canvas.extent()
    expected = _map_rect(canvas, 50, 40, 250, 200)
    _sent_click(canvas, 50, 40)
    _sent_click(canvas, 250, 200)
    _send(canvas, "dblclick", 250, 200)
    _send(canvas, "release", 250, 200)
    assert done == [expected] and canvas.mapTool() is pan
    assert canvas.extent() == before
    # Any tool: it gets nothing of the gesture, then the next click as usual.
    canvas, recorder, _tool, done, _hints, _items = _drawing(_recorder)
    _sent_click(canvas, 50, 40)
    _sent_click(canvas, 250, 200)
    _send(canvas, "dblclick", 250, 200)
    _send(canvas, "move", 251, 200, held=Qt.MouseButton.LeftButton)
    _send(canvas, "release", 251, 200)
    assert len(done) == 1 and canvas.mapTool() is recorder and recorder.events == []
    _sent_click(canvas, 120, 90)
    assert recorder.events == ["press", "release"]
    # A double click long after the drawing is the next tool's own.
    canvas, recorder, _tool, done, _hints, _items = _drawing(_recorder)
    _sent_click(canvas, 50, 40)
    _sent_click(canvas, 250, 200)
    QTest.qWait(QApplication.doubleClickInterval() + 100)
    _send(canvas, "dblclick", 250, 200)
    _send(canvas, "release", 250, 200)
    assert recorder.events == ["dblclick", "release"]


def test_extent_cancelled_while_dragging_ends_at_the_release(plugin):
    """Right click or Esc with the left button still down cancels; the tool
    goes once the button is up, so its release does not reach the next tool
    (the Pan tool would re-centre the map on it)."""
    for previous in (None, _recorder):
        # Right click.
        canvas, back, tool, done, hints, items = _drawing(previous)
        before = canvas.extent()
        _send(canvas, "press", 100, 100)
        _send(canvas, "move", 200, 200, held=Qt.MouseButton.LeftButton)
        _send(canvas, "press", 200, 200, Qt.MouseButton.RightButton,
              held=Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        _send(canvas, "release", 200, 200, Qt.MouseButton.RightButton, held=Qt.MouseButton.LeftButton)
        assert done == [] and canvas.mapTool() is tool and hints[-1] == ""
        assert _bands(canvas) == []  # cancelled: nothing more to see
        _send(canvas, "move", 220, 210, held=Qt.MouseButton.LeftButton)
        _send(canvas, "release", 220, 210)
        assert done == [None] and canvas.mapTool() is back and canvas.extent() == before
        assert len(canvas.scene().items()) == items
        if previous is not None:
            assert back.events == []
        # Esc: the canvas gives keys to its keyPressed signal only while a
        # button is down.
        canvas, back, tool, done, hints, items = _drawing(previous)
        before = canvas.extent()
        _send(canvas, "press", 100, 100)
        _send(canvas, "move", 200, 200, held=Qt.MouseButton.LeftButton)
        _press_key(canvas)
        assert done == [] and canvas.mapTool() is tool and hints[-1] == ""
        _send(canvas, "release", 200, 200)
        assert done == [None] and canvas.mapTool() is back and canvas.extent() == before
        if previous is not None:
            assert back.events == []
    # Esc with no button down: at once.
    canvas, pan, _tool, done, _hints, _items = _drawing()
    _sent_click(canvas, 100, 100)
    _press_key(canvas)
    assert done == [None] and canvas.mapTool() is pan


def test_extent_slips_of_the_mouse_are_not_an_extent(plugin):
    """A click whose release lands a few pixels away (touchpads, pen
    tablets; under 20 pixels across plus down, as for QGIS's zoom tool) is
    one corner, not a tiny rectangle; nor is a second click that makes a
    sliver one or two pixels high."""
    for slip in ((102, 102), (105, 105), (106, 104), (109, 110)):
        canvas, _pan, tool, done, hints, _items = _drawing()
        _sent_click(canvas, 100, 100, release_at=slip)
        assert done == [] and canvas.mapTool() is tool and "opposite corner" in hints[-1], slip
    canvas, _pan, tool, done, hints, _items = _drawing()
    _sent_click(canvas, 100, 100, release_at=(104, 101))
    assert done == [] and canvas.mapTool() is tool and "opposite corner" in hints[-1]
    _sent_click(canvas, 300, 101)                           # 200 x 1 pixels
    _sent_click(canvas, 102, 250)                           # 2 x 150 pixels
    assert done == [] and canvas.mapTool() is tool and len(_bands(canvas)) == 1
    _sent_click(canvas, 300, 250)
    assert done == [_map_rect(canvas, 100, 100, 300, 250)]
    # A real drag still draws at once, also a short one.
    for corner in ((180, 160), (112, 108)):
        canvas, _pan, _tool, done, _hints, _items = _drawing()
        _send(canvas, "press", 100, 100)
        _send(canvas, "move", *corner, held=Qt.MouseButton.LeftButton)
        _send(canvas, "release", *corner)
        assert done == [_map_rect(canvas, 100, 100, *corner)]


def test_extent_on_a_rotated_map_needs_an_area(plugin):
    """On a rotated map the rectangle's sides do not run along the screen's:
    a second corner on the same map y (or x) as the first is no area, and
    the drawing goes on."""
    from qgis.core import QgsPointXY  # pylint: disable=import-outside-toplevel
    canvas, _pan, tool, done, _hints, _items = _drawing()
    canvas.setRotation(45)
    center, step = canvas.center(), 60 * canvas.mapUnitsPerPixel()

    def pixel(right, up):
        point = canvas.getCoordinateTransform().transform(
            QgsPointXY(center.x() + right * step, center.y() + up * step))
        return round(point.x()), round(point.y())

    _sent_click(canvas, *pixel(-1, -1))
    _sent_click(canvas, *pixel(1, -1))   # same map y: 120 pixels wide, no height
    _sent_click(canvas, *pixel(-1, 1))   # same map x
    assert done == [] and canvas.mapTool() is tool and len(_bands(canvas)) == 1
    _sent_click(canvas, *pixel(1, 1))
    assert done == [_map_rect(canvas, *pixel(-1, -1), *pixel(1, 1))]
    assert done[0].width() > 100 * canvas.mapUnitsPerPixel() < done[0].height()


def test_extent_snapped_corner_and_a_click_next_to_it(plugin):
    """With snapping on, the first corner snaps to a point up to 10 pixels
    away; a click a pixel away from where the user clicked (but outside the
    snapping distance) is still a click on the first corner."""
    from qgis.core import (Qgis, QgsFeature, QgsGeometry, QgsSnappingConfig,  # pylint: disable=import-outside-toplevel
                           QgsSnappingUtils, QgsTolerance, QgsVectorLayer)
    canvas, _pan, tool, done, _hints, _items = _drawing()
    vertex = canvas.getCoordinateTransform().toMapCoordinates(200, 150)  # on a pixel
    layer = QgsVectorLayer("Point?crs=EPSG:3857", "corners", "memory")
    feature = QgsFeature()
    feature.setGeometry(QgsGeometry.fromPointXY(vertex))
    layer.dataProvider().addFeatures([feature])
    canvas.setLayers([layer])
    utils = QgsSnappingUtils(canvas)
    utils.setIndexingStrategy(QgsSnappingUtils.IndexingStrategy.IndexAlwaysFull)
    utils.setMapSettings(canvas.mapSettings())
    config = QgsSnappingConfig()
    config.setEnabled(True)
    config.setMode(Qgis.SnappingMode.AllLayers)
    config.setTypeFlag(Qgis.SnappingType.Vertex)
    config.setTolerance(10)
    config.setUnits(QgsTolerance.UnitType.Pixels)
    utils.setConfig(config)
    canvas.setSnappingUtils(utils)
    _sent_click(canvas, 207, 157, release_at=(208, 157))   # 9.9 px away: snaps; 10.6 px: does not
    _sent_click(canvas, 208, 157)
    _sent_click(canvas, 209, 158)
    assert done == [] and canvas.mapTool() is tool
    _sent_click(canvas, 350, 260)
    assert len(done) == 1
    assert (done[0].xMinimum(), done[0].yMaximum()) == (vertex.x(), vertex.y())  # snapped


def test_extent_esc_while_a_button_is_still_down(plugin):
    """Esc with a mouse button still down never brings the previous tool
    back before that button's release (it would get the release: the Pan
    tool re-centres the map), and it does not take back a rectangle drawn
    already."""
    left, right = Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton
    for previous in (None, _recorder):
        # Dragging, a right click cancels (the left button still down),
        # then Esc: the right release ended the canvas's "button down", so
        # the key goes to keyPressEvent.
        canvas, back, tool, done, _hints, _items = _drawing(previous)
        before = canvas.extent()
        _send(canvas, "press", 100, 100)
        _send(canvas, "move", 200, 200, held=left)
        _send(canvas, "press", 200, 200, right, held=left | right)
        _send(canvas, "release", 200, 200, right, held=left)
        _press_key(canvas)
        assert done == [] and canvas.mapTool() is tool
        _send(canvas, "move", 220, 210, held=left)
        _send(canvas, "release", 220, 210)
        assert done == [None] and canvas.mapTool() is back and canvas.extent() == before
        if previous is not None:
            assert back.events == []
    # The opposite corner clicked with the right button held, then Esc:
    # the rectangle stays, the tool goes at the right release.
    canvas, pan, tool, done, _hints, _items = _drawing()
    _sent_click(canvas, 100, 100)
    _send(canvas, "press", 250, 200, right, held=right)
    _send(canvas, "press", 250, 200, held=left | right)
    _send(canvas, "release", 250, 200, held=right)
    _press_key(canvas)
    assert done == [] and canvas.mapTool() is tool
    _send(canvas, "release", 250, 200, right)
    assert done == [_map_rect(canvas, 100, 100, 250, 200)] and canvas.mapTool() is pan


def test_extent_tool_is_deleted_when_done(plugin):
    """Drawn, cancelled or replaced by another map tool, the extent tool
    deletes itself: the canvas (its parent) would keep one per drawing, with
    its snapping mark, and a right click in a transient map tool chosen
    while drawing could bring it back."""
    from qgis.gui import QgsMapToolPan  # pylint: disable=import-outside-toplevel
    from qgis.PyQt import sip  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtCore import QCoreApplication, QEvent  # pylint: disable=import-outside-toplevel
    from q2vt_plugin.src.gui.extent_tool import ExtentTool  # pylint: disable=import-error,import-outside-toplevel

    def gone(canvas, tool):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        return sip.isdeleted(tool) and not [c for c in canvas.children() if isinstance(c, ExtentTool)]

    canvas, _pan, tool, done, _hints, _items = _drawing()
    _sent_click(canvas, 50, 40)
    _sent_click(canvas, 250, 200)
    assert len(done) == 1 and gone(canvas, tool)
    canvas, _pan, tool, done, _hints, _items = _drawing()
    _press_key(canvas)
    assert done == [None] and gone(canvas, tool)
    canvas, _recording, tool, done, _hints, _items = _drawing(_recorder)
    pan = QgsMapToolPan(canvas)
    canvas.setMapTool(pan)
    assert done == [None] and gone(canvas, tool)
    # (Sent without its press: the Pan tool opens the canvas menu on that.)
    _send(canvas, "release", 100, 100, Qt.MouseButton.RightButton)
    assert canvas.mapTool() is pan


def test_extent_tool_cancel(plugin):
    canvas, pan, tool, done, hints, items = _drawing()
    _sent_click(canvas, 100, 100)
    tool.cancel()
    tool.cancel()  # once is enough
    assert done == [None] and canvas.mapTool() is pan and hints[-1] == ""
    assert len(canvas.scene().items()) == items
