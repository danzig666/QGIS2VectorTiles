"""
Zoomed-out load of the published layers (Publish window, Map tab:
*Zoomed-out load…*): which layers make the web map slow when zoomed out,
about how much, and from which zoom their features, or only their labels,
are worth drawing. The checked suggestions become the layers' web-only
visible scales (the Scales column) with one click. A slider sets how eager
the suggestions are, from "very slow" (show as much as possible zoomed out)
to "very fast" (hide small features and crowded labels early).
"""

from qgis.PyQt.QtCore import QCoreApplication, Qt
from qgis.PyQt.QtGui import QGuiApplication
from qgis.core import QgsSettings
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox,
                                 QHBoxLayout, QHeaderView, QLabel, QLineEdit, QSlider, QSpinBox,
                                 QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..publishing import zoom_load
from .layer_search import matches
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
    """A checkbox and an editable zoom: "☑ z14 (1:34 000)". Changing the
    zoom ticks the box; the lowest zoom means no limit (shown from the
    web map's first zoom)."""

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
        self.spin.setToolTip(tr("From which zoom it is drawn; the lowest zoom: no limit"))
        self.spin.valueChanged.connect(self._show_scale)
        self.spin.valueChanged.connect(lambda *_: self.check.setChecked(True))
        self.check.toggled.connect(lambda *_: on_change())
        self.spin.valueChanged.connect(lambda *_: on_change())
        layout.addWidget(self.check)
        layout.addWidget(self.spin)
        self._show_scale()

    def _show_scale(self, *_):
        if self.spin.value() <= self.spin.minimum():
            self.spin.setSuffix("  " + tr("(no limit)"))
        else:
            self.spin.setSuffix(f"  ({scale_text(self.scale())})")

    def scale(self) -> float:
        """The minimum scale of the zoom; 0 at the lowest zoom (no limit)."""
        if self.spin.value() <= self.spin.minimum():
            return 0.0
        return zoom_load.scale_for_zoom(self.factor, self.spin.value())

    def chosen(self):
        return self.spin.value() if self.check.isChecked() else None


SPEED_KEY = "QWebMap/zoomLoadSpeed"


def speed_names():
    return [tr("Very slow"), tr("Slow"), tr("Balanced"), tr("Fast"), tr("Very fast")]


class ZoomLoadDialog(QDialog):
    """The analysis result; ``chosen()`` after accept: {layer id:
    (min scale or None, labels min scale or None)} of the checked rows."""

    def __init__(self, parent, loads, factor: float, zooms):
        super().__init__(parent)
        self.loads, self.factor, self.zooms = list(loads), factor, (int(zooms[0]), int(zooms[1]))
        try:
            speed = int(QgsSettings().value(SPEED_KEY, zoom_load.BALANCED))
        except (TypeError, ValueError):
            speed = zoom_load.BALANCED
        speed = max(0, min(len(zoom_load.SPEEDS) - 1, speed))
        if speed != zoom_load.BALANCED:
            zoom_load.resuggest(self.loads, self.zooms[1], speed)
        self.setWindowTitle(tr("Zoomed-out load of the web map"))
        self.resize(1000, 540)
        layout = QVBoxLayout(self)
        intro = QLabel(tr(
            "Estimated from a sample of each layer's features in the export extent. Zoomed out, one "
            "tile holds a whole area: every feature in it is loaded and drawn, even when it is "
            "smaller than a pixel, and every label is laid out though few of them fit. The ticked "
            "zooms hide the features, or only the labels, when zoomed out beyond them; they are not "
            "tiled there either. Every zoom can be changed by hand (changing it ticks it); the lowest "
            "zoom removes a limit. Only the web map changes, not the QGIS project."))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.find = QLineEdit()
        self.find.setClearButtonEnabled(True)
        self.find.setPlaceholderText(tr("Find a layer…"))
        self.find.setToolTip(tr("Shows the layers whose name contains this text (upper or lower case, "
                                "accents not needed); hidden rows keep their ticks"))
        self.find.textChanged.connect(self._filter_rows)
        layout.addWidget(self.find)
        speed_row = QHBoxLayout()
        speed_row.addWidget(QLabel(tr("Zoomed out, the web map should be:")))
        speed_row.addWidget(QLabel(tr("slow, showing much")))
        self.speed = QSlider(Qt.Orientation.Horizontal)
        self.speed.setRange(0, len(zoom_load.SPEEDS) - 1)
        self.speed.setPageStep(1)
        self.speed.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.speed.setValue(speed)
        self.speed.setToolTip(tr(
            "Faster: small features and crowded labels are hidden from closer zooms on (less to load "
            "and draw zoomed out). Slower: more is shown zoomed out. Zoomed in, everything is shown "
            "either way. Moving it suggests the zooms again (hand-set zooms are replaced)."))
        speed_row.addWidget(self.speed, 1)
        speed_row.addWidget(QLabel(tr("fast, showing little")))
        self.speed_name = QLabel()
        self.speed_name.setMinimumWidth(80)
        speed_row.addWidget(self.speed_name)
        layout.addLayout(speed_row)
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
        self._show_speed_name()
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
        self.speed.valueChanged.connect(self._speed_changed)
        width = sum(header.sectionSize(c) for c in range(6)) + 60   # the whole table, no scrolling
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            width = min(width, screen.availableGeometry().width() - 40)
        self.resize(max(self.width(), width), self.height())

    def _show_speed_name(self) -> None:
        self.speed_name.setText(speed_names()[self.speed.value()])

    def _speed_changed(self, value: int) -> None:
        """Suggest the zooms again at this speed (remembered for next time)."""
        QgsSettings().setValue(SPEED_KEY, int(value))
        zoom_load.resuggest(self.loads, self.zooms[1], value)
        for row, load in enumerate(self.loads):
            for column in (COL_FROM, COL_LABELS_FROM):
                self.table.removeCellWidget(row, column)
            self._fill_row(row, load)
        self._filter_rows(self.find.text())
        self._show_speed_name()
        self._update_summary()

    def _filter_rows(self, text: str) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_LAYER)
            self.table.setRowHidden(row, bool(text) and not matches(item.text() if item else "", text))

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
        low = self.zooms[0]
        # Every layer's zooms can be set by hand: a suggestion is ticked, else
        # the zoom it is shown from now is offered unticked.
        if load.draws_features:
            suggested = load.suggested_from is not None
            picks["features"] = _ZoomPick(load.suggested_from if suggested else start, low, high,
                                          self.factor, suggested, self._update_summary)
            if not suggested:
                picks["features"].setToolTip(tr("Light enough") if not load.heavy(start)
                                             else tr("Visible there"))
            self.table.setCellWidget(row, COL_FROM, picks["features"])
        else:
            self._item(row, COL_FROM, "—", tr("Labels only"))
        if load.labels and load.label_share > 0:
            begin = load.labels_from
            labels = load.tile_features.get(begin, 0) * load.label_share
            placed = load.labels_shown(begin)
            self._item(row, COL_LABELS_NOW, tr("z{}: {} labels, ~{}% fit").format(
                begin, count_text(labels), max(1, int(round(placed * 100))) if labels else 0),
                tr("Shown now from zoom {} on").format(begin))
            suggested = load.suggested_labels_from is not None
            picks["labels"] = _ZoomPick(load.suggested_labels_from if suggested else begin, low, high,
                                        self.factor, suggested, self._update_summary)
            if not suggested:
                picks["labels"].setToolTip(tr("Most labels fit there"))
            self.table.setCellWidget(row, COL_LABELS_FROM, picks["labels"])
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
        start = load.shown_from if features is None else features
        return start, max(start, load.labels_from if labels is None else labels)

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
        ticked zooms (layers without one are left out); 0: no limit."""
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
                picks["features"].scale() if features is not None else None,
                picks["labels"].scale() if labels is not None else None)
        return result
