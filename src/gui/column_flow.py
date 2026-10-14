"""A layout of aligned columns whose count follows the width.

Like a grid of checkboxes, but a wide window gets more columns (fewer rows)
and a narrow one fewer, down to one: the window stays freely resizable. The
last item may be a "wide" one (a line edit) that takes the rest of the last
row, or a row of its own when too little is left."""

from qgis.PyQt.QtCore import QPoint, QRect, QSize, Qt, QTimer
from qgis.PyQt.QtWidgets import QLayout, QSizePolicy, QStyle, QWidget, QWidgetItem


class ColumnFlowLayout(QLayout):
    """Items in row-major order in as many aligned columns as fit."""

    def __init__(self, parent=None, wide_min_width=240):
        super().__init__(parent)
        self._items = []
        self._wide = None
        self._wide_min_width = wide_min_width
        self._height = None  # at the current width

    # QLayout interface
    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items) + (self._wide is not None)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        if index == len(self._items) and self._wide is not None:
            return self._wide
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        if index == len(self._items) and self._wide is not None:
            item, self._wide = self._wide, None
            return item
        return None

    def setWideWidget(self, widget):
        """The item after the columns that takes the rest of the last row."""
        self.addChildWidget(widget)
        self._wide = QWidgetItem(widget)
        self.invalidate()

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        # No: Qt would take the minimum height at the narrowest width (one
        # column). minimumSize() follows the current width instead.
        return False

    def heightForWidth(self, width):
        return self._place(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        height = self._place(rect, apply=True)
        if height != self._height:
            # The row count changed with the width: the window's minimum
            # height follows (after this layout pass, not inside it).
            self._height = height
            QTimer.singleShot(0, self._height_changed)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        """One column wide; as high as the rows at the current width need
        (not the one-column height, which would make the window tall)."""
        margins = self.contentsMargins()
        width = max([self._column_width()] + [self._wide_min_width if self._wide is not None else 0])
        width += margins.left() + margins.right()
        current = self.geometry().width()
        return QSize(width, self.heightForWidth(current if current > width else width * 3))

    def _height_changed(self):
        self.invalidate()
        if self.parentWidget() is not None:
            self.parentWidget().updateGeometry()

    # Placement
    def _spacing(self, orientation):
        spacing = self.spacing()
        if spacing >= 0:
            return spacing
        parent = self.parentWidget()
        if parent is None:
            return 6
        metric = (QStyle.PixelMetric.PM_LayoutHorizontalSpacing if orientation == Qt.Orientation.Horizontal
                  else QStyle.PixelMetric.PM_LayoutVerticalSpacing)
        value = parent.style().pixelMetric(metric, None, parent)
        return value if value >= 0 else 6

    def _column_width(self):
        return max([item.sizeHint().width() for item in self._items] or [0])

    def _row_height(self):
        return max([item.sizeHint().height() for item in self._items] or [0])

    def _wide_height(self):
        return self._wide.sizeHint().height() if self._wide is not None else 0

    def _columns(self, width, h_space):
        """The most columns whose natural widths (each column as wide as its
        widest item, like a grid) fit in `width`, and those widths."""
        widths = [item.sizeHint().width() for item in self._items]
        for columns in range(max(1, len(widths)), 0, -1):
            natural = [max(widths[col::columns]) for col in range(columns)]
            if sum(natural) + (columns - 1) * h_space <= width or columns == 1:
                return columns, natural
        return 1, [0]

    def _place(self, rect, apply):
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        h_space, v_space = self._spacing(Qt.Orientation.Horizontal), self._spacing(Qt.Orientation.Vertical)
        columns, natural = self._columns(area.width(), h_space)
        # The width left over is shared out evenly, so the columns spread
        # across the whole row.
        extra = max(0, area.width() - sum(natural) - (columns - 1) * h_space) / columns
        lefts, left = [], float(area.x())
        for width in natural:
            lefts.append(round(left))
            left += width + extra + h_space
        row_height = self._row_height()
        if self._wide is not None and len(self._items) % columns:
            row_height = max(row_height, self._wide_height())  # it may share the last row
        rows = -(-len(self._items) // columns) if self._items else 0
        if apply:
            for index, item in enumerate(self._items):
                row, col = divmod(index, columns)
                item.setGeometry(QRect(QPoint(lefts[col], area.y() + row * (row_height + v_space)),
                                       QSize(round(natural[col] + extra), row_height)))
        height = rows * row_height + max(0, rows - 1) * v_space
        if self._wide is not None:
            used = len(self._items) % columns
            wide_height = self._wide_height()
            left = lefts[used] if used else area.x()
            if used and area.right() + 1 - left < self._wide_min_width:
                used = 0  # too little room left: a row of its own
                left = area.x()
            if used:
                top = area.y() + (rows - 1) * (row_height + v_space) + (row_height - wide_height) // 2
            else:
                top = area.y() + height + (v_space if rows else 0)
                height += wide_height + (v_space if rows else 0)
            if apply:
                self._wide.setGeometry(QRect(left, top, area.right() + 1 - left, wide_height))
        return height + margins.top() + margins.bottom()


def flow_container(widgets, wide=None, parent=None):
    """A widget holding `widgets` in a ColumnFlowLayout (and the wide one last)."""
    holder = QWidget(parent)
    layout = ColumnFlowLayout(holder)
    layout.setContentsMargins(0, 0, 0, 0)
    for widget in widgets:
        layout.addWidget(widget)
    if wide is not None:
        layout.setWideWidget(wide)
    holder.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    return holder
