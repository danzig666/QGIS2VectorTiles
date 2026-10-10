"""
extent_tool.py

The published area drawn on the QGIS map canvas like QGIS's "Rectangle from
2 points": click one corner, a rectangle follows the mouse, click the
opposite corner (pressing, dragging and releasing works too). Corners snap
when QGIS's snapping is on. Right click, Esc or choosing another map tool
cancels. The tool that was active before comes back either way, once the
mouse buttons are up, and the rest of a double click on the last corner does
not reach it. The tool deletes itself when it is done.
"""

from typing import Callable, Optional

from qgis.core import Qgis, QgsApplication, QgsGeometry, QgsPointLocator, QgsRectangle
from qgis.gui import QgsMapTool, QgsRubberBand, QgsSnapIndicator
from qgis.PyQt import sip
from qgis.PyQt.QtCore import QCoreApplication, QEvent, QObject, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import QApplication

# A rectangle narrower or lower than this on screen (pixels) is a slip of the
# mouse along a side, not an area: the drawing goes on.
MIN_SIDE_PIXELS = 4

# The press on the first corner released less than this far from where it
# went down (pixels across plus down) is a click whose release slipped
# (touchpads, pen tablets), not a drag. QGIS's zoom tool takes a drag under
# 20 pixels for a click, too. Always more than QApplication.startDragDistance().
MIN_DRAG_PIXELS = 20

# The buttons whose release reaches the map tool (the canvas keeps the middle
# one for panning).
TOOL_BUTTONS = Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton

MOUSE_EVENTS = (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick,
                QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease)


def tr(text: str) -> str:
    return QCoreApplication.translate("ExtentTool", text)


class _DoubleClickRest(QObject):
    """Eats the rest of a double click on the corner that ended the drawing
    (the double click and its release), so the map tool that came back does
    not get it: the Pan tool would zoom in and re-centre the map, a selection
    tool could select. Gone after the double-click interval or at the next
    press; if the double click's release is lost (a broken mouse grab), at
    the next press of that button or the first mouse event with it up."""

    def __init__(self, viewport):
        super().__init__(viewport)
        self._button = None  # the double click's button, until its release
        viewport.installEventFilter(self)
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._expire)
        timer.start(QApplication.doubleClickInterval())

    def eventFilter(self, watched, event):  # noqa: N802 - Qt override
        kind = event.type()
        if kind not in MOUSE_EVENTS:
            return False
        if self._button is not None:  # inside the double click: all of it
            if kind == QEvent.Type.MouseButtonRelease and event.button() == self._button:
                self._stop()
                return True
            if event.button() == self._button or not event.buttons() & self._button:
                self._stop()  # its release was lost: this is the next tool's
                return False
            return True
        if kind == QEvent.Type.MouseButtonDblClick:
            self._button = event.button()
            return True
        if kind == QEvent.Type.MouseButtonPress:
            self._stop()  # a new click: the next tool's
        return False

    def _expire(self):
        if self._button is None:  # otherwise at its release
            self._stop()

    def _stop(self):
        self.parent().removeEventFilter(self)
        self.deleteLater()


class ExtentTool(QgsMapTool):
    """Calls ``done`` once: with the drawn rectangle (map canvas CRS), or
    with None when cancelled. ``hint`` gets what to do next while drawing
    (again on mouse moves), and "" once the tool is done. The tool deletes
    itself afterwards."""

    # A rectangle from two corners (as QgsMapToolExtent's), emitted when it
    # is accepted.
    extentChanged = pyqtSignal(QgsRectangle)

    def __init__(self, canvas, done: Callable[[Optional[QgsRectangle]], None],
                 hint: Optional[Callable[[str], None]] = None):
        super().__init__(canvas)
        self._done = done
        self._hint = hint
        self._previous = canvas.mapTool()
        self._start = None      # the first corner (snapped), map canvas CRS
        self._clicked = None    # where it was clicked (not snapped), map canvas CRS
        self._pressed = False   # the left button went down on this tool and is still down
        self._opening = False   # ... and that press set the first corner
        self._held = Qt.MouseButton.NoButton  # left and right buttons down, as last seen
        self._ending = False    # drawn or cancelled: the tool goes once the buttons are up
        self._result = None
        self._told = ""         # the hint of the moment
        self._band = None
        self._snap = QgsSnapIndicator(canvas)
        self.setCursor(QgsApplication.getThemeCursor(QgsApplication.Cursor.CapturePoint))

    def activate(self):
        super().activate()
        # While a mouse button is down, or the map is being panned (Space or
        # the middle button held), the canvas gives keys only to this signal,
        # not to keyPressEvent.
        self.canvas().keyPressed.connect(self._key_while_busy)
        self._tell(tr("Click the first corner of the published area. Right click or Esc cancels."))

    def cancel(self) -> None:
        """Stop drawing (``done`` gets None) and bring the previous tool
        back: once the mouse buttons are up, or their release would go to
        that tool."""
        if self._done is None:
            return
        if not self._ending:
            self._end(None)
        if not self._held:
            self._finish()

    def canvasPressEvent(self, event):  # noqa: N802 - Qt override
        self._held = event.buttons() & TOOL_BUTTONS
        if event.button() != Qt.MouseButton.LeftButton or self._ending:
            return
        self._pressed = True
        self._opening = self._start is None
        if not self._opening:
            return
        # (originalMapPoint() is the snapped point too once snapPoint() ran.)
        self._clicked = self.toMapCoordinates(event.originalPixelPoint())
        self._start = event.snapPoint()
        # QGIS's own selection rectangle colours.
        self._band = QgsRubberBand(self.canvas(), Qgis.GeometryType.Polygon)
        self._band.setFillColor(QColor(254, 178, 76, 63))
        self._band.setStrokeColor(QColor(254, 58, 29, 100))
        self._band.setWidth(1)
        self._tell(tr("Click the opposite corner. Right click or Esc cancels."))

    def canvasDoubleClickEvent(self, event):  # noqa: N802 - Qt override
        # Qt sends a quick second press near the first as a double click,
        # also when the first press was not on the map: Draw… double-clicked,
        # the window steps aside at the first click and the second lands on
        # the map. With no first corner yet the first press was not this
        # tool's (it would have set the corner), and this is no corner.
        if event.button() == Qt.MouseButton.LeftButton and self._start is None:
            self._held = event.buttons() & TOOL_BUTTONS
            return
        self.canvasPressEvent(event)

    def canvasMoveEvent(self, event):  # noqa: N802 - Qt override
        self._held = event.buttons() & TOOL_BUTTONS
        if self._ending:
            return
        if self._told:  # again: a QGIS status tip may have replaced it
            self._tell(self._told)
        point = event.snapPoint()
        self._snap.setMatch(event.mapPointMatch())
        if self._band is not None:
            self._band.setToGeometry(QgsGeometry.fromRect(QgsRectangle(self._start, point)))

    def canvasReleaseEvent(self, event):  # noqa: N802 - Qt override
        self._held = event.buttons() & TOOL_BUTTONS
        if event.button() == Qt.MouseButton.RightButton:
            if not self._ending:
                self._end(None)
        elif event.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            opening, self._opening = self._opening, False
            # Not with the right button down: that is a cancel on its way
            # (its release cancels).
            if not self._ending and not self._held & Qt.MouseButton.RightButton:
                self._corner(event, opening)
        # Not while a button is still down: its release would go to the
        # previous tool (the Pan tool would re-centre the map).
        if self._ending and not self._held:
            self._finish(after_click=True)

    def _corner(self, event, opening: bool) -> None:
        """The release of a press (``opening``: of the press on the first
        corner): the opposite corner, or not yet."""
        point = event.snapPoint()
        # Decided where the mouse really was (not where it snapped), on
        # screen now: the map may have been panned or zoomed since.
        moved = (event.originalPixelPoint() - self.toCanvasCoordinates(self._clicked)).manhattanLength()
        slip = QApplication.startDragDistance()
        if opening:  # a drag from the first corner, or a click on it whose release slipped
            slip = max(MIN_DRAG_PIXELS, slip + 1)
        if moved < slip:
            return  # a click on the first corner: wait for the opposite one
        rect = QgsRectangle(self._start, point)
        # Measured in map units, so that it holds on a rotated map too (the
        # sides are not along the screen's there).
        least = MIN_SIDE_PIXELS * self.canvas().mapUnitsPerPixel()
        if rect.isEmpty() or min(rect.width(), rect.height()) < least:
            return  # no area to speak of: keep drawing
        self._end(rect)
        self.extentChanged.emit(QgsRectangle(rect))

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self.cancel()
            return
        super().keyPressEvent(event)

    def _key_while_busy(self, event) -> None:
        # (Also after keyPressEvent when the canvas gives the key to both:
        # cancelled already then.)
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()  # with a button down, the tool goes at its release

    def deactivate(self):
        try:
            self.canvas().keyPressed.disconnect(self._key_while_busy)
        except TypeError:  # not connected
            pass
        self._clear()
        super().deactivate()
        if self._done is not None:  # another map tool was chosen
            self._tell("")
            done, self._done = self._done, None
            try:
                done(None)
            finally:
                self._forget()

    def _tell(self, text: str) -> None:
        self._told = text
        if self._hint is not None:
            self._hint(text)

    def _end(self, rect: Optional[QgsRectangle]) -> None:
        """Drawn (``rect``) or cancelled (None); nothing more to see."""
        self._ending = True
        self._result = rect
        self._clear()
        self._tell("")

    def _clear(self) -> None:
        """No rectangle or snapping mark left on the canvas."""
        self._start = None
        if self._band is not None:
            band, self._band = self._band, None
            band.reset(Qgis.GeometryType.Polygon)
            sip.delete(band)  # owned by the canvas otherwise: one per drawing would be kept
        self._snap.setMatch(QgsPointLocator.Match())

    def _finish(self, after_click: bool = False) -> None:
        done, self._done = self._done, None
        if done is None:
            return
        canvas = self.canvas()
        try:
            if self._previous is not None and self._previous is not self:
                canvas.setMapTool(self._previous)
            else:
                canvas.unsetMapTool(self)
        except RuntimeError:  # the previous tool is gone
            canvas.unsetMapTool(self)
        if after_click:
            _DoubleClickRest(canvas.viewport())
        try:
            done(self._result)
        finally:  # (also when ``done`` fails)
            self._forget()

    def _forget(self) -> None:
        """Done: the tool keeps nothing alive (the window behind ``hint``,
        the previous tool) and goes, or the canvas (its parent) would keep
        one per drawing, and a right click in a transient map tool chosen
        while drawing could bring it back."""
        self._hint = None
        self._previous = None
        self.deleteLater()
