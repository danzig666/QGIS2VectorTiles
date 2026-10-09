"""
Zoomed-out load of the published layers (Publish window, Map tab:
*Zoomed-out load…*): which layers make the web map slow when zoomed out,
about how much, and from which zoom their features, or only their labels,
are worth drawing. The checked suggestions become the layers' web-only
visible scales (the Scales column) with one click.
"""

from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtGui import QGuiApplication
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox,
                                 QHBoxLayout, QHeaderView, QLabel, QSpinBox, QTableWidget,
                                 QTableWidgetItem, QVBoxLayout, QWidget)

from ..publishing import zoom_load
from .scale_range import scale_text

COL_LAYER, COL_COUNT, COL_NOW, COL_FROM, COL_LABELS_NOW, COL_LABELS_FROM = range(6)


def tr(text: str) -> str:
    return QCoreApplication.translate("ZoomLoad", text)


def size_text(nbytes: float) -> str:
    if nbytes >= 1024 * 1024:
        return f"{nbytes / 1024 / 1024:.1f} MB"
    return f"{max(1, round(nbytes / 1024))} KB"


def count_text(value: float) -> str:
    return f"{int(round(value)):,}".replace(",", " ")


class _ZoomPick(QWidget):
    """A checkbox and a zoom: "☑ z14 (1:34 000)"."""

    def __init__(self, zoom: int, low: int, high: int, factor: float, checked: bool, on_change):
        super().__init__()
        self.factor = factor
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        self.check = QCheckBox()
        self.check.setChecked(checked)
        self.spin = QSpinBox()
        self.spin.setRange(low, high)
        self.spin.setPrefix("z")
        self.spin.setValue(zoom)
        self.spin.valueChanged.connect(self._show_scale)
        self.check.toggled.connect(lambda *_: on_change())
        self.spin.valueChanged.connect(lambda *_: on_change())
        layout.addWidget(self.check)
        layout.addWidget(self.spin)
        self._show_scale()

    def _show_scale(self, *_):
        self.spin.setSuffix(f"  ({scale_text(self.scale())})")

    def scale(self) -> float:
        return zoom_load.scale_for_zoom(self.factor, self.spin.value())

    def chosen(self):
        return self.spin.value() if self.check.isChecked() else None


class ZoomLoadDialog(QDialog):
    """The analysis result; ``chosen()`` after accept: {layer id:
    (min scale or None, labels min scale or None)} of the checked rows."""

    def __init__(self, parent, loads, factor: float, zooms):
        super().__init__(parent)
        self.loads, self.factor, self.zooms = list(loads), factor, (int(zooms[0]), int(zooms[1]))
        self.setWindowTitle(tr("Zoomed-out load of the web map"))
        self.resize(1000, 540)
        layout = QVBoxLayout(self)
        intro = QLabel(tr(
            "Estimated from a sample of each layer's features in the export extent. Zoomed out, one "
            "tile holds a whole area: every feature in it is loaded and drawn, even when it is "
            "smaller than a pixel, and every label is laid out though few of them fit. The checked "
            "suggestions hide the features, or only the labels, when zoomed out beyond the zoom "
            "shown; they are not tiled there either. Only the web map changes, not the QGIS project."))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.table = QTableWidget(len(self.loads), 6)
        self.table.setHorizontalHeaderLabels([
            tr("Layer"), tr("Features"), tr("Busiest tile, zoomed out"), tr("Features from"),
            tr("Labels, zoomed out"), tr("Labels from")])
        self.table.horizontalHeaderItem(COL_NOW).setToolTip(tr(
            "The layer's part of the heaviest tile at the most zoomed-out zoom it is shown at now"))
        self.table.horizontalHeaderItem(COL_FROM).setToolTip(tr(
            "Suggested: the layer (features and labels) hidden when zoomed out beyond this zoom; "
            "below it the typical feature is smaller than a pixel or two"))
        self.table.horizontalHeaderItem(COL_LABELS_NOW).setToolTip(tr(
            "How many labels the heaviest tile has, and about how many of them can be placed"))
        self.table.horizontalHeaderItem(COL_LABELS_FROM).setToolTip(tr(
            "Suggested: only the labels hidden when zoomed out beyond this zoom; the features are "
            "still drawn there"))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        for column in range(6):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.picks = {}
        for row, load in enumerate(self.loads):
            self._fill_row(row, load)
        for column, kind in ((COL_FROM, "features"), (COL_LABELS_FROM, "labels")):
            # Resizing to contents does not see the cell widgets.
            widths = [picks[kind].sizeHint().width() + 8 for picks in self.picks.values() if kind in picks]
            if widths:
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
                header.resizeSection(column, max(widths + [header.sectionSize(column)]))
        layout.addWidget(self.table, 1)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = buttons.addButton(tr("Apply the checked limits"),
                                              QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_summary()
        width = sum(header.sectionSize(c) for c in range(6)) + 60   # the whole table, no scrolling
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            width = min(width, screen.availableGeometry().width() - 40)
        self.resize(max(self.width(), width), self.height())

    # ------------------------------------------------------------------ rows
    def _item(self, row: int, column: int, text: str, tip: str = "") -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        if tip:
            item.setToolTip(tip)
        if column != COL_LAYER:
            item.setTextAlignment(int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
        self.table.setItem(row, column, item)
        return item

    def _fill_row(self, row: int, load) -> None:
        high = self.zooms[1]
        self._item(row, COL_LAYER, load.name)
        self._item(row, COL_COUNT, count_text(load.features))
        start = load.shown_from
        feature_bytes = load.tile_bytes.get(start, 0)
        count = load.tile_features.get(start, 0)
        if not load.draws_features:
            text = tr("z{}: labels only").format(start)
        elif count == 1:
            text = tr("z{}: 1 feature, {}").format(start, size_text(feature_bytes))
        else:
            text = tr("z{}: {} features, {}").format(start, count_text(count), size_text(feature_bytes))
        self._item(row, COL_NOW, text, tr("Shown now from zoom {} on").format(start)
                   + (tr("\nHeavy: slows the map down") if load.heavy(start) else ""))
        picks = {}
        if load.draws_features and load.suggested_from is not None:
            picks["features"] = _ZoomPick(load.suggested_from, start + 1, high, self.factor, True,
                                          self._update_summary)
            self.table.setCellWidget(row, COL_FROM, picks["features"])
        else:
            reason = tr("Light enough") if not load.heavy(start) else tr("Visible there")
            self._item(row, COL_FROM, "—", reason)
        if load.labels and load.label_share > 0:
            begin = load.labels_from
            labels = load.tile_features.get(begin, 0) * load.label_share
            placed = load.labels_shown(begin)
            self._item(row, COL_LABELS_NOW, tr("z{}: {} labels, ~{}% fit").format(
                begin, count_text(labels), max(1, int(round(placed * 100))) if labels else 0),
                tr("Shown now from zoom {} on").format(begin))
            if load.suggested_labels_from is not None:
                picks["labels"] = _ZoomPick(load.suggested_labels_from, begin + 1, high, self.factor,
                                            True, self._update_summary)
                self.table.setCellWidget(row, COL_LABELS_FROM, picks["labels"])
            else:
                self._item(row, COL_LABELS_FROM, "—", tr("Most labels fit there"))
        else:
            self._item(row, COL_LABELS_NOW, tr("no labels") if not load.labels else tr("no label texts"))
            self._item(row, COL_LABELS_FROM, "")
        self.picks[load.layer_id] = picks

    # ------------------------------------------------------------------ result
    def _limits(self, load):
        """(features from, labels from) zooms with the checked picks applied."""
        picks = self.picks.get(load.layer_id, {})
        features = picks["features"].chosen() if "features" in picks else None
        labels = picks["labels"].chosen() if "labels" in picks else None
        start = max(load.shown_from, features or 0)
        return start, max(start, load.labels_from, labels or 0)

    def _tile_bytes(self, zoom: int, after: bool) -> float:
        total = 0.0
        for load in self.loads:
            start, labels_start = self._limits(load) if after else (load.shown_from, load.labels_from)
            if zoom >= start:
                total += load.tile_bytes.get(zoom, 0)
            if load.labels and zoom >= labels_start:
                total += load.label_bytes.get(zoom, 0)
        return total

    def _update_summary(self) -> None:
        low, high = self.zooms
        worst_now = max(range(low, high + 1), key=lambda z: self._tile_bytes(z, False))
        now, after = self._tile_bytes(worst_now, False), self._tile_bytes(worst_now, True)
        worst_after = max(range(low, high + 1), key=lambda z: self._tile_bytes(z, True))
        text = tr("Heaviest tile now: about {} (zoom {}, all layers together).").format(
            size_text(now), worst_now)
        if self.chosen():
            text += " " + tr("With the checked limits: about {} (zoom {}); {} at zoom {}.").format(
                size_text(self._tile_bytes(worst_after, True)), worst_after, size_text(after), worst_now)
        self.summary.setText(text)

    def chosen(self):
        """{layer id: (min scale or None, labels min scale or None)} of the
        checked suggestions (layers without one are left out)."""
        result = {}
        for load in self.loads:
            picks = self.picks.get(load.layer_id, {})
            features = picks["features"].chosen() if "features" in picks else None
            labels = picks["labels"].chosen() if "labels" in picks else None
            if features is None and labels is None:
                continue
            if labels is not None and features is not None and labels <= features:
                labels = None  # the layer is hidden there anyway
            result[load.layer_id] = (
                zoom_load.scale_for_zoom(self.factor, features) if features is not None else None,
                zoom_load.scale_for_zoom(self.factor, labels) if labels is not None else None)
        return result
