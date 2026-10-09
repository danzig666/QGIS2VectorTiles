"""
Web-only visible scale range of layers (Publish window, Map tab): the scales
a layer is shown at in the web map, on top of its own QGIS scale range.
Where a layer is hidden it is not tiled either, so a map with many detailed
layers stays fast when zoomed out (and the export is smaller).
"""

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                                 QLabel, QVBoxLayout)


def tr(text: str) -> str:
    return QCoreApplication.translate("ScaleRange", text)


def scale_text(value: float) -> str:
    """1:25 000 (QGIS-style, thin spaces between thousands)."""
    return "1:" + f"{int(round(value)):,}".replace(",", " ")


def range_text(low: float, high: float) -> str:
    """Column text of a scale range (0 = no limit): "1:25 000 –", "– 1:500"."""
    if not low and not high:
        return ""
    return f"{scale_text(low) if low else ''} – {scale_text(high) if high else ''}".strip()


def _scale_widget(canvas=None):
    try:
        from qgis.gui import QgsScaleWidget  # pylint: disable=import-outside-toplevel
    except ImportError:  # pragma: no cover - QGIS always has it
        QgsScaleWidget = None  # pylint: disable=invalid-name
    if QgsScaleWidget is None:
        from qgis.PyQt.QtWidgets import QDoubleSpinBox  # pylint: disable=import-outside-toplevel
        box = QDoubleSpinBox()
        box.setRange(1, 1e9)
        box.setDecimals(0)
        box.scale, box.setScale = box.value, box.setValue
        return box
    widget = QgsScaleWidget()
    if canvas is not None:
        widget.setMapCanvas(canvas)  # adds "current canvas scale"
        widget.setShowCurrentScaleButton(True)
    return widget


def labels_text(labels_low: float) -> str:
    """Column text of the labels-only limit: "labels 1:10 000 –"."""
    return tr("labels {} –").format(scale_text(labels_low)) if labels_low else ""


class ScaleRangeDialog(QDialog):
    """Three optional limits: hidden when zoomed out beyond / in beyond, and
    only the labels hidden when zoomed out beyond."""

    def __init__(self, parent=None, low: float = 0.0, high: float = 0.0, count: int = 1,
                 qgis_range: str = "", canvas=None, labels_low: float = 0.0):
        super().__init__(parent)
        self.setWindowTitle(tr("Visible scales in the web map"))
        layout = QVBoxLayout(self)
        intro = QLabel(tr("{} layer(s). Where a layer is hidden it is not tiled either: the web map "
                          "stays fast when zoomed out and the export is smaller. This only affects "
                          "the web map; the QGIS project is not changed.").format(count))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        if qgis_range:
            own = QLabel(tr("In QGIS the layer is shown at: {}").format(qgis_range))
            own.setWordWrap(True)
            layout.addWidget(own)
        form = QFormLayout()
        self.out_check = QCheckBox(tr("Hide when zoomed out beyond"))
        self.out_scale = _scale_widget(canvas)
        self.in_check = QCheckBox(tr("Hide when zoomed in beyond"))
        self.in_scale = _scale_widget(canvas)
        self.labels_check = QCheckBox(tr("Hide only the labels when zoomed out beyond"))
        self.labels_check.setToolTip(tr("The features are still drawn there, without their labels: "
                                        "labels zoomed far out are slow and mostly cannot be placed"))
        self.labels_scale = _scale_widget(canvas)
        for check, widget, value, default in ((self.out_check, self.out_scale, low, 25000),
                                              (self.in_check, self.in_scale, high, 500),
                                              (self.labels_check, self.labels_scale, labels_low, 10000)):
            check.setChecked(bool(value))
            widget.setScale(value or default)
            widget.setEnabled(bool(value))
            check.toggled.connect(widget.setEnabled)
            row = QHBoxLayout()
            row.addWidget(widget, 1)
            form.addRow(check, row)
        layout.addLayout(form)
        hint = QLabel(tr("Example: buildings and contours hidden when zoomed out beyond 1:10 000; "
                         "a small-scale overview hidden when zoomed in beyond 1:5 000; parcel "
                         "numbers hidden when zoomed out beyond 1:4 000, the parcels still drawn."))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.error = QLabel()
        layout.addWidget(self.error)

    def values(self):
        """(zoomed-out limit, zoomed-in limit, labels' zoomed-out limit); 0 = none."""
        low = float(self.out_scale.scale()) if self.out_check.isChecked() else 0.0
        high = float(self.in_scale.scale()) if self.in_check.isChecked() else 0.0
        labels_low = float(self.labels_scale.scale()) if self.labels_check.isChecked() else 0.0
        return low, high, labels_low

    def _accept(self):
        low, high, _labels_low = self.values()
        if low and high and high >= low:
            self.error.setText(tr("The zoomed-out limit must be a smaller scale (larger number) "
                                  "than the zoomed-in limit."))
            return
        self.accept()
