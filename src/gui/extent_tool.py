"""
extent_tool.py

The published area drawn on the QGIS map canvas: drag a rectangle; Esc or
choosing another map tool cancels. The tool that was active before comes
back either way.
"""

from typing import Callable, Optional

from qgis.core import QgsRectangle
from qgis.gui import QgsMapToolExtent
from qgis.PyQt.QtCore import Qt


class ExtentTool(QgsMapToolExtent):
    """Calls ``done`` once: with the drawn rectangle (map canvas CRS), or
    with None when cancelled."""

    def __init__(self, canvas, done: Callable[[Optional[QgsRectangle]], None]):
        super().__init__(canvas)
        self._done = done
        self._previous = canvas.mapTool()
        self.extentChanged.connect(self._drawn)

    def _drawn(self, rect: QgsRectangle) -> None:
        if rect is None or rect.isNull() or rect.width() <= 0 or rect.height() <= 0:
            return  # a click without dragging: draw again
        self._finish(QgsRectangle(rect))

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self._finish(None)
            return
        super().keyPressEvent(event)

    def deactivate(self):
        super().deactivate()
        if self._done is not None:  # another map tool was chosen
            done, self._done = self._done, None
            done(None)

    def _finish(self, rect: Optional[QgsRectangle]) -> None:
        done, self._done = self._done, None
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
