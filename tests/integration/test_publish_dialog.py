"""The Publish Web Map window (PUB-13): defaults from the project, every
setting saved in the project file and read back, local export without
changing the project, loopback preview, local and (fake) R2 publication
with the review gate, credentials only by reference."""

import json
import os
import time
import urllib.request

import pytest
from qgis.core import QgsApplication, QgsProject
from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtWidgets import QMessageBox

from q2vt_fixtures import reset_project
from publishing.models import ReleaseState

CHECKED = Qt.CheckState.Checked


@pytest.fixture
def messages(monkeypatch):
    shown = []
    for name in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, _n=name, **k: shown.append((_n, a[2] if len(a) > 2 else "")))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    return shown


@pytest.fixture
def project(plugin, tmp_path):
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_publishing_pipeline import _parcels  # pylint: disable=import-error
    parcels = _parcels(str(tmp_path / "parcels.gpkg"))
    project = reset_project()
    group = project.layerTreeRoot().addGroup("Szabályozás")
    project.addMapLayer(parcels, False)
    group.addLayer(parcels)
    project.layerTreeRoot().findLayer(parcels.id()).setItemVisibilityChecked(False)
    project.setFileName(str(tmp_path / "terv.qgz"))
    return project, parcels


def _dialog():
    from q2vt_plugin.src.gui.publish_dialog import PublishDialog  # pylint: disable=import-error
    return PublishDialog(iface=None)


def _configure(dialog, parcels, tmp_path):
    dialog.e_title.setText("Arló szabályozási terv")
    dialog.e_slug.setText("arlo-terv")
    dialog.e_min_zoom.setValue(11)
    dialog.e_max_zoom.setValue(13)
    dialog.profile.view.extent = [2119000, 6019000, 2123000, 6023000]
    item = next(dialog._tree_items())  # pylint: disable=protected-access
    item.setCheckState(1, CHECKED)
    item.setCheckState(2, CHECKED)
    dialog.o_dir.setText(str(tmp_path / "web maps"))
    dialog._fill_interaction_layers()  # pylint: disable=protected-access
    table = dialog.i_fields
    names = [table.item(row, 0).text() for row in range(table.rowCount())]
    row = names.index("hrsz")
    for column in (1, 4, 5):  # popup, search, key
        table.item(row, column).setCheckState(CHECKED)
    table.setItem(row, 2, type(table.item(row, 0))("Helyrajzi szám"))
    table.cellWidget(names.index("zone"), 6).setCurrentIndex(1)  # values filter
    assert not dialog.i_google_key.isEnabled()  # the key field follows the Street View switch
    dialog.i_flags["street_view"].setChecked(True)
    dialog.i_google_key.setText(" AIzaTESTKEY ")


def _wait_task(dialog, timeout=120):
    end = time.time() + timeout
    while dialog.task is not None and time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.05)
    assert dialog.task is None, "publishing task did not finish"


def test_defaults_follow_the_project(project, messages):
    project, parcels = project
    dialog = _dialog()
    config = dialog.profile.layer(parcels.id())
    assert config is not None and config.included is False  # hidden layers are not selected by default
    assert dialog.profile.output.archive == "pmtiles" and dialog.profile.destination.kind == "local"
    dialog.close()


def test_a_closed_window_is_freed_with_its_layer_lists(project, messages):
    """The layer lists follow the project's layers. A closed window that was
    never freed kept them reacting to every project change (and crashed
    QGIS's Python once that window was half collected)."""
    import gc
    import weakref
    from qgis.PyQt import sip
    dialog = _dialog()
    lists = [dialog.p_layer, dialog.p_zoning, dialog.p_regulation, dialog.t_layer]
    alive = weakref.ref(dialog)
    dialog.close()
    del dialog
    gc.collect()
    assert alive() is None
    assert all(sip.isdeleted(layer_list) for layer_list in lists)
    project[0].clear()


def test_settings_are_saved_in_the_project_file(project, messages, tmp_path):
    project, parcels = project
    dialog = _dialog()
    _configure(dialog, parcels, tmp_path)
    assert dialog.save_settings()
    assert project.isDirty()
    assert project.write()
    path = project.fileName()
    layer_id = parcels.id()
    project.clear()
    assert QgsProject.instance().read(path)
    from q2vt_plugin.src.gui import publication_profiles as store  # pylint: disable=import-error
    profile, saved_from = store.active_profile(QgsProject.instance())
    assert profile.title == "Arló szabályozási terv" and saved_from == path
    config = profile.layer(layer_id)
    assert config.included and config.initially_visible and config.key_fields == ["hrsz"]
    assert [p.alias for p in config.popup_fields] == ["Helyrajzi szám"]
    assert [f.field for f in config.filter_fields] == ["zone"]
    assert profile.interaction.street_view and profile.interaction.google_api_key == "AIzaTESTKEY"
    raw, _ = QgsProject.instance().readEntry(store.SCOPE, store.KEY_PROFILES, "")
    assert "secret" not in raw.lower() and "password" not in raw.lower()
    # A second window shows the saved settings.
    again = _dialog()
    assert again.e_title.text() == "Arló szabályozási terv" and again.e_slug.text() == "arlo-terv"
    assert again.i_google_key.text() == "AIzaTESTKEY" and again.i_google_key.isEnabled()
    dialog.close()
    again.close()


def test_export_preview_and_local_publish(project, messages, tmp_path):
    project, parcels = project
    tree = [(n.layer().id(), n.isVisible()) for n in project.layerTreeRoot().findLayers()]
    dialog = _dialog()
    _configure(dialog, parcels, tmp_path)
    assert dialog.export_locally(), messages
    assert [(n.layer().id(), n.isVisible()) for n in project.layerTreeRoot().findLayers()] == tree
    assert os.path.exists(dialog.local_result.entry_path)
    dialog.preview()
    url = dialog.preview_server.url("arlo-terv/index.html")
    with urllib.request.urlopen(url) as response:
        assert response.status == 200 and b"bootstrap" in response.read()
    dialog.preview_server.stop()
    dialog.publish()  # local destination: export + local link
    assert dialog.public_url.startswith("file:")
    dialog.close()


def test_r2_publish_needs_approval_and_runs_in_a_task(project, messages, tmp_path, monkeypatch):
    project, parcels = project
    from publishing_fake_s3 import FakeS3Client
    from publishing.preview_server import PreviewServer
    from publishing.providers.base import Credentials
    from publishing.providers.r2 import R2Provider
    dialog = _dialog()
    _configure(dialog, parcels, tmp_path)
    root = tmp_path / "bucket"
    client = FakeS3Client(str(root))
    public = PreviewServer(str(root / "maps")).start()
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("r2"))
    dialog.d_account.setText("abc123")
    dialog.d_bucket.setText("maps")
    dialog.d_public.setText(public.url("").rstrip("/"))
    dialog.d_session_key.setText("AKIATEST")
    dialog.d_session_secret.setText("session-secret-value")
    profile = dialog.collect()
    monkeypatch.setattr(dialog, "_provider", lambda: R2Provider(
        profile.destination, Credentials("AKIATEST", "session-secret-value"), "maps/arlo-terv",
        client=client, sleep=lambda s: None))
    try:
        dialog.approve.setChecked(False)
        dialog.publish()
        assert dialog.task is None and dialog.local_result is None  # stopped at the review
        dialog.refresh_review()
        assert "Popup fields: hrsz" in dialog.review.toPlainText()
        dialog.approve.setChecked(True)
        dialog.publish()
        _wait_task(dialog)
        assert dialog.public_url.endswith("/maps/arlo-terv/index.html"), messages
        assert client.keys() and all(k.startswith("maps/arlo-terv/") for k in client.keys())
        from q2vt_plugin.src.gui import publication_profiles as store  # pylint: disable=import-error
        saved, _ = store.active_profile(project)
        assert saved.approval.fingerprint  # approval stored with the settings
        raw, _ = project.readEntry(store.SCOPE, store.KEY_PROFILES, "")
        assert "session-secret-value" not in raw and "AKIATEST" not in raw
        pointer = json.loads(open(root / "maps" / "maps" / "arlo-terv" / "current.json").read())
        assert pointer["releaseId"] == dialog.local_result.release.release_id
    finally:
        public.stop()
        dialog.close()


def test_copy_embed_code(project, messages, tmp_path):
    from qgis.PyQt.QtGui import QGuiApplication
    project, parcels = project
    dialog = _dialog()
    _configure(dialog, parcels, tmp_path)
    QGuiApplication.clipboard().setText("before")
    assert dialog.copy_embed_code() == ""  # a local copy cannot be shown in another page
    assert QGuiApplication.clipboard().text() == "before" and "Destination" in dialog.status.text()
    dialog.d_kind.setCurrentIndex(dialog.d_kind.findData("r2"))
    dialog.d_public.setText("https://maps.example.hu/")
    code = dialog.copy_embed_code()
    assert code.startswith('<iframe src="https://maps.example.hu/') and "/arlo-terv/index.html?embed\"" in code
    assert 'title="Arló szabályozási terv"' in code and QGuiApplication.clipboard().text() == code
    dialog.public_url = "https://maps.example.hu/terv/index.html#v=1/47.5/19"  # after a publication
    assert 'src="https://maps.example.hu/terv/index.html?embed#v=1/47.5/19"' in dialog.copy_embed_code()
    dialog.close()


def test_info_documents_terrain_addresses_round_trip(project, messages, tmp_path):
    """The 4.24 settings: map info and documents (Info tab), terrain (Basemap
    tab), house numbers, overview and drawing (Interaction tab) and a layer's
    3D height field are kept through the window and listed in the review."""
    from qgis.core import QgsField, QgsRasterLayer, QgsVectorLayer  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtCore import QVariant  # pylint: disable=import-outside-toplevel
    from publishing.models import DocumentConfig  # pylint: disable=import-error
    sys_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "browser")
    import sys  # pylint: disable=import-outside-toplevel
    sys.path.insert(0, sys_path)
    from test_web_viewer_extras import _dem  # pylint: disable=import-error
    project, parcels = project
    dem = QgsRasterLayer(_dem(tmp_path / "dem.tif"), "Domborzat")
    addresses = QgsVectorLayer("Point?crs=EPSG:3857", "Házszámok", "memory")
    addresses.dataProvider().addAttributes([QgsField("hsz", QVariant.String)])
    addresses.updateFields()
    document = tmp_path / "rendelet.pdf"
    document.write_bytes(b"%PDF-1.4\n%%EOF\n")
    dialog = _dialog()
    _configure(dialog, parcels, tmp_path)
    project.addMapLayers([dem, addresses])
    profile = dialog.collect()
    profile.info.issuer, profile.info.legal_date = "Arló", "2025. 10. 01."
    profile.info.documents = [DocumentConfig("HÉSZ", str(document))]
    profile.terrain.layer_id, profile.terrain.exaggeration = dem.id(), 2.0
    profile.interaction.address_layer_id, profile.interaction.address_number_field = addresses.id(), "hsz"
    profile.interaction.overview_map = profile.interaction.three_d = True
    dialog._populate(profile)  # pylint: disable=protected-access
    dialog._fill_interaction_layers()  # the Interaction tab opened  # pylint: disable=protected-access
    dialog.i_layers.setCurrentRow(0)
    dialog.i_height.setCurrentIndex(dialog.i_height.findData("terulet"))  # chosen in the window
    again = dialog.collect()
    assert again.info.issuer == "Arló" and again.info.legal_date == "2025. 10. 01."
    assert [(d.title, d.path) for d in again.info.documents] == [("HÉSZ", str(document))]
    assert again.terrain.layer_id == dem.id() and again.terrain.exaggeration == 2.0 and again.terrain.hillshade
    assert again.interaction.address_layer_id == addresses.id() and again.interaction.address_number_field == "hsz"
    assert again.interaction.address_street_field == "" and again.interaction.overview_map
    assert again.interaction.three_d and not again.interaction.drawing  # drawing: off by default
    assert again.layer(parcels.id()).height_field == "terulet"
    assert dialog.i_height.currentData() == "terulet"
    dialog.refresh_review()
    review = dialog.review.toPlainText()
    assert "HÉSZ (rendelet.pdf)" in review and "Domborzat" in review and "Házszámok" in review
    assert "overview map, 3D view" in review
    assert "terulet" in review
    dialog.close()


def _window_on_a_canvas():
    """A Publish window whose QGIS is an offscreen map canvas with the Pan
    tool, a message bar and a status bar noting what they get; mouse and key
    helpers for the map tool of the moment."""
    from types import SimpleNamespace  # pylint: disable=import-outside-toplevel
    from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle  # pylint: disable=import-outside-toplevel
    from qgis.gui import QgsMapCanvas, QgsMapMouseEvent, QgsMapToolPan  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtCore import QEvent, QPoint  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtGui import QKeyEvent  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtTest import QTest  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtWidgets import QApplication  # pylint: disable=import-outside-toplevel
    from q2vt_plugin.src.gui.publish_dialog import PublishDialog  # pylint: disable=import-error,import-outside-toplevel
    canvas = QgsMapCanvas()
    canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
    canvas.resize(400, 300)
    canvas.setExtent(QgsRectangle(2100000, 6000000, 2140000, 6030000))
    pan = QgsMapToolPan(canvas)
    canvas.setMapTool(pan)
    bar, popped, status = [], [], []
    shown = [""]  # what the status bar shows now

    class Bar:
        def pushItem(self, item): bar.append(item)
        def popWidget(self, item): popped.append(item)

    class StatusBar:
        def showMessage(self, text, timeout=0): status.append(text); shown[0] = text  # noqa: E702
        def clearMessage(self): status.append(""); shown[0] = ""  # noqa: E702
        def currentMessage(self): return shown[0]

    class Iface:
        def mapCanvas(self): return canvas
        def messageBar(self): return Bar()
        def statusBarIface(self): return StatusBar()
        def mainWindow(self): return None

    def mouse(kind, x, y, button=Qt.MouseButton.NoButton, buttons=Qt.MouseButton.NoButton):
        tool = canvas.mapTool()
        handler = {QEvent.Type.MouseButtonPress: tool.canvasPressEvent, QEvent.Type.MouseMove: tool.canvasMoveEvent,
                   QEvent.Type.MouseButtonDblClick: tool.canvasDoubleClickEvent,
                   QEvent.Type.MouseButtonRelease: tool.canvasReleaseEvent}[kind]
        handler(QgsMapMouseEvent(canvas, kind, QPoint(x, y), button, buttons, Qt.KeyboardModifier.NoModifier))

    def click(x, y, button=Qt.MouseButton.LeftButton):
        mouse(QEvent.Type.MouseButtonPress, x, y, button, button)
        mouse(QEvent.Type.MouseButtonRelease, x, y, button, Qt.MouseButton.NoButton)

    def esc():
        canvas.mapTool().keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                                 Qt.KeyboardModifier.NoModifier))

    def wait():
        QTest.qWait(QApplication.doubleClickInterval() + 100)

    dialog = PublishDialog(iface=Iface())

    def back():
        """The window is not back at once, but a double-click interval later."""
        assert not dialog.isVisible()
        wait()
        assert dialog.isVisible()

    def map_extent(x0, y0, x1, y1):
        """The rectangle between two pixels, rounded, as the profile keeps it (EPSG:3857)."""
        to_map = canvas.getCoordinateTransform()
        drawn = QgsRectangle(to_map.toMapCoordinates(x0, y0), to_map.toMapCoordinates(x1, y1))
        return [round(drawn.xMinimum()), round(drawn.yMinimum()), round(drawn.xMaximum()), round(drawn.yMaximum())]

    return SimpleNamespace(dialog=dialog, canvas=canvas, pan=pan, bar=bar, popped=popped, status=status,
                           shown=shown, mouse=mouse, click=click, esc=esc, wait=wait, back=back,
                           map_extent=map_extent)


def test_the_extent_is_drawn_on_the_map(project, messages):
    """The published area drawn on the QGIS map by clicking two opposite
    corners: the window steps aside meanwhile, the map gets the keyboard (Esc)
    and the status bar says which corner comes next; right click, Esc,
    another map tool or opening the window again cancels; the tool used
    before comes back and the message bar notice goes. The window comes back
    a double-click interval later: the rest of a double click on the last
    corner must not click whatever is under the mouse in it."""
    from q2vt_plugin.src.gui.extent_tool import ExtentTool  # pylint: disable=import-error
    w = _window_on_a_canvas()
    dialog, canvas, pan, bar, popped, status = w.dialog, w.canvas, w.pan, w.bar, w.popped, w.status
    dialog.show()
    assert canvas.focusWidget() is None
    dialog._draw_extent()  # pylint: disable=protected-access
    assert isinstance(canvas.mapTool(), ExtentTool) and not dialog.isVisible() and bar
    assert "two opposite corners" in bar[-1].text() and "first corner" in status[-1]
    assert canvas.focusWidget() is canvas  # Esc goes to the map tool
    w.click(100, 50)
    assert isinstance(canvas.mapTool(), ExtentTool) and "opposite corner" in status[-1]  # still drawing
    assert popped == []
    w.click(200, 110)
    assert canvas.mapTool() is pan and status[-1] == ""
    assert popped == [bar[-1]]  # the notice goes with the drawing
    w.back()
    expected = w.map_extent(100, 50, 200, 110)
    assert [round(v) for v in dialog.profile.view.extent] == expected
    assert dialog.profile.view.extent_layer == "" and "Fixed extent" in dialog.extent_label.text()
    dialog._draw_extent()  # pylint: disable=protected-access
    w.click(150, 80)
    w.esc()
    assert canvas.mapTool() is pan and status[-1] == ""
    w.back()
    assert [round(v) for v in dialog.profile.view.extent] == expected
    dialog._draw_extent()  # pylint: disable=protected-access
    w.click(150, 80)
    w.click(300, 200, Qt.MouseButton.RightButton)
    assert canvas.mapTool() is pan
    w.back()
    assert [round(v) for v in dialog.profile.view.extent] == expected
    dialog._draw_extent()  # pylint: disable=protected-access
    canvas.setMapTool(pan)  # another tool chosen
    assert status[-1] == "" and popped[-1] is bar[-1]
    w.back()
    # The window opened again from the Web menu while drawing: drawing given
    # up; drawing again starts from the tool used before.
    dialog._draw_extent()  # pylint: disable=protected-access
    w.click(150, 80)
    dialog.show()
    assert canvas.mapTool() is pan and status[-1] == "" and popped[-1] is bar[-1]
    assert [round(v) for v in dialog.profile.view.extent] == expected
    dialog._draw_extent()  # pylint: disable=protected-access
    w.esc()
    assert canvas.mapTool() is pan
    w.back()
    dialog.close()


def test_the_extent_hint_in_the_status_bar(project, messages):
    """The hint comes back after a QGIS status tip replaced it (hovering a
    toolbar), and only the hint is cleared when the drawing ends."""
    from qgis.PyQt.QtCore import QEvent  # pylint: disable=import-outside-toplevel
    w = _window_on_a_canvas()
    w.dialog.show()
    w.dialog._draw_extent()  # pylint: disable=protected-access
    w.shown[0] = "Zoom In"
    w.mouse(QEvent.Type.MouseMove, 120, 90)
    assert "first corner" in w.shown[0]
    w.shown[0] = "Another plugin's message"
    w.esc()
    assert w.shown[0] == "Another plugin's message"
    w.wait()
    w.dialog.close()


def test_closing_the_window_gives_the_drawing_up(project, messages):
    """Closed while the extent is being drawn (the plugin unloaded or
    reloaded): the drawing is given up, the previous map tool comes back
    and the window is not shown again, also when it is closed while it was
    about to come back."""
    w = _window_on_a_canvas()
    dialog, canvas = w.dialog, w.canvas
    dialog.show()
    before = list(dialog.profile.view.extent)
    dialog._draw_extent()  # pylint: disable=protected-access
    w.click(150, 80)
    dialog.close()
    assert canvas.mapTool() is w.pan and w.status[-1] == "" and w.popped[-1] is w.bar[-1]
    w.wait()
    assert not dialog.isVisible() and canvas.mapTool() is w.pan
    assert dialog.profile.view.extent == before
    w.click(160, 90)  # the Pan tool's
    assert not dialog.isVisible()
    # Closed after the drawing, before the window came back.
    dialog.show()
    dialog._draw_extent()  # pylint: disable=protected-access
    w.click(100, 50)
    w.click(200, 110)
    dialog.close()
    w.wait()
    assert not dialog.isVisible() and canvas.mapTool() is w.pan


def test_double_clicking_draw_gives_no_corner(project, messages):
    """Draw… double-clicked: the second click lands on the map (the window
    stepped aside at the first) as a double click. It is no corner: the
    user's next two clicks give the extent."""
    from qgis.PyQt.QtCore import QEvent  # pylint: disable=import-outside-toplevel
    from q2vt_plugin.src.gui.extent_tool import ExtentTool  # pylint: disable=import-error
    w = _window_on_a_canvas()
    w.dialog.show()
    w.dialog._draw_extent()  # pylint: disable=protected-access
    left = Qt.MouseButton.LeftButton
    w.mouse(QEvent.Type.MouseButtonDblClick, 300, 220, left, left)
    w.mouse(QEvent.Type.MouseButtonRelease, 300, 220, left)
    assert isinstance(w.canvas.mapTool(), ExtentTool) and "first corner" in w.status[-1]
    w.click(60, 40)
    assert isinstance(w.canvas.mapTool(), ExtentTool) and "opposite corner" in w.status[-1]
    w.click(250, 200)
    w.back()
    assert [round(v) for v in w.dialog.profile.view.extent] == w.map_extent(60, 40, 250, 200)
    w.dialog.close()


def test_an_area_a_web_map_cannot_show(project, messages):
    """Drawn beyond the poles on a map in degrees: EPSG:3857 cannot take it.
    The window says so and comes back, the extent stays as it was."""
    from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle  # pylint: disable=import-outside-toplevel
    w = _window_on_a_canvas()
    w.canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:4326"))
    w.canvas.setExtent(QgsRectangle(0, 95, 40, 125))
    w.dialog.show()
    before = list(w.dialog.profile.view.extent)
    w.dialog._draw_extent()  # pylint: disable=protected-access
    w.click(100, 50)
    w.click(200, 110)
    assert w.canvas.mapTool() is w.pan
    w.back()
    assert w.dialog.profile.view.extent == before and "web map" in w.dialog.status.text()
    w.dialog.status.setText("")
    w.dialog._use_canvas_extent()  # Map canvas  # pylint: disable=protected-access
    assert w.dialog.profile.view.extent == before and "web map" in w.dialog.status.text()
    w.dialog.close()
