"""
extent_tool.py

The published area drawn on the QGIS map canvas like QGIS's "Rectangle from
2 points": click one corner, a rectangle follows the mouse, click the
opposite corner (pressing, dragging and releasing works too). Corners snap
when QGIS's snapping is on. Right click, Esc or choosing another map tool
cancels. The tool that was active before comes back either way.
"""

from typing import Callable, Optional

from qgis.core import Qgis, QgsApplication, QgsGeometry, QgsPointLocator, QgsRectangle
from qgis.gui import QgsMapTool, QgsRubberBand, QgsSnapIndicator
from qgis.PyQt.QtCore import QCoreApplication, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor

# A release this close to the first corner (screen pixels) is the same point:
# the first click itself, or a second click on it. Neither ends the drawing.
SAME_POINT_PIXELS = 3


def tr(text: str) -> str:
    return QCoreApplication.translate("ExtentTool", text)


class ExtentTool(QgsMapTool):
    """Calls ``done`` once: with the drawn rectangle (map canvas CRS), or
    with None when cancelled. ``hint`` gets what to do next while drawing,
    and "" once the tool is done."""

    # A rectangle from two corners (as QgsMapToolExtent's): one without area
    # is ignored and the drawing goes on.
    extentChanged = pyqtSignal(QgsRectangle)

    def __init__(self, canvas, done: Callable[[Optional[QgsRectangle]], None],
                 hint: Optional[Callable[[str], None]] = None):
        super().__init__(canvas)
        self._done = done
        self._hint = hint
        self._previous = canvas.mapTool()
        self._start = None  # the first corner, map canvas CRS
        self._band = None
        self._snap = QgsSnapIndicator(canvas)
        self.setCursor(QgsApplication.getThemeCursor(QgsApplication.Cursor.CapturePoint))
        self.extentChanged.connect(self._drawn)

    def activate(self):
        super().activate()
        self._tell(tr("Click the first corner of the published area. Right click or Esc cancels."))

    def canvasPressEvent(self, event):  # noqa: N802 - Qt override
        if event.button() != Qt.MouseButton.LeftButton or self._start is not None:
            return
        self._start = event.snapPoint()
        # QGIS's own selection rectangle colours.
        self._band = QgsRubberBand(self.canvas(), Qgis.GeometryType.Polygon)
        self._band.setFillColor(QColor(254, 178, 76, 63))
        self._band.setStrokeColor(QColor(254, 58, 29, 100))
        self._band.setWidth(1)
        self._tell(tr("Click the opposite corner. Right click or Esc cancels."))

    def canvasMoveEvent(self, event):  # noqa: N802 - Qt override
        point = event.snapPoint()
        self._snap.setMatch(event.mapPointMatch())
        if self._band is not None:
            self._band.setToGeometry(QgsGeometry.fromRect(QgsRectangle(self._start, point)))

    def canvasReleaseEvent(self, event):  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.RightButton:
            self._finish(None)
            return
        if event.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        point = event.snapPoint()
        # Compared on screen now (the map may have been panned or zoomed
        # since the first click).
        moved = self.toCanvasCoordinates(point) - self.toCanvasCoordinates(self._start)
        if moved.manhattanLength() <= SAME_POINT_PIXELS:
            return  # wait for the opposite corner
        self.extentChanged.emit(QgsRectangle(self._start, point))

    def _drawn(self, rect: QgsRectangle) -> None:
        if rect is None or rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            return  # no area: keep drawing
        self._finish(QgsRectangle(rect))

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self._finish(None)
            return
        super().keyPressEvent(event)

    def deactivate(self):
        self._clear()
        super().deactivate()
        if self._done is not None:  # another map tool was chosen
            self._tell("")
            done, self._done = self._done, None
            done(None)

    def _tell(self, text: str) -> None:
        if self._hint is not None:
            self._hint(text)

    def _clear(self) -> None:
        """No rectangle or snapping mark left on the canvas."""
        self._start = None
        if self._band is not None:
            self.canvas().scene().removeItem(self._band)
            self._band = None
        self._snap.setMatch(QgsPointLocator.Match())

    def _finish(self, rect: Optional[QgsRectangle]) -> None:
        done, self._done = self._done, None
        self._clear()
        self._tell("")
        canvas = self.canvas()
        try:
            if self._previous is not None and self._previous is not self:
                canvas.setMapTool(self._previous)
            else:
                canvas.unsetMapTool(self)
        except RuntimeError:  # the previous tool is gone
            canvas.unsetMapTool(self)
        if done is not None:
            done(rect)
