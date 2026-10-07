"""
"Publish Web Map" window.

Tabs: Map, Interaction, Output, Destination, Review. Every (non-secret)
setting is saved in the QGIS project (publication_profiles.py); keys are
kept in the QGIS authentication database or only for this session.

* Export locally — runs the exporter on QGIS's main thread (its
  NoThreading guarantees), builds a validated immutable release and keeps
  the project unchanged (layer tree, visibility, styles).
* Preview — serves the local publication on 127.0.0.1 with byte ranges and
  opens it in the web browser (no Node, no tile server).
* Publish — uploads the validated release in a background task (files and
  network only), verifies it through the public URL and only then
  activates it. The first publication and every change of what becomes
  public need an explicit approval on the Review tab.
"""

import datetime as _dt
import os
import traceback

from qgis.core import (Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
                       QgsIconUtils, QgsLayerTreeGroup, QgsLayerTreeLayer, QgsMessageLog,
                       QgsProcessingFeedback, QgsProject, QgsRasterLayer, QgsRectangle, QgsSettings,
                       QgsTask, QgsVectorLayer)
from qgis.PyQt.QtCore import QCoreApplication, Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices, QGuiApplication
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                                 QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                                 QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                                 QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                                 QRadioButton, QSpinBox, QSplitter, QStackedWidget, QTableWidget,
                                 QTableWidgetItem, QTabWidget, QTextBrowser, QToolButton, QTreeWidget,
                                 QTreeWidgetItem, QVBoxLayout, QWidget)

from ..publishing.errors import PublishingError
from ..publishing.models import (XyzBasemap, BASEMAP_FLAVORS, FIELD_TYPES, CutLineConfig, FilterField, GroupConfig,
                                 LayerConfig, PopupField, PublicationProfile, ReleaseState,
                                 RestrictionConfig, slugify)
from ..publishing.profile import disclosure_fingerprint, needs_review, publication_prefix, validate
from . import publication_profiles as store

TAG = "QWebMap"
CHECKED = Qt.CheckState.Checked
UNCHECKED = Qt.CheckState.Unchecked
PARTIAL = Qt.CheckState.PartiallyChecked
LAYER_ROLE = Qt.ItemDataRole.UserRole
GROUP_ROLE = Qt.ItemDataRole.UserRole + 1
SCALES_ROLE = Qt.ItemDataRole.UserRole + 2  # [min scale, max scale], web only
COL_PUBLISH, COL_VISIBLE, COL_TOGGLE, COL_LEGEND, COL_SCALES = 1, 2, 3, 4, 5


def publishable(layer) -> bool:
    """Vector layers with geometry (MVT) and raster layers (own image archive)."""
    return (isinstance(layer, QgsVectorLayer) and layer.isSpatial()) or isinstance(layer, QgsRasterLayer)


def tr(text: str) -> str:
    return QCoreApplication.translate("PublishDialog", text)


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def _check(value: bool):
    return CHECKED if value else UNCHECKED


class DialogFeedback(QgsProcessingFeedback):
    """Feedback for main-thread stages: progress bar + log, cancellable."""

    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog

    def setProgress(self, value):  # noqa: N802
        super().setProgress(value)
        self.dialog.progress.setValue(int(max(0, min(100, value))))
        QCoreApplication.processEvents()

    def pushInfo(self, info):  # noqa: N802
        self.dialog.log(info)

    def pushWarning(self, warning):  # noqa: N802
        self.dialog.log(f"⚠ {warning}")

    def reportError(self, error, fatalError=False):  # noqa: N802,N803
        self.dialog.log(f"✖ {error}")


class UploadTask(QgsTask):
    """Upload, verify and activate a validated local release (no QGIS
    project access: files and network only)."""

    message = pyqtSignal(str)

    def __init__(self, release, profile, provider, work_dir, secrets, activate=True):
        super().__init__(tr("Publishing web map"), QgsTask.Flag.CanCancel)
        self.release, self.profile, self.provider = release, profile, provider
        self.work_dir, self.secrets, self.activate_release = work_dir, list(secrets), activate
        self.result = None
        self.error = ""

    def run(self):
        from ..publishing.deployments import publish  # pylint: disable=import-outside-toplevel
        from ..publishing.progress import Progress  # pylint: disable=import-outside-toplevel

        def feedback(percent, text):
            if percent is not None:
                self.setProgress(percent)
            if text:
                self.message.emit(text)
        try:
            self.result = publish(self.release, self.profile, self.provider, self.work_dir,
                                  Progress(feedback, cancel=self.isCanceled), secrets=self.secrets,
                                  activate_release=self.activate_release)
            return True
        except Exception:  # noqa: BLE001 - reported in finished()
            self.error = traceback.format_exc()
            for secret in self.secrets:
                self.error = self.error.replace(secret, "***")
            return False


class PublishDialog(QDialog):
    def __init__(self, iface=None, parent=None):
        super().__init__(parent or (iface.mainWindow() if iface else None))
        self.iface = iface
        self.project = QgsProject.instance()
        self.setWindowTitle(tr("Publish Web Map — QWebMap"))
        self.resize(980, 760)
        self._restore_geometry()
        self.layer_configs = {}
        self.current_layer_id = None
        self.local_result = None
        self.local_profile_json = None
        self.preview_server = None
        self.session_credentials = None
        self.task = None
        self.feedback = None
        self.profile = self._load_profile()
        self._build()
        self._populate(self.profile)

    # ------------------------------------------------------------------ profile
    def _default_profile(self) -> PublicationProfile:
        title = self.project.title() or os.path.splitext(os.path.basename(self.project.fileName()))[0] \
            or tr("Web map")
        profile = PublicationProfile(title=title, slug=slugify(title))
        for node in self.project.layerTreeRoot().findLayers():
            layer = node.layer()
            if publishable(layer):
                profile.layers.append(LayerConfig(layer.id(), included=node.isVisible(),
                                                  initially_visible=node.isVisible()))
        if self.iface is not None:
            profile.view.extent = self._canvas_extent_3857()
        base = os.path.dirname(self.project.fileName()) or os.path.expanduser("~")
        profile.output.local_directory = os.path.join(base, "web_maps")
        return profile

    def _load_profile(self) -> PublicationProfile:
        found = store.active_profile(self.project)
        if not found:
            return self._default_profile()
        profile, saved_from = found
        here = self.project.fileName()
        if saved_from and here and os.path.normcase(saved_from) != os.path.normcase(here):
            answer = QMessageBox.question(
                self, tr("Publication settings from another project file"),
                tr("These publication settings were saved in another project file:\n{}\n\n"
                   "Yes: keep updating the same web map (same address).\n"
                   "No: create a new, separate web map from these settings.").format(saved_from),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if answer == QMessageBox.StandardButton.No:
                profile = store.as_new_publication(profile)
        known = {c.layer_id for c in profile.layers}
        for node in self.project.layerTreeRoot().findLayers():
            layer = node.layer()
            if publishable(layer) and layer.id() not in known:
                profile.layers.append(LayerConfig(layer.id(), included=False, initially_visible=False))
        return profile

    def _canvas_extent_3857(self):
        canvas = self.iface.mapCanvas()
        transform = QgsCoordinateTransform(canvas.mapSettings().destinationCrs(),
                                           QgsCoordinateReferenceSystem("EPSG:3857"), self.project)
        box = transform.transformBoundingBox(canvas.extent())
        return [box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()]

    # ------------------------------------------------------------------ UI
    def _build(self):
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.tabs.addTab(self._map_tab(), tr("Map"))
        self.tabs.addTab(self._interaction_tab(), tr("Interaction"))
        self.tabs.addTab(self._basemap_tab(), tr("Basemap"))
        self.tabs.addTab(self._parcel_tab(), tr("Parcel report"))
        self.tabs.addTab(self._output_tab(), tr("Output"))
        self.tabs.addTab(self._destination_tab(), tr("Destination"))
        self.tabs.addTab(self._review_tab(), tr("Review"))
        self.tabs.currentChanged.connect(self._tab_changed)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumBlockCount(2000)
        self.logbox.setMaximumHeight(110)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.logbox)
        buttons = QHBoxLayout()
        self.btn_save = QPushButton(tr("Save settings"))
        self.btn_export = QPushButton(tr("Export locally"))
        self.btn_preview = QPushButton(tr("Preview"))
        self.btn_publish = QPushButton(tr("Publish"))
        self.btn_open = QPushButton(tr("Open map"))
        self.btn_copy = QPushButton(tr("Copy link"))
        self.btn_folder = QPushButton(tr("Open local package"))
        self.btn_cancel = QPushButton(tr("Cancel"))
        self.btn_close = QPushButton(tr("Close"))
        # Settings to / from a file (another project, a colleague, a backup).
        self.btn_settings_file = QToolButton()
        self.btn_settings_file.setText(tr("Settings file…"))
        self.btn_settings_file.setToolTip(tr("Export these settings to a file, or import settings "
                                             "from one (layers are matched by name in another project)."))
        self.btn_settings_file.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        file_menu = QMenu(self)
        file_menu.addAction(tr("Export settings to a file…"), self.export_settings_file)
        file_menu.addAction(tr("Import settings from a file…"), self.import_settings_file)
        self.btn_settings_file.setMenu(file_menu)
        for button in (self.btn_save, self.btn_settings_file, self.btn_export, self.btn_preview,
                       self.btn_publish, self.btn_open, self.btn_copy, self.btn_folder):
            buttons.addWidget(button)
        # Presets (domain conventions, e.g. a national zoning plan): only when a
        # variant of the plugin ships some (publishing/presets).
        from ..publishing import presets  # pylint: disable=import-outside-toplevel
        self.presets = presets.available()
        self.btn_preset = QToolButton()
        self.btn_preset.setText(tr("Preset…"))
        self.btn_preset.setToolTip(tr("Fill the settings from a preset; review them before publishing."))
        self.btn_preset.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self)
        for module in self.presets:
            menu.addAction(module.TITLE, lambda m=module: self.apply_preset(m))
        self.btn_preset.setMenu(menu)
        self.btn_preset.setVisible(bool(self.presets))
        buttons.addWidget(self.btn_preset)
        buttons.addStretch(1)
        buttons.addWidget(self.btn_cancel)
        buttons.addWidget(self.btn_close)
        layout.addLayout(buttons)
        self.btn_save.clicked.connect(self.save_settings)
        self.btn_export.clicked.connect(self.export_locally)
        self.btn_preview.clicked.connect(self.preview)
        self.btn_publish.clicked.connect(self.publish)
        self.btn_open.clicked.connect(self.open_map)
        self.btn_copy.clicked.connect(self.copy_link)
        self.btn_folder.clicked.connect(self.open_folder)
        self.btn_cancel.clicked.connect(self.cancel)
        self.btn_close.clicked.connect(self.close)
        self.btn_cancel.setEnabled(False)
        for button in (self.btn_preview, self.btn_open, self.btn_copy, self.btn_folder):
            button.setEnabled(False)
        self.public_url = ""

    def _map_tab(self):
        widget = QWidget()
        layout = QHBoxLayout(widget)
        form_box = QGroupBox(tr("Web map"))
        form = QFormLayout(form_box)
        self.e_title = QLineEdit()
        self.e_slug = QLineEdit()
        self.e_title.textEdited.connect(lambda text: self.e_slug.setText(slugify(text)))
        self.e_description = QPlainTextEdit()
        self.e_description.setMaximumHeight(70)
        self.e_locale = QComboBox()
        self.e_locale.addItem("Magyar", "hu")
        self.e_locale.addItem("English", "en")
        self.e_attribution = QLineEdit()
        logo_row = QHBoxLayout()
        self.e_logo = QLineEdit()
        logo_button = QPushButton("…")
        logo_button.clicked.connect(self._choose_logo)
        logo_row.addWidget(self.e_logo)
        logo_row.addWidget(logo_button)
        form.addRow(tr("Title"), self.e_title)
        form.addRow(tr("Address name (slug)"), self.e_slug)
        form.addRow(tr("Description"), self.e_description)
        form.addRow(tr("Language of the viewer"), self.e_locale)
        form.addRow(tr("Attribution"), self.e_attribution)
        form.addRow(tr("Logo (PNG/JPEG/WebP)"), logo_row)
        self.e_min_zoom = QSpinBox()
        self.e_min_zoom.setRange(0, 22)
        self.e_max_zoom = QSpinBox()
        self.e_max_zoom.setRange(0, 22)
        self.e_max_view = QSpinBox()
        self.e_max_view.setRange(0, 24)
        form.addRow(tr("Minimum tile zoom"), self.e_min_zoom)
        form.addRow(tr("Maximum tile zoom"), self.e_max_zoom)
        form.addRow(tr("Maximum browser zoom (overzoom)"), self.e_max_view)
        # Extent: a layer's extent (follows the layer) or a fixed one taken
        # from the map canvas; summarized in words, not raw coordinates.
        extent_box = QVBoxLayout()
        extent_row = QHBoxLayout()
        self.e_extent_layer = self._layer_combo(any_layer=True, allow_empty=True,
                                                empty_text=tr("Fixed extent (from the map canvas)"))
        self.e_extent_layer.setToolTip(tr("The published area: the extent of a layer (follows the "
                                          "layer when its data changes), or a fixed area."))
        self.e_extent_layer.layerChanged.connect(self._extent_layer_changed)
        extent_button = QPushButton(tr("Map canvas"))
        extent_button.setToolTip(tr("Fix the published area to what the QGIS map canvas shows now."))
        extent_button.clicked.connect(self._use_canvas_extent)
        extent_button.setEnabled(self.iface is not None)
        self.e_extent_layer.setMinimumContentsLength(12)
        extent_row.addWidget(self.e_extent_layer, 1)
        extent_row.addWidget(extent_button)
        extent_box.addLayout(extent_row)
        # Short lines, not wrapped: the form reserves their full height.
        self.extent_label = QLabel()
        self.extent_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        extent_box.addWidget(self.extent_label)
        form.addRow(tr("Extent"), extent_box)
        try:
            from qgis.gui import QgsColorButton  # pylint: disable=import-outside-toplevel
            self.e_accent = QgsColorButton()
            self.e_accent.setAllowOpacity(False)
        except ImportError:
            self.e_accent = QLineEdit()
        form.addRow(tr("Accent colour of the viewer"), self.e_accent)
        layout.addWidget(form_box, 2)
        layers_box = QGroupBox(tr("Layers (publishing does not change the project's layer visibility)"))
        layers_layout = QVBoxLayout(layers_box)
        theme_row = QHBoxLayout()
        self.theme_pick = QComboBox()
        self.theme_pick.setToolTip(tr("A QGIS map theme (View → Map Themes)"))
        publish_theme = QPushButton(tr("Publish its layers"))
        publish_theme.setToolTip(tr("Also publish the layers visible in this map theme (layers "
                                    "already published stay published, so several themes add up)"))
        publish_theme.clicked.connect(lambda: self._apply_theme(publish=True))
        start_theme = QPushButton(tr("Use as start view"))
        start_theme.setToolTip(tr("Visible at start: the layers visible in this map theme"))
        start_theme.clicked.connect(lambda: self._apply_theme(publish=False))
        theme_row.addWidget(QLabel(tr("Map theme:")))
        theme_row.addWidget(self.theme_pick, 1)
        theme_row.addWidget(publish_theme)
        theme_row.addWidget(start_theme)
        layers_layout.addLayout(theme_row)
        bulk_row = QHBoxLayout()
        self.bulk = QToolButton()
        self.bulk.setText(tr("Selected layers"))
        self.bulk.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.bulk.setMenu(self._bulk_menu())
        bulk_row.addWidget(QLabel(tr("Select several rows (Ctrl/Shift + click) to change them together:")), 1)
        bulk_row.addWidget(self.bulk)
        publish_visible = QPushButton(tr("Publish the visible layers"))
        publish_visible.setToolTip(tr("Publish exactly the layers visible in the QGIS Layers panel now "
                                      "(also visible at start); the hidden ones are no longer published"))
        publish_visible.clicked.connect(self.publish_visible_layers)
        bulk_row.addWidget(publish_visible)
        layers_layout.addLayout(bulk_row)
        self.tree = QTreeWidget()
        # Short headers (the layer names need the room); the meaning in tooltips.
        self.tree.setHeaderLabels([tr("Layer"), tr("Publish"), tr("At start"), tr("Switchable"),
                                   tr("Legend"), tr("Scales")])
        self.tree.headerItem().setToolTip(COL_LEGEND, tr("Shown in the web map's legend"))
        self.tree.headerItem().setToolTip(COL_PUBLISH, tr("Published in the web map"))
        self.tree.headerItem().setToolTip(COL_VISIBLE, tr("Visible when the web map opens"))
        self.tree.headerItem().setToolTip(COL_TOGGLE, tr("Visitors can switch it off"))
        self.tree.headerItem().setToolTip(COL_SCALES, tr(
            "Web map only: hide layers when zoomed out (or in) beyond a scale - faster when zoomed "
            "out, smaller export. Double-click a cell, or select rows → Selected layers → Visible "
            "scales…"))
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (COL_PUBLISH, COL_VISIBLE, COL_TOGGLE, COL_LEGEND, COL_SCALES):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemDoubleClicked.connect(self._tree_double_clicked)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(
            lambda pos: self._bulk_menu().exec(self.tree.viewport().mapToGlobal(pos)))
        self.tree.itemChanged.connect(self._tree_changed)
        layers_layout.addWidget(self.tree, 1)
        themes_row = QHBoxLayout()
        self.themes_list = QListWidget()
        self.themes_list.setMaximumHeight(84)
        self.themes_list.itemChanged.connect(lambda *_: self._refresh_initial_theme())
        self.theme_initial = QComboBox()
        themes_box = QVBoxLayout()
        themes_box.addWidget(QLabel(tr("Map themes offered as views in the web map:")))
        themes_box.addWidget(self.themes_list)
        initial_box = QVBoxLayout()
        initial_box.addWidget(QLabel(tr("Start with:")))
        initial_box.addWidget(self.theme_initial)
        initial_box.addStretch(1)
        themes_row.addLayout(themes_box, 2)
        themes_row.addLayout(initial_box, 1)
        layers_layout.addLayout(themes_row)
        layout.addWidget(layers_box, 3)
        return widget

    # ------------------------------------------------------------------ layer tree
    def _bulk_menu(self):
        menu = QMenu(self)
        for text, column, value in (
                (tr("Publish"), COL_PUBLISH, True), (tr("Do not publish"), COL_PUBLISH, False),
                (tr("Visible at start"), COL_VISIBLE, True), (tr("Hidden at start"), COL_VISIBLE, False),
                (tr("Can be switched off by visitors"), COL_TOGGLE, True),
                (tr("Always shown (cannot be switched off)"), COL_TOGGLE, False),
                (tr("Show in the legend"), COL_LEGEND, True), (tr("Hide from the legend"), COL_LEGEND, False)):
            menu.addAction(text, lambda c=column, v=value: self.apply_to_selection(c, v))
        menu.addSeparator()
        menu.addAction(tr("Visible scales…"), self.edit_scales)
        return menu

    def _selected_layer_items(self):
        """Selected layer rows; a selected group stands for every layer in it."""
        items, seen = [], set()
        for item in self.tree.selectedItems():
            for target in [item] + (list(self._descendants(item)) if item.data(0, GROUP_ROLE) else []):
                if target.data(0, LAYER_ROLE) and id(target) not in seen:
                    seen.add(id(target))
                    items.append(target)
        return items

    def _tree_double_clicked(self, item, column):
        if column == COL_SCALES and (item.data(0, LAYER_ROLE) or item.data(0, GROUP_ROLE)):
            if not item.isSelected():
                self.tree.clearSelection()
                item.setSelected(True)
            self.edit_scales()

    def edit_scales(self, values=None) -> bool:
        """Set the web-only visible scale range of the selected layers
        (``values``: (min scale, max scale) instead of asking)."""
        from .scale_range import ScaleRangeDialog, range_text  # pylint: disable=import-outside-toplevel
        items = self._selected_layer_items()
        if not items:
            self.status.setText(tr("Select layers (or groups) in the list first."))
            return False
        if values is None:
            low, high = items[0].data(COL_SCALES, SCALES_ROLE) or (0.0, 0.0)
            layer = self.project.mapLayer(items[0].data(0, LAYER_ROLE))
            own = range_text(layer.minimumScale(), layer.maximumScale()) \
                if layer is not None and layer.hasScaleBasedVisibility() else ""
            dialog = ScaleRangeDialog(self, low, high, len(items), own if len(items) == 1 else "",
                                      self.iface.mapCanvas() if self.iface is not None else None)
            if not dialog.exec():
                return False
            values = dialog.values()
        for item in items:
            self._set_item_scales(item, *values)
        return True

    def _set_item_scales(self, item, low, high):
        from .scale_range import range_text  # pylint: disable=import-outside-toplevel
        item.setData(COL_SCALES, SCALES_ROLE, [float(low or 0), float(high or 0)])
        layer = self.project.mapLayer(item.data(0, LAYER_ROLE))
        own = range_text(layer.minimumScale(), layer.maximumScale()) \
            if layer is not None and layer.hasScaleBasedVisibility() else ""
        text = range_text(low, high)
        item.setText(COL_SCALES, text or (tr("(QGIS {})").format(own) if own else ""))
        item.setForeground(COL_SCALES, self.palette().text() if text else self.palette().placeholderText())
        item.setToolTip(COL_SCALES, (tr("Web map: {}").format(text) if text else tr("No web limit"))
                        + (tr("\nQGIS layer: {}").format(own) if own else ""))

    def _descendants(self, item):
        for i in range(item.childCount()):
            child = item.child(i)
            yield child
            yield from self._descendants(child)

    def apply_to_selection(self, column: int, value: bool):
        """Set a column for the selected rows; a selected group also applies
        to every layer (and group) inside it."""
        targets = []
        for item in self.tree.selectedItems():
            targets.append(item)
            if item.data(0, GROUP_ROLE):
                targets.extend(self._descendants(item))
        seen = set()
        for item in targets:
            if id(item) in seen:
                continue
            seen.add(id(item))
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable and item.data(column, Qt.ItemDataRole.CheckStateRole) is not None:
                item.setCheckState(column, _check(value))

    def _apply_theme(self, publish: bool):
        name = self.theme_pick.currentText()
        if not name:
            return
        visible = set(self.project.mapThemeCollection().mapThemeVisibleLayerIds(name))
        for item in self._tree_items():
            shown = item.data(0, LAYER_ROLE) in visible
            if publish:
                # Additive: the theme's layers are published too, none are
                # unpublished (several themes can be combined).
                if shown:
                    item.setCheckState(COL_PUBLISH, CHECKED)
                    item.setCheckState(COL_VISIBLE, CHECKED)
            else:
                item.setCheckState(COL_VISIBLE, _check(shown))
        if publish:
            self.status.setText(tr('Layers of map theme "{}" added to the published layers.').format(name))
        else:
            self.status.setText(tr('Map theme "{}" applied to the layer list.').format(name))

    def publish_visible_layers(self):
        """Published layers := the layers visible in the QGIS layer tree now
        (a layer in a hidden group is hidden); they are visible at start."""
        root = self.project.layerTreeRoot()
        count = 0
        for item in self._tree_items():
            node = root.findLayer(item.data(0, LAYER_ROLE))
            shown = node is not None and node.isVisible()
            item.setCheckState(COL_PUBLISH, _check(shown))
            if shown:
                item.setCheckState(COL_VISIBLE, CHECKED)
                count += 1
        self.status.setText(tr("Published layers: the {} layers visible in QGIS.").format(count))

    def _refresh_initial_theme(self):
        current = self.theme_initial.currentData()
        self.theme_initial.blockSignals(True)
        self.theme_initial.clear()
        self.theme_initial.addItem(tr("(the layer settings above)"), "")
        for i in range(self.themes_list.count()):
            item = self.themes_list.item(i)
            if item.checkState() == CHECKED:
                self.theme_initial.addItem(item.text(), item.text())
        self.theme_initial.setCurrentIndex(max(0, self.theme_initial.findData(current or "")))
        self.theme_initial.blockSignals(False)

    def _choose_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Logo"), "", "Images (*.png *.jpg *.jpeg *.webp)")
        if path:
            self.e_logo.setText(path)

    def _use_canvas_extent(self):
        self.profile.view.extent = self._canvas_extent_3857()
        self.profile.view.extent_layer = ""
        self.e_extent_layer.blockSignals(True)
        self.e_extent_layer.setLayer(None)
        self.e_extent_layer.blockSignals(False)
        self._show_extent()

    def _extent_layer_changed(self, layer):
        self.profile.view.extent_layer = layer.id() if layer is not None else ""
        if layer is not None:
            self.profile.view.extent = self._layer_extent_3857(layer)
        elif not self.profile.view.extent and self.iface is not None:
            self.profile.view.extent = self._canvas_extent_3857()
        self._show_extent()

    def _layer_extent_3857(self, layer):
        transform = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem("EPSG:3857"),
                                           self.project)
        box = transform.transformBoundingBox(layer.extent())
        if box.isEmpty():
            return self.profile.view.extent
        return [box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()]

    def _current_extent_3857(self):
        """The extent setting now: the chosen layer's extent (refreshed), else
        the fixed extent, else the map canvas."""
        layer = self.project.mapLayer(self.profile.view.extent_layer) \
            if self.profile.view.extent_layer else None
        if layer is not None:
            self.profile.view.extent = self._layer_extent_3857(layer)
        return self.profile.view.extent or (self._canvas_extent_3857() if self.iface else None)

    def _show_extent(self):
        extent = self.profile.view.extent
        layer = self.project.mapLayer(self.profile.view.extent_layer) \
            if self.profile.view.extent_layer else None
        if not extent:
            self.extent_label.setText(tr("The map canvas extent\nat the time of export."))
            return
        transform = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:3857"),
                                           QgsCoordinateReferenceSystem("EPSG:4326"), self.project)
        box = transform.transformBoundingBox(QgsRectangle(*extent))
        # Ground size: Web Mercator units shrink by cos(latitude).
        import math  # pylint: disable=import-outside-toplevel
        scale = math.cos(math.radians((box.yMinimum() + box.yMaximum()) / 2))
        width = (extent[2] - extent[0]) * scale / 1000
        height = (extent[3] - extent[1]) * scale / 1000
        source = tr("Layer extent") if layer is not None else tr("Fixed extent")
        self.extent_label.setText(tr("{source}: about {w:.1f} × {h:.1f} km\n"
                                     "E {w0:.4f}° – {e0:.4f}°\nN {s0:.4f}° – {n0:.4f}°").format(
            source=source, w=width, h=height, w0=box.xMinimum(), e0=box.xMaximum(),
            s0=box.yMinimum(), n0=box.yMaximum()))
        self.extent_label.setToolTip(tr("Extent of “{}”").format(layer.name()) if layer is not None
                                     else tr("Fixed with “Map canvas”"))

    def _fill_tree(self, profile):
        self.tree.blockSignals(True)
        self.tree.clear()
        configs = {c.layer_id: c for c in profile.layers}

        def add(group, parent, path):
            for child in group.children():
                if isinstance(child, QgsLayerTreeGroup):
                    sub = path + (child.name(),)
                    config = profile.group(sub) or GroupConfig(list(sub))
                    item = QTreeWidgetItem(parent, [child.name()])
                    item.setData(0, GROUP_ROLE, list(sub))
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(COL_PUBLISH, UNCHECKED)
                    item.setCheckState(COL_VISIBLE, _check(config.initially_visible))
                    item.setCheckState(COL_TOGGLE, _check(config.toggleable))
                    font = item.font(0)
                    font.setBold(True)
                    item.setFont(0, font)
                    add(child, item, sub)
                    item.setExpanded(True)
                elif isinstance(child, QgsLayerTreeLayer):
                    layer = child.layer()
                    if not publishable(layer):
                        continue
                    config = configs.get(layer.id()) or LayerConfig(layer.id(), included=False,
                                                                     initially_visible=False)
                    raster = isinstance(layer, QgsRasterLayer)
                    item = QTreeWidgetItem(parent, [layer.name() + (tr(" (raster)") if raster else "")])
                    try:
                        item.setIcon(0, QgsIconUtils.iconForLayer(layer))
                    except (AttributeError, TypeError):
                        pass
                    item.setData(0, LAYER_ROLE, layer.id())
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(COL_PUBLISH, _check(config.included))
                    item.setCheckState(COL_VISIBLE, _check(config.initially_visible))
                    item.setCheckState(COL_TOGGLE, _check(config.toggleable))
                    item.setCheckState(COL_LEGEND, _check(config.legend))
                    self._set_item_scales(item, config.min_scale, config.max_scale)
        add(self.project.layerTreeRoot(), self.tree.invisibleRootItem(), ())
        self._sync_group_publish(self.tree.invisibleRootItem())
        self.tree.blockSignals(False)

    def _sync_group_publish(self, item):
        """Group "Publish" boxes show their layers: all, some or none."""
        states = []
        for i in range(item.childCount()):
            child = item.child(i)
            if child.data(0, GROUP_ROLE):
                states.append(self._sync_group_publish(child))
            elif child.data(0, LAYER_ROLE):
                states.append(child.checkState(COL_PUBLISH))
        if not item.data(0, GROUP_ROLE):
            return None
        states = [x for x in states if x is not None]
        state = CHECKED if states and all(x == CHECKED for x in states) else \
            UNCHECKED if all(x == UNCHECKED for x in states) else PARTIAL
        item.setCheckState(COL_PUBLISH, state)
        return state

    def _tree_changed(self, item, column):
        self.tree.blockSignals(True)
        try:
            if item.data(0, GROUP_ROLE) and column == COL_PUBLISH and item.checkState(column) != PARTIAL:
                for child in self._descendants(item):
                    if child.data(0, LAYER_ROLE):
                        child.setCheckState(COL_PUBLISH, item.checkState(column))
                        if item.checkState(column) == UNCHECKED:
                            child.setCheckState(COL_VISIBLE, UNCHECKED)
            if item.data(0, LAYER_ROLE):
                if column == COL_VISIBLE and item.checkState(COL_VISIBLE) == CHECKED:
                    item.setCheckState(COL_PUBLISH, CHECKED)
                if column == COL_PUBLISH and item.checkState(COL_PUBLISH) == UNCHECKED:
                    item.setCheckState(COL_VISIBLE, UNCHECKED)
            # Something that cannot be switched off must be visible at start.
            if column == COL_TOGGLE and item.checkState(COL_TOGGLE) == UNCHECKED:
                item.setCheckState(COL_VISIBLE, CHECKED)
                if item.data(0, LAYER_ROLE):
                    item.setCheckState(COL_PUBLISH, CHECKED)
            if column == COL_VISIBLE and item.checkState(COL_VISIBLE) == UNCHECKED:
                item.setCheckState(COL_TOGGLE, CHECKED)
            if column == COL_LEGEND and item.data(0, LAYER_ROLE):
                self._legend_changed(item.data(0, LAYER_ROLE), item.checkState(COL_LEGEND) == CHECKED)
            self._sync_group_publish(self.tree.invisibleRootItem())
        finally:
            self.tree.blockSignals(False)

    def _legend_changed(self, layer_id, shown: bool):
        """The Map tab's Legend column and the Interaction tab's "Show in the
        legend" are one setting."""
        config = self.layer_configs.setdefault(layer_id, LayerConfig(layer_id))
        config.legend = shown
        if layer_id == self.current_layer_id:
            for box in (self.i_legend, self.r_legend):
                box.blockSignals(True)
                box.setChecked(shown)
                box.blockSignals(False)

    def _legend_box_toggled(self, shown: bool):
        for item in self._tree_items():
            if item.data(0, LAYER_ROLE) == self.current_layer_id:
                self.tree.blockSignals(True)
                item.setCheckState(COL_LEGEND, _check(shown))
                self.tree.blockSignals(False)
        if self.current_layer_id:
            self.layer_configs.setdefault(self.current_layer_id, LayerConfig(self.current_layer_id)).legend = shown

    def _tree_items(self):
        stack = [self.tree.invisibleRootItem()]
        while stack:
            item = stack.pop()
            for i in range(item.childCount()):
                child = item.child(i)
                stack.append(child)
                if child.data(0, LAYER_ROLE):
                    yield child

    def _group_items(self):
        stack = [self.tree.invisibleRootItem()]
        while stack:
            item = stack.pop()
            for i in range(item.childCount()):
                child = item.child(i)
                stack.append(child)
                if child.data(0, GROUP_ROLE):
                    yield child

    def _interaction_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        options = QGroupBox(tr("Viewer"))
        grid = QGridLayout(options)
        self.i_flags = {}
        for index, (key, label) in enumerate([
                ("labels_toggle", tr("Labels switch")), ("opacity_controls", tr("Opacity sliders")),
                ("search", tr("Search")), ("filters", tr("Attribute filters")),
                ("popups", tr("Popups")), ("permalinks", tr("Shareable links")),
                ("coordinates", tr("Coordinates (WGS84 / EOV)")), ("measure", tr("Measurement")),
                ("print", tr("Print")),
                ("legend_visible_only", tr("Legend: only what is visible in the current view")),
                ("layers_panel", tr("Layers tab (off: the legend only)")),
                ("street_search", tr("Search street names (OpenStreetMap)")),
                ("street_view", tr("Street View (Google)"))]):
            box = QCheckBox(label)
            self.i_flags[key] = box
            grid.addWidget(box, index // 3, index % 3)
        self.i_google_key = QLineEdit()
        self.i_google_key.setPlaceholderText(tr("Google Maps API key (needed for Street View)"))
        self.i_google_key.setEnabled(False)
        self.i_flags["street_view"].toggled.connect(self.i_google_key.setEnabled)
        grid.addWidget(self.i_google_key, index // 3, index % 3 + 1, 1, 3 - index % 3 - 1 or 1)
        street_view_help = tr(
            "A Street View button on the map: tap a place to see it, the panorama looks toward the "
            "tapped point. Blue lines on the map show where Street View exists. Needs a Google "
            "Cloud API key with the Maps JavaScript API and the Map Tiles API (the blue lines) "
            "enabled. The key is visible in the published page: restrict it in Google Cloud to "
            "your site's address (HTTP referrers) and to these two APIs.")
        self.i_flags["street_view"].setToolTip(street_view_help)
        self.i_google_key.setToolTip(street_view_help)
        self.i_flags["street_search"].setToolTip(tr(
            "The search also finds the named streets of OpenStreetMap inside the extent layer "
            "(Map tab; without one, inside the export extent). They are read from the basemap, or "
            "without a basemap from the Protomaps build (internet needed while exporting). Only the "
            "names, a point and the bounds of each street are published."))
        layout.addWidget(options)
        split = QSplitter()
        split.setChildrenCollapsible(False)
        self.i_layers = QListWidget()
        # Room for layer names: wrapped, never squeezed by the field table.
        self.i_layers.setMinimumWidth(220)
        self.i_layers.setWordWrap(True)
        self.i_layers.setSpacing(2)
        self.i_layers.currentItemChanged.connect(self._interaction_layer_changed)
        split.addWidget(self.i_layers)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        form = QFormLayout()
        self.i_title = QLineEdit()
        self.i_display = QLineEdit()
        self.i_display.setPlaceholderText(tr('QGIS expression, e.g. "hrsz" (default: first search field)'))
        self.i_opacity = QDoubleSpinBox()
        self.i_opacity.setRange(0, 1)
        self.i_opacity.setSingleStep(0.1)
        self.i_legend = QCheckBox(tr("Show in the legend"))
        self.i_links = QCheckBox(tr("Feature links (deep links)"))
        self.i_label_always = QCheckBox(tr("Label every feature (smaller where the label does not fit)"))
        self.i_label_always.setToolTip(tr(
            "Web map: every feature of this layer gets its label (e.g. every parcel number). A label "
            "that does not fit its polygon is drawn smaller (down to half size), at worst at the "
            "roomiest point of the polygon; it is never left out."))
        self.i_snap = QCheckBox(tr("Measurements snap to this layer (corners and edges)"))
        self.i_snap.setToolTip(tr(
            "Web map: while measuring, points snap to the nearest corner of this layer's "
            "features, else to their nearest edge or line (as drawn on the map)."))
        form.addRow(tr("Title in the viewer"), self.i_title)
        form.addRow(tr("Feature title (display expression)"), self.i_display)
        form.addRow(tr("Initial opacity"), self.i_opacity)
        form.addRow("", self.i_legend)
        self.i_legend.toggled.connect(self._legend_box_toggled)
        form.addRow("", self.i_links)
        form.addRow("", self.i_label_always)
        form.addRow("", self.i_snap)
        right_layout.addLayout(form)
        self.i_fields = QTableWidget(0, 7)
        self.i_fields.setHorizontalHeaderLabels([tr("Field"), tr("Popup"), tr("Popup title"), tr("Type"),
                                                 tr("Search"), tr("Feature key"), tr("Filter")])
        self.i_fields.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.i_fields.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        fields_note = QLabel(tr("Only checked fields become public (popup, search, filter). "
                                "The feature key should be unique and never empty, e.g. a "
                                "parcel number; without one, feature links only work in the "
                                "same version of the map."))
        fields_note.setWordWrap(True)  # unwrapped, it made the tab ~2000 px wide (list squeezed)
        right_layout.addWidget(fields_note)
        right_layout.addWidget(self.i_fields, 1)
        self.i_stack = QStackedWidget()
        self.i_stack.addWidget(right)
        self.i_stack.addWidget(self._raster_page())
        split.addWidget(self.i_stack)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([300, 660])  # else the field table's width squeezes the list
        self.i_split = split
        layout.addWidget(split, 1)
        return widget

    def _raster_page(self):
        page = QWidget()
        form = QFormLayout(page)
        self.r_title = QLineEdit()
        self.r_format = QComboBox()
        self.r_format.addItem(tr("WebP (small, transparent; the default)"), "webp")
        self.r_format.addItem(tr("PNG (sharp, transparent; larger)"), "png")
        self.r_format.addItem(tr("JPEG (photos; no transparency)"), "jpeg")
        self.r_min = QSpinBox()
        self.r_max = QSpinBox()
        for box in (self.r_min, self.r_max):
            box.setRange(-1, 22)
            box.setSpecialValueText(tr("as the map"))
            box.valueChanged.connect(self._raster_estimate)
        self.r_quality = QSpinBox()
        self.r_quality.setRange(1, 100)
        self.r_hidpi = QCheckBox(tr("Sharp on high-resolution screens (512 px tiles, about 4× larger)"))
        self.r_opacity = QDoubleSpinBox()
        self.r_opacity.setRange(0, 1)
        self.r_opacity.setSingleStep(0.1)
        self.r_legend = QCheckBox(tr("Show in the legend"))
        self.r_legend.toggled.connect(self._legend_box_toggled)
        self.r_estimate = QLabel()
        self.r_estimate.setWordWrap(True)
        form.addRow(tr("Title in the viewer"), self.r_title)
        form.addRow(tr("Image format"), self.r_format)
        form.addRow(tr("Minimum zoom"), self.r_min)
        form.addRow(tr("Maximum zoom"), self.r_max)
        form.addRow(tr("Quality (JPEG/WebP)"), self.r_quality)
        form.addRow("", self.r_hidpi)
        form.addRow(tr("Initial opacity"), self.r_opacity)
        form.addRow("", self.r_legend)
        form.addRow("", self.r_estimate)
        form.addRow("", _note(tr("Raster layers are drawn by QGIS exactly as on the canvas into their own "
                                  "image tile archive (data/raster-….pmtiles). Vector layers always stay "
                                  "vector tiles. Above the maximum zoom the last images are enlarged.")))
        return page

    def _raster_estimate(self):
        if not self.current_layer_id or self.i_stack.currentIndex() != 1:
            return
        from ..publishing.raster_tiles import plan_layer  # pylint: disable=import-outside-toplevel
        layer = self.project.mapLayer(self.current_layer_id)
        config = LayerConfig(self.current_layer_id,
                             raster_min_zoom=self.r_min.value() if self.r_min.value() >= 0 else None,
                             raster_max_zoom=self.r_max.value() if self.r_max.value() >= 0 else None)
        profile = PublicationProfile()
        profile.view.min_zoom, profile.view.max_zoom = self.e_min_zoom.value(), self.e_max_zoom.value()
        try:
            plan = plan_layer(self.project, layer, config, profile, self._extent())
        except Exception:  # noqa: BLE001 - no extent yet
            self.r_estimate.setText("")
            return
        self.r_estimate.setText(tr("At most {} tiles at zooms {}–{} in the export extent.").format(
            plan.tiles, plan.min_zoom, plan.max_zoom) + ("\n⚠ " + "; ".join(plan.warnings) if plan.warnings else ""))

    def _basemap_tab(self):
        from qgis.PyQt.QtWidgets import QScrollArea  # pylint: disable=import-outside-toplevel
        page = QWidget()
        outer = QVBoxLayout(page)
        bundled = QGroupBox(tr("OpenStreetMap vector basemap (copied into the release)"))
        form = QFormLayout(bundled)
        outer.addWidget(bundled)
        self.b_kind = QComboBox()
        self.b_kind.addItem(tr("No basemap (only the project's layers)"), "none")
        self.b_kind.addItem(tr("OpenStreetMap vector basemap (Protomaps)"), "protomaps")
        self.b_latest = QRadioButton(tr("Download the area from the latest Protomaps daily build (internet "
                                        "needed while exporting)"))
        self.b_custom = QRadioButton(tr("Use this PMTiles file or URL (Protomaps schema):"))
        source_row = QHBoxLayout()
        self.b_source = QLineEdit()
        self.b_source.setPlaceholderText("https://…/planet.pmtiles  |  C:/maps/hungary.pmtiles")
        browse = QPushButton("…")
        browse.clicked.connect(self._choose_basemap_file)
        source_row.addWidget(self.b_source, 1)
        source_row.addWidget(browse)
        self.b_flavors = {}
        flavors_row = QHBoxLayout()
        titles = {"light": tr("Light"), "dark": tr("Dark"), "white": tr("White"), "grayscale": tr("Grayscale"),
                  "black": tr("Black")}
        for flavor in BASEMAP_FLAVORS:
            box = QCheckBox(titles[flavor])
            box.toggled.connect(self._refresh_basemap_initial)
            self.b_flavors[flavor] = box
            flavors_row.addWidget(box)
        flavors_row.addStretch(1)
        self.b_initial = QComboBox()
        self.b_max = QSpinBox()
        self.b_max.setRange(0, 15)
        self.b_padding = QSpinBox()
        self.b_padding.setRange(0, 1000)
        self.b_padding.setSuffix(" %")
        self.b_overview_zoom = QSpinBox()
        self.b_overview_zoom.setRange(0, 15)
        self.b_overview_km = QSpinBox()
        self.b_overview_km.setRange(0, 5000)
        self.b_overview_km.setSuffix(" km")
        form.addRow(tr("Basemap"), self.b_kind)
        form.addRow("", self.b_latest)
        form.addRow("", self.b_custom)
        form.addRow("", source_row)
        form.addRow(tr("Styles offered"), flavors_row)
        form.addRow(tr("Most detailed zoom"), self.b_max)
        form.addRow(tr("Area around the extent"), self.b_padding)
        form.addRow(tr("Overview zooms (0 to)"), self.b_overview_zoom)
        form.addRow(tr("Overview area"), self.b_overview_km)
        form.addRow("", _note(tr(
            "The basemap is vector tiles like the map: only the tiles of your area are copied into the "
            "release (data/basemap.pmtiles) with fonts generated for its labels, so visitors never load "
            "anything from another site. Visitors can switch styles or turn it off. Map data © "
            "OpenStreetMap contributors (ODbL), shown automatically in the attribution.")))
        # Web basemaps: QGIS's own XYZ connections (Browser -> XYZ Tiles),
        # each with "Use in web map"; loaded by the visitor's browser.
        web = QGroupBox(tr("Web basemaps (XYZ tiles, loaded from their server while browsing)"))
        web_layout = QVBoxLayout(web)
        self.b_xyz = QTableWidget(0, 6)
        self.b_xyz.setHorizontalHeaderLabels([tr("Use"), tr("Name"), tr("XYZ tile address"),
                                              tr("Attribution"), tr("Min z"), tr("Max z")])
        self.b_xyz.horizontalHeaderItem(0).setToolTip(tr("Offered in the web map's basemap menu"))
        self.b_xyz.horizontalHeaderItem(1).setToolTip(tr("The name of the QGIS XYZ connection"))
        header = self.b_xyz.horizontalHeader()
        header.setSectionResizeMode(0, header.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, header.ResizeMode.Stretch)
        for column, width in ((1, 190), (3, 170), (4, 55), (5, 55)):
            self.b_xyz.setColumnWidth(column, width)
        self.b_xyz.setMinimumHeight(170)
        self.b_xyz.setWordWrap(False)
        self.b_xyz.itemChanged.connect(self._refresh_basemap_initial)
        buttons = QHBoxLayout()
        add = QPushButton(tr("Add new…"))
        add.setToolTip(tr("A new XYZ connection: saved in QGIS too (Browser → XYZ Tiles)."))
        add.clicked.connect(lambda: self._add_xyz_row(XyzBasemap(max_zoom=19), used=True, editable_name=True))
        reload_button = QPushButton(tr("Reload from QGIS"))
        reload_button.setToolTip(tr("List the XYZ connections saved in QGIS again (added meanwhile in the Browser)."))
        reload_button.clicked.connect(lambda: self._fill_xyz_table(self._xyz_rows()))
        for button in (add, reload_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        web_layout.addWidget(self.b_xyz)
        web_layout.addLayout(buttons)
        web_layout.addWidget(_note(tr(
            "The list is QGIS's own XYZ tile connections (Browser → XYZ Tiles): tick the ones the web map "
            "offers. New ones and edited attributions are saved there too. Addresses use {z}, {x}, {y} "
            "({-y}: TMS rows, {s}: a/b/c servers) and must be https://. Visitors choose them in the map's "
            "basemap menu; their browser loads the tiles from that server while browsing (nothing is copied "
            "into the release, and the page allows exactly these servers). Use only addresses you are "
            "allowed to use, with the attribution the service requires. The ticked ones are also kept in "
            "the project and in the exported settings file.")))
        outer.addWidget(web)
        start = QFormLayout()
        start.addRow(tr("Basemap shown at start"), self.b_initial)
        outer.insertLayout(0, start)
        outer.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(page)
        return scroll

    def _add_xyz_row(self, entry, used=True, editable_name=True):
        """One web basemap row; the name of an existing QGIS connection is fixed
        (editing it would make another connection)."""
        self.b_xyz.blockSignals(True)
        row = self.b_xyz.rowCount()
        self.b_xyz.insertRow(row)
        use = QTableWidgetItem()
        https = str(entry.url).startswith("https://") or not entry.url
        flags = Qt.ItemFlag.ItemIsUserCheckable | (Qt.ItemFlag.ItemIsEnabled if https else Qt.ItemFlag.NoItemFlags)
        use.setFlags(flags)
        use.setCheckState(_check(used and https))
        if not https:
            use.setToolTip(tr("Web pages can only load https:// tiles."))
        self.b_xyz.setItem(row, 0, use)
        for column, value in enumerate((entry.title, entry.url, entry.attribution,
                                        str(entry.min_zoom), str(entry.max_zoom)), start=1):
            item = QTableWidgetItem(value)
            if column == 1 and not editable_name:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.b_xyz.setItem(row, column, item)
        self.b_xyz.blockSignals(False)
        self._refresh_basemap_initial()

    def _fill_xyz_table(self, used):
        """The web map's basemaps (ticked, in their order), then every other
        XYZ connection saved in QGIS (not ticked)."""
        try:
            from .xyz_connections import qgis_xyz_connections  # pylint: disable=import-outside-toplevel
            saved = qgis_xyz_connections()
        except Exception:  # noqa: BLE001 - settings are a convenience
            saved = []
        in_qgis = {entry.title for entry in saved}
        self.b_xyz.setRowCount(0)
        for entry in used:
            self._add_xyz_row(entry, used=True, editable_name=entry.title not in in_qgis)
        titles = {entry.title for entry in used}
        for entry in saved:
            if entry.title not in titles:
                self._add_xyz_row(entry, used=False, editable_name=False)

    def _xyz_rows(self, used_only=True):
        """The ticked web basemaps (or every row), in table order."""
        out = []
        for row in range(self.b_xyz.rowCount()):
            cell = lambda column: (self.b_xyz.item(row, column).text().strip()  # noqa: E731
                                   if self.b_xyz.item(row, column) else "")
            ticked = self.b_xyz.item(row, 0) is not None and self.b_xyz.item(row, 0).checkState() == CHECKED
            if not cell(2) or (used_only and not ticked):
                continue
            try:
                low, high = int(cell(4) or 0), int(cell(5) or 19)
            except ValueError:
                low, high = 0, 19
            out.append(XyzBasemap(title=cell(1) or f"XYZ {row + 1}", url=cell(2), attribution=cell(3),
                                  min_zoom=low, max_zoom=high))
        return out

    # ------------------------------------------------------------------ parcel report
    def _layer_combo(self, geometry=None, allow_empty=False, any_layer=False, empty_text=None):
        from qgis.gui import QgsMapLayerComboBox  # pylint: disable=import-outside-toplevel
        from qgis.core import QgsMapLayerProxyModel  # pylint: disable=import-outside-toplevel
        combo = QgsMapLayerComboBox()
        flags = "All" if any_layer else {"polygon": "PolygonLayer", "line": "LineLayer",
                                         None: "VectorLayer"}[geometry]
        try:
            combo.setFilters(getattr(QgsMapLayerProxyModel.Filter, flags))
        except AttributeError:
            combo.setFilters(getattr(Qgis.LayerFilter, flags))
        if empty_text is not None:
            try:
                combo.setAllowEmptyLayer(allow_empty, empty_text)
            except TypeError:  # QGIS < 3.20: no text for the empty entry
                combo.setAllowEmptyLayer(allow_empty)
        else:
            combo.setAllowEmptyLayer(allow_empty)
        combo.setProject(self.project)
        return combo

    @staticmethod
    def _field_combo(layer_combo, allow_empty=False):
        from qgis.gui import QgsFieldComboBox  # pylint: disable=import-outside-toplevel
        combo = QgsFieldComboBox()
        combo.setAllowEmptyFieldName(allow_empty)
        combo.setLayer(layer_combo.currentLayer())
        layer_combo.layerChanged.connect(combo.setLayer)
        return combo

    @staticmethod
    def _fields_table():
        table = QTableWidget(0, 2)
        table.setHorizontalHeaderLabels([tr("Field (tick to show)"), tr("Title")])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        table.verticalHeader().setVisible(False)
        table.setMinimumHeight(110)
        return table

    @staticmethod
    def _fill_fields_table(table, layer, chosen):
        chosen = {p.field: p for p in chosen}
        names = [f.name() for f in layer.fields()] if layer is not None else []
        table.setRowCount(len(names))
        for row, name in enumerate(names):
            item = QTableWidgetItem(name)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(_check(name in chosen))
            table.setItem(row, 0, item)
            table.setItem(row, 1, QTableWidgetItem(chosen[name].alias if name in chosen else ""))

    @staticmethod
    def _read_fields_table(table):
        out = []
        for row in range(table.rowCount()):
            if table.item(row, 0).checkState() == CHECKED:
                title = table.item(row, 1).text().strip() if table.item(row, 1) else ""
                out.append(PopupField(table.item(row, 0).text(), title))
        return out

    def _parcel_tab(self):
        from qgis.PyQt.QtWidgets import QScrollArea  # pylint: disable=import-outside-toplevel
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        widget = QWidget()
        scroll.setWidget(widget)
        layout = QVBoxLayout(widget)
        self.p_enabled = QCheckBox(tr("Clicking a parcel shows its report: area, parts cut by the zoning "
                                      "and the regulation lines, zones and restrictions"))
        layout.addWidget(self.p_enabled)
        top = QHBoxLayout()
        parcel_box = QGroupBox(tr("Parcels"))
        form = QFormLayout(parcel_box)
        self.p_layer = self._layer_combo("polygon")
        self.p_key = self._field_combo(self.p_layer)
        self.p_fields = self._fields_table()
        self.p_layer.layerChanged.connect(lambda layer: self._fill_fields_table(self.p_fields, layer, []))
        form.addRow(tr("Parcel layer"), self.p_layer)
        form.addRow(tr("Parcel id (unique, e.g. hrsz)"), self.p_key)
        form.addRow(tr("Parcel data shown"), self.p_fields)
        top.addWidget(parcel_box, 1)
        zoning_box = QGroupBox(tr("Zoning (parts of the parcel)"))
        form = QFormLayout(zoning_box)
        self.p_zoning = self._layer_combo("polygon")
        self.p_code = self._field_combo(self.p_zoning)
        self.p_zone_fields = self._fields_table()
        self.p_zoning.layerChanged.connect(lambda layer: self._fill_fields_table(self.p_zone_fields, layer, []))
        self.p_zoning.layerChanged.connect(self._guess_zone_code)
        self.p_cuts = QListWidget()
        self.p_cuts.setMaximumHeight(90)
        form.addRow(tr("Zone layer (its style gives the zone colours)"), self.p_zoning)
        form.addRow(tr("Zone code field"), self.p_code)
        form.addRow(tr("Zone values shown per part"), self.p_zone_fields)
        form.addRow(tr("Lines that cut parcels"), self.p_cuts)
        top.addWidget(zoning_box, 1)
        layout.addLayout(top)
        restrictions_box = QGroupBox(tr("Restrictions (shown with their legend symbol when they touch the parcel)"))
        rlayout = QVBoxLayout(restrictions_box)
        self.p_restrictions = QTableWidget(0, 6)
        self.p_restrictions.setHorizontalHeaderLabels([tr("Layer"), tr("Title"), tr("Name field"),
                                                       tr("Distance (m)"), tr("Explanation"), tr("Legal reference")])
        header = self.p_restrictions.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        for column in (1, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.p_restrictions.verticalHeader().setVisible(False)
        self.p_restrictions.setMinimumHeight(220)
        rlayout.addWidget(self.p_restrictions)
        rlayout.addWidget(_note(tr("Tick the layers that restrict building. Lines and points need a "
                                   "distance (protection zone); polygons count where they overlap. Only "
                                   "the title, explanation, reference and the chosen name field become "
                                   "public.")))
        layout.addWidget(restrictions_box)
        bottom = QHBoxLayout()
        regulation_box = QGroupBox(tr("Zone regulations table (optional, e.g. the local building code)"))
        form = QFormLayout(regulation_box)
        self.p_regulation = self._layer_combo(None, allow_empty=True)
        self.p_regulation_code = self._field_combo(self.p_regulation, allow_empty=True)
        self.p_regulation_fields = self._fields_table()
        self.p_regulation.layerChanged.connect(
            lambda layer: self._fill_fields_table(self.p_regulation_fields, layer, []))
        self.p_regulation.layerChanged.connect(self._guess_regulation_code)
        form.addRow(tr("Table"), self.p_regulation)
        form.addRow(tr("Zone code field"), self.p_regulation_code)
        form.addRow(tr("Fields shown"), self.p_regulation_fields)
        bottom.addWidget(regulation_box, 1)
        other_box = QGroupBox(tr("Accuracy and notice"))
        form = QFormLayout(other_box)
        self.p_min_area = QDoubleSpinBox()
        self.p_min_area.setRange(0, 1000)
        self.p_min_area.setSuffix(" m²")
        self.p_min_share = QDoubleSpinBox()
        self.p_min_share.setRange(0, 50)
        self.p_min_share.setSuffix(" %")
        self.p_disclaimer = QPlainTextEdit()
        self.p_disclaimer.setMaximumHeight(70)
        self.p_disclaimer.setPlaceholderText(tr("Empty: the viewer's standard notice ('Information only, not an "
                                                "official certificate…') in the viewer's language"))
        form.addRow(tr("Ignore parts smaller than"), self.p_min_area)
        form.addRow(tr("Ignore restriction overlaps below"), self.p_min_share)
        form.addRow(tr("Notice shown on every report"), self.p_disclaimer)
        bottom.addWidget(other_box, 1)
        layout.addLayout(bottom)
        return scroll

    def _guess_zone_code(self, layer):
        """Zone code field: the field QGIS styles / labels the zone layer by
        (e.g. szab_ov), unless one is chosen already - never a feature id
        (the report showed fid numbers instead of the zone codes)."""
        from ..publishing.parcel_report import guess_zone_field, looks_like_id  # pylint: disable=import-outside-toplevel
        if layer is None or not looks_like_id(layer, self.p_code.currentField()):
            return
        guessed = guess_zone_field(layer)
        if guessed:
            self.p_code.setField(guessed)

    def _guess_regulation_code(self, layer):
        """A regulations table chosen: pick its zone code field — the one named
        like the zone layer's code field (e.g. szab_ov), else a usual code name."""
        if layer is None or self.p_regulation_code.currentField():
            return
        names = {field.name().lower(): field.name() for field in layer.fields()}
        for wanted in (self.p_code.currentField(), "szab_ov", "ovezet", "övezet", "kod", "kód",
                       "code", "zone", "zone_code"):
            if wanted and wanted.lower() in names:
                self.p_regulation_code.setField(names[wanted.lower()])
                return

    def _fill_parcel_tab(self, profile):
        info = profile.parcel_info
        project_layer = self.project.mapLayer
        self.p_enabled.setChecked(info.enabled)
        for combo, layer_id in ((self.p_layer, info.parcel_layer_id), (self.p_zoning, info.zoning_layer_id),
                                (self.p_regulation, info.regulation_layer_id)):
            if layer_id and project_layer(layer_id) is not None:
                combo.setLayer(project_layer(layer_id))
            elif combo is self.p_regulation:
                combo.setLayer(None)
        self._fill_fields_table(self.p_fields, self.p_layer.currentLayer(), info.fields)
        self._fill_fields_table(self.p_zone_fields, self.p_zoning.currentLayer(), info.zoning_fields)
        self._fill_fields_table(self.p_regulation_fields, self.p_regulation.currentLayer(), info.regulation_fields)
        if info.key_field:
            self.p_key.setField(info.key_field)
        if info.zoning_code_field:
            self.p_code.setField(info.zoning_code_field)
        self._guess_zone_code(self.p_zoning.currentLayer())  # an id field saved before: the real code
        if info.regulation_code_field:
            self.p_regulation_code.setField(info.regulation_code_field)
        cuts = {c.layer_id: c for c in info.cut_lines}
        restrictions = {r.layer_id: r for r in info.restrictions}
        self.p_cuts.clear()
        self.p_restrictions.setRowCount(0)
        for node in self.project.layerTreeRoot().findLayers():
            layer = node.layer()
            if not isinstance(layer, QgsVectorLayer) or not layer.isSpatial():
                continue
            if layer.geometryType() == 1 or getattr(layer.geometryType(), "value", None) == 1:
                item = QListWidgetItem(layer.name())
                item.setData(LAYER_ROLE, layer.id())
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(_check(layer.id() in cuts))
                self.p_cuts.addItem(item)
            config = restrictions.get(layer.id())
            row = self.p_restrictions.rowCount()
            self.p_restrictions.insertRow(row)
            name = QTableWidgetItem(layer.name())
            name.setData(LAYER_ROLE, layer.id())
            name.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            name.setCheckState(_check(config is not None))
            self.p_restrictions.setItem(row, 0, name)
            self.p_restrictions.setItem(row, 1, QTableWidgetItem(config.title if config else layer.name()))
            fields = QComboBox()
            fields.addItem("", "")
            for field in layer.fields():
                fields.addItem(field.name(), field.name())
            fields.setCurrentIndex(max(0, fields.findData(config.name_field if config else "")))
            self.p_restrictions.setCellWidget(row, 2, fields)
            distance = QDoubleSpinBox()
            distance.setRange(0, 1000)
            distance.setValue(config.buffer_m if config else 0)
            self.p_restrictions.setCellWidget(row, 3, distance)
            self.p_restrictions.setItem(row, 4, QTableWidgetItem(config.note if config else ""))
            self.p_restrictions.setItem(row, 5, QTableWidgetItem(config.reference if config else ""))
        self.p_min_area.setValue(info.min_area)
        self.p_min_share.setValue(info.min_share)
        self.p_disclaimer.setPlainText(info.disclaimer)

    def _collect_parcel_tab(self, profile):
        info = profile.parcel_info
        info.enabled = self.p_enabled.isChecked()
        info.parcel_layer_id = self.p_layer.currentLayer().id() if self.p_layer.currentLayer() else ""
        info.key_field = self.p_key.currentField()
        info.fields = self._read_fields_table(self.p_fields)
        info.zoning_layer_id = self.p_zoning.currentLayer().id() if self.p_zoning.currentLayer() else ""
        info.zoning_code_field = self.p_code.currentField()
        info.zoning_fields = self._read_fields_table(self.p_zone_fields)
        regulation = self.p_regulation.currentLayer()
        info.regulation_layer_id = regulation.id() if regulation else ""
        info.regulation_code_field = self.p_regulation_code.currentField() if regulation else ""
        info.regulation_fields = self._read_fields_table(self.p_regulation_fields) if regulation else []
        info.cut_lines = [CutLineConfig(self.p_cuts.item(i).data(LAYER_ROLE), self.p_cuts.item(i).text())
                          for i in range(self.p_cuts.count()) if self.p_cuts.item(i).checkState() == CHECKED]
        restrictions = []
        for row in range(self.p_restrictions.rowCount()):
            item = self.p_restrictions.item(row, 0)
            if item.checkState() != CHECKED:
                continue
            restrictions.append(RestrictionConfig(
                layer_id=item.data(LAYER_ROLE), title=self.p_restrictions.item(row, 1).text().strip(),
                name_field=self.p_restrictions.cellWidget(row, 2).currentData() or "",
                buffer_m=float(self.p_restrictions.cellWidget(row, 3).value()),
                note=self.p_restrictions.item(row, 4).text().strip(),
                reference=self.p_restrictions.item(row, 5).text().strip()))
        info.restrictions = restrictions
        info.min_area = float(self.p_min_area.value())
        info.min_share = float(self.p_min_share.value())
        info.disclaimer = self.p_disclaimer.toPlainText().strip()

    def _choose_basemap_file(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Basemap"), "", "PMTiles (*.pmtiles)")
        if path:
            self.b_source.setText(path)
            self.b_custom.setChecked(True)

    def _refresh_basemap_initial(self, *_):
        current = self.b_initial.currentData()
        self.b_initial.blockSignals(True)
        self.b_initial.clear()
        self.b_initial.addItem(tr("None (switched off)"), "none")
        for flavor, box in self.b_flavors.items():
            if box.isChecked():
                self.b_initial.addItem(box.text(), flavor)
        for number, entry in enumerate(self._xyz_rows() if hasattr(self, "b_xyz") else [], start=1):
            self.b_initial.addItem(f"{tr('Web')}: {entry.title}", f"xyz-{number}")
        index = self.b_initial.findData(current) if current else -1
        self.b_initial.setCurrentIndex(index if index >= 0 else min(1, self.b_initial.count() - 1))
        self.b_initial.blockSignals(False)

    def _output_tab(self):
        widget = QWidget()
        form = QFormLayout(widget)
        self.o_archive = QComboBox()
        self.o_archive.addItem(tr("PMTiles (vector tiles for static web hosting)"), "pmtiles")
        self.o_archive.addItem(tr("PMTiles + MBTiles copy"), "both")
        self.o_archive.addItem(tr("MBTiles only (desktop / preview; not for direct web hosting)"), "mbtiles")
        dir_row = QHBoxLayout()
        self.o_dir = QLineEdit()
        dir_button = QPushButton("…")
        dir_button.clicked.connect(self._choose_dir)
        dir_row.addWidget(self.o_dir)
        dir_row.addWidget(dir_button)
        self.o_xyz = QCheckBox(tr("Also write the legacy XYZ web package (web/)"))
        self.o_zip = QCheckBox(tr("Also write an offline ZIP of the release"))
        self.o_cpu = QSpinBox()
        self.o_cpu.setRange(1, 100)
        self.o_fidelity = QComboBox()
        self.o_fidelity.addItem(tr("Vector-first: approximate where declared and report"), 0)
        self.o_fidelity.addItem(tr("Strict: fail if any component is unsupported or approximated"), 1)
        self.o_overzoom = QComboBox()
        self.o_overzoom.addItem(tr("Keep showing the last tiles beyond the maximum zoom"), 0)
        self.o_overzoom.addItem(tr("Hide layers beyond the maximum zoom"), 1)
        self.o_labels = QComboBox()
        self.o_labels.addItem(tr("Whole polygon"), 0)
        self.o_labels.addItem(tr("Visible polygon"), 1)
        self.o_labels.addItem(tr("As set in each layer's labels"), 2)
        self.o_all_fields = QCheckBox(tr("Publish ALL attribute fields in the tiles (not recommended)"))
        cache_row = QHBoxLayout()
        self.o_reuse = QCheckBox(tr("Reuse unchanged layers from earlier exports (faster)"))
        self.o_reuse.setToolTip(tr("Layers whose data files, style and export settings did not change "
                                   "since an earlier export in this output folder are not processed "
                                   "again. Database and web layers are always exported."))
        clear_cache = QPushButton(tr("Clear cache…"))
        clear_cache.clicked.connect(self.clear_cache)
        cache_row.addWidget(self.o_reuse, 1)
        cache_row.addWidget(clear_cache)
        form.addRow(tr("Archive"), self.o_archive)
        form.addRow(tr("Local output folder"), dir_row)
        form.addRow("", self.o_xyz)
        form.addRow("", self.o_zip)
        form.addRow(tr("CPU limit (%)"), self.o_cpu)
        form.addRow(tr("Fidelity"), self.o_fidelity)
        form.addRow(tr("Beyond the maximum zoom"), self.o_overzoom)
        form.addRow(tr("Polygon labels"), self.o_labels)
        form.addRow("", self.o_all_fields)
        form.addRow("", cache_row)
        form.addRow("", _note(tr("Vector layers are always published as vector tiles (MVT) in a "
                                  "PMTiles archive, never as images. Only QGIS raster layers become image "
                                  "tiles, each in its own archive. Sprites, patterns, legend swatches and "
                                  "fonts are styling assets.")))
        return widget

    def clear_cache(self):
        """Delete the export cache of the local output folder."""
        from ..core.export_cache import ExportCache  # pylint: disable=import-outside-toplevel
        from ..publishing.controller import cache_dir  # pylint: disable=import-outside-toplevel
        try:
            cache = ExportCache(cache_dir(self.collect()))
        except PublishingError as error:
            self._fail(error.code, error.message, error.detail)
            return
        size = cache.size()
        if not size:
            QMessageBox.information(self, tr("Cache"), tr("The cache of this output folder is empty."))
            return
        answer = QMessageBox.question(
            self, tr("Cache"), tr("Delete the export cache ({:.0f} MB)? The next export processes "
                                  "every layer again.").format(size / 1024 / 1024))
        if answer == QMessageBox.StandardButton.Yes:
            cache.clear()
            self.status.setText(tr("Export cache deleted."))

    def _choose_dir(self):
        path = QFileDialog.getExistingDirectory(self, tr("Local output folder"), self.o_dir.text())
        if path:
            self.o_dir.setText(path)

    def _destination_tab(self):
        from .r2_guide import FIELD_HELP  # pylint: disable=import-outside-toplevel
        widget = QWidget()
        form = QFormLayout(widget)
        self.d_form = form
        self.d_kind = QComboBox()
        self.d_kind.addItem(tr("Local only (no upload)"), "local")
        self.d_kind.addItem(tr("Cloudflare R2"), "r2")
        self.d_kind.addItem(tr("Other S3-compatible storage"), "s3")
        self.d_account = QLineEdit()
        self.d_account.setPlaceholderText(tr("32 characters – or paste the S3 API URL / dashboard address"))
        self.d_endpoint = QLineEdit()
        self.d_endpoint.setPlaceholderText(tr("R2: leave empty (derived from the account id)"))
        self.d_bucket = QLineEdit()
        self.d_bucket.setPlaceholderText("maps")
        self.d_prefix = QLineEdit()
        self.d_prefix.setPlaceholderText("maps/<slug>")
        self.d_public = QLineEdit()
        self.d_public.setPlaceholderText("https://maps.example.com")
        for field, key in ((self.d_account, "account"), (self.d_endpoint, "endpoint"),
                           (self.d_bucket, "bucket"), (self.d_prefix, "prefix"), (self.d_public, "public")):
            field.setToolTip(tr(FIELD_HELP[key]))
        # Pasted Cloudflare addresses: take the account id (and bucket) from them.
        self.d_account.editingFinished.connect(lambda: self._parse_pasted(self.d_account))
        self.d_endpoint.editingFinished.connect(lambda: self._parse_pasted(self.d_endpoint))
        self.d_bucket.editingFinished.connect(lambda: self._parse_pasted(self.d_bucket))
        self.d_address = QLabel()
        self.d_address.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for field in (self.d_public, self.d_prefix, getattr(self, "e_slug", None)):
            if field is not None:
                field.textChanged.connect(self._update_address)
        self.d_retention = QSpinBox()
        self.d_retention.setRange(1, 100)
        self.d_conditional = QCheckBox(tr("Storage supports conditional writes (R2, AWS S3)"))
        auth_row = QHBoxLayout()
        try:
            from qgis.gui import QgsAuthConfigSelect  # pylint: disable=import-outside-toplevel
            self.d_auth = QgsAuthConfigSelect(widget)
        except ImportError:
            self.d_auth = None
        if self.d_auth is not None:
            auth_row.addWidget(self.d_auth)
            self.d_auth.setToolTip(tr(FIELD_HELP["auth"]))
        help_row = QHBoxLayout()
        guide = QPushButton(tr("Step-by-step: set up Cloudflare R2…"))
        guide.setToolTip(tr("Where to find each value in the Cloudflare dashboard"))
        guide.clicked.connect(self.show_r2_guide)
        help_row.addWidget(guide)
        help_row.addStretch(1)
        self.d_help_row = form.rowCount()
        form.addRow("", help_row)
        form.addRow(tr("Destination"), self.d_kind)
        self.d_rows = {}
        form.addRow(tr("Cloudflare account id"), self.d_account)
        self.d_rows["account"] = self.d_account
        form.addRow(tr("S3 API endpoint"), self.d_endpoint)
        self.d_rows["endpoint"] = self.d_endpoint
        form.addRow(tr("Bucket"), self.d_bucket)
        form.addRow(tr("Prefix in the bucket"), self.d_prefix)
        form.addRow(tr("Public base URL (custom domain)"), self.d_public)
        form.addRow(tr("Map address"), self.d_address)
        form.addRow(tr("Saved credentials (QGIS authentication: user = access key id, "
                       "password = secret)"), auth_row)
        session = QHBoxLayout()
        self.d_session_key = QLineEdit()
        self.d_session_key.setPlaceholderText(tr("Access Key ID"))
        self.d_session_key.setToolTip(tr(FIELD_HELP["session"]))
        self.d_session_secret = QLineEdit()
        self.d_session_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.d_session_secret.setPlaceholderText(tr("Secret Access Key"))
        self.d_session_secret.setToolTip(tr(FIELD_HELP["session"]))
        save_keys = QPushButton(tr("Save keys in QGIS…"))
        save_keys.setToolTip(tr("Store these keys encrypted in QGIS and select them above"))
        save_keys.clicked.connect(self.save_keys)
        session.addWidget(self.d_session_key)
        session.addWidget(self.d_session_secret)
        session.addWidget(save_keys)
        form.addRow(tr("Or keys for this session only"), session)
        form.addRow(tr("Releases to keep"), self.d_retention)
        form.addRow("", self.d_conditional)
        self.d_kind.currentIndexChanged.connect(self._destination_kind_changed)
        buttons = QHBoxLayout()
        test = QPushButton(tr("Test connection"))
        test.clicked.connect(self.test_connection)
        cors = QPushButton(tr("Show CORS policy"))
        cors.clicked.connect(self.show_cors)
        history = QPushButton(tr("Releases and rollback…"))
        history.clicked.connect(self.show_history)
        buttons.addWidget(test)
        buttons.addWidget(cors)
        buttons.addWidget(history)
        buttons.addStretch(1)
        form.addRow("", buttons)
        form.addRow("", _note(tr("Use bucket-scoped API tokens. The S3 API endpoint is not the public "
                                  "address: connect a custom domain to the bucket (r2.dev is rate "
                                  "limited and meant for development). Nothing is created, made "
                                  "public or deleted in your account automatically.")))
        return widget

    def _review_tab(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.review = QTextBrowser()
        self.review.setOpenLinks(False)
        layout.addWidget(self.review)
        row = QHBoxLayout()
        refresh = QPushButton(tr("Refresh review"))
        refresh.clicked.connect(self.refresh_review)
        self.approve = QCheckBox(tr("I understand that the geometry of the selected layers and the "
                                    "fields listed above become public at the target address."))
        row.addWidget(refresh)
        row.addWidget(self.approve, 1)
        layout.addLayout(row)
        return widget

    # ------------------------------------------------------------------ populate / collect
    def _populate(self, profile: PublicationProfile):
        self.e_title.setText(profile.title)
        self.e_slug.setText(profile.slug)
        self.e_description.setPlainText(profile.description)
        self.e_locale.setCurrentIndex(max(0, self.e_locale.findData(profile.locale)))
        self.e_attribution.setText(profile.attribution)
        self.e_logo.setText(profile.logo_path)
        self.e_min_zoom.setValue(profile.view.min_zoom)
        self.e_max_zoom.setValue(profile.view.max_zoom)
        self.e_max_view.setValue(profile.view.max_view_zoom)
        self.e_extent_layer.blockSignals(True)
        self.e_extent_layer.setLayer(self.project.mapLayer(profile.view.extent_layer)
                                     if profile.view.extent_layer else None)
        self.e_extent_layer.blockSignals(False)
        if profile.view.extent_layer and self.e_extent_layer.currentLayer() is None:
            profile.view.extent_layer = ""  # the layer is gone: keep its last extent, fixed
        self._current_extent_3857()
        self._show_extent()
        self._fill_tree(profile)  # extent label refreshed by _use_canvas_extent
        if hasattr(self.e_accent, "setColor"):
            from qgis.PyQt.QtGui import QColor  # pylint: disable=import-outside-toplevel
            self.e_accent.setColor(QColor(profile.accent_color))
        else:
            self.e_accent.setText(profile.accent_color)
        themes = self.project.mapThemeCollection().mapThemes()
        self.theme_pick.clear()
        self.theme_pick.addItems(themes)
        self.themes_list.blockSignals(True)
        self.themes_list.clear()
        for name in themes:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(_check(name in profile.themes.names))
            self.themes_list.addItem(item)
        self.themes_list.blockSignals(False)
        self._refresh_initial_theme()
        self.theme_initial.setCurrentIndex(max(0, self.theme_initial.findData(profile.themes.initial)))
        self._fill_parcel_tab(profile)
        basemap = profile.basemap
        self.b_kind.setCurrentIndex(max(0, self.b_kind.findData(basemap.kind)))
        (self.b_custom if basemap.source else self.b_latest).setChecked(True)
        self.b_source.setText(basemap.source)
        for flavor, box in self.b_flavors.items():
            box.setChecked(flavor in basemap.flavors)
        self._fill_xyz_table(basemap.xyz)
        self._refresh_basemap_initial()
        self.b_initial.setCurrentIndex(max(0, self.b_initial.findData(basemap.initial)))
        self.b_max.setValue(basemap.max_zoom)
        self.b_padding.setValue(int(round(basemap.padding * 100)))
        self.b_overview_zoom.setValue(basemap.overview_zoom)
        self.b_overview_km.setValue(int(basemap.overview_km))
        for key, box in self.i_flags.items():
            box.setChecked(bool(getattr(profile.interaction, key)))
        self.i_google_key.setText(profile.interaction.google_api_key)
        self.i_google_key.setEnabled(profile.interaction.street_view)
        self.layer_configs = {c.layer_id: c for c in profile.layers}
        self.o_archive.setCurrentIndex(max(0, self.o_archive.findData(profile.output.archive)))
        self.o_dir.setText(profile.output.local_directory)
        self.o_xyz.setChecked(profile.output.xyz_package)
        self.o_zip.setChecked(profile.output.zip)
        self.o_cpu.setValue(max(1, profile.output.cpu_percent))
        self.o_fidelity.setCurrentIndex(max(0, self.o_fidelity.findData(profile.output.fidelity_mode)))
        self.o_overzoom.setCurrentIndex(max(0, self.o_overzoom.findData(profile.output.overzoom)))
        self.o_labels.setCurrentIndex(max(0, self.o_labels.findData(profile.output.polygon_labels_base)))
        self.o_all_fields.setChecked(profile.output.include_all_fields)
        self.o_reuse.setChecked(profile.output.reuse_unchanged)
        dest = profile.destination
        self.d_kind.setCurrentIndex(max(0, self.d_kind.findData(dest.kind)))
        self.d_account.setText(dest.account_id)
        self.d_endpoint.setText(dest.endpoint)
        self.d_bucket.setText(dest.bucket)
        self.d_prefix.setText(dest.prefix)
        self.d_public.setText(dest.public_base_url)
        self.d_retention.setValue(dest.retention)
        self.d_conditional.setChecked(dest.conditional_writes)
        if self.d_auth is not None and dest.credential_ref:
            self.d_auth.setConfigId(dest.credential_ref)
        self._destination_kind_changed()
        self._update_address()

    def _tab_changed(self, index):
        if self.tabs.widget(index) is not None and self.tabs.tabText(index) == tr("Interaction"):
            self._fill_interaction_layers()
        if self.tabs.tabText(index) == tr("Review"):
            self.refresh_review()

    def _included_ids(self):
        return [item.data(0, LAYER_ROLE) for item in self._tree_items()
                if item.checkState(COL_PUBLISH) == CHECKED]

    def _fill_interaction_layers(self):
        self._store_layer_fields()
        self.i_layers.blockSignals(True)
        self.i_layers.clear()
        for layer_id in self._included_ids():
            layer = self.project.mapLayer(layer_id)
            item = QListWidgetItem(layer.name())
            item.setData(Qt.ItemDataRole.UserRole, layer_id)
            item.setToolTip(layer.name())
            try:
                item.setIcon(QgsIconUtils.iconForLayer(layer))
            except (AttributeError, TypeError):
                pass
            self.i_layers.addItem(item)
        self.i_layers.blockSignals(False)
        self.current_layer_id = None
        if self.i_layers.count():
            self.i_layers.setCurrentRow(0)

    def _interaction_layer_changed(self, current, previous):
        self._store_layer_fields()
        self.current_layer_id = current.data(Qt.ItemDataRole.UserRole) if current else None
        if not self.current_layer_id:
            return
        layer = self.project.mapLayer(self.current_layer_id)
        config = self.layer_configs.setdefault(self.current_layer_id, LayerConfig(self.current_layer_id))
        if isinstance(layer, QgsRasterLayer):
            self.i_stack.setCurrentIndex(1)
            self.r_title.setText(config.title)
            self.r_format.setCurrentIndex(max(0, self.r_format.findData(config.raster_format)))
            self.r_min.setValue(-1 if config.raster_min_zoom is None else config.raster_min_zoom)
            self.r_max.setValue(-1 if config.raster_max_zoom is None else config.raster_max_zoom)
            self.r_quality.setValue(config.raster_quality)
            self.r_hidpi.setChecked(config.raster_hidpi)
            self.r_opacity.setValue(config.opacity)
            self.r_legend.setChecked(config.legend)
            self._raster_estimate()
            return
        self.i_stack.setCurrentIndex(0)
        self.i_title.setText(config.title)
        self.i_display.setText(config.display_expression)
        self.i_opacity.setValue(config.opacity)
        self.i_legend.setChecked(config.legend)
        self.i_label_always.setChecked(config.label_always)
        self.i_snap.setChecked(config.snap)
        self.i_links.setChecked(config.deep_links)
        popups = {p.field: p for p in config.popup_fields}
        filters = {f.field: f for f in config.filter_fields}
        fields = layer.fields()
        self.i_fields.setRowCount(fields.count())
        for row in range(fields.count()):
            name = fields.at(row).name()
            item = QTableWidgetItem(name)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.i_fields.setItem(row, 0, item)
            for column, value in ((1, name in popups), (4, name in config.search_fields),
                                  (5, name in config.key_fields)):
                box = QTableWidgetItem()
                box.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                box.setCheckState(_check(value))
                self.i_fields.setItem(row, column, box)
            self.i_fields.setItem(row, 2, QTableWidgetItem(popups[name].alias if name in popups else ""))
            kind = QComboBox()
            for value in FIELD_TYPES:
                kind.addItem(value, value)
            guess = popups[name].type if name in popups else self._guess_type(fields.at(row))
            kind.setCurrentIndex(max(0, kind.findData(guess)))
            self.i_fields.setCellWidget(row, 3, kind)
            flt = QComboBox()
            flt.addItem(tr("no filter"), "")
            for value, label in (("values", tr("values")), ("range", tr("range")), ("text", tr("text"))):
                flt.addItem(label, value)
            flt.setCurrentIndex(max(0, flt.findData(filters[name].kind if name in filters else "")))
            self.i_fields.setCellWidget(row, 6, flt)

    @staticmethod
    def _guess_type(field) -> str:
        name = field.typeName().lower()
        if field.isNumeric():
            return "integer" if "int" in name else "number"
        if "bool" in name:
            return "boolean"
        if "date" in name or "time" in name:
            return "date"
        return "string"

    def _store_layer_fields(self):
        if not self.current_layer_id:
            return
        config = self.layer_configs.setdefault(self.current_layer_id, LayerConfig(self.current_layer_id))
        if isinstance(self.project.mapLayer(self.current_layer_id), QgsRasterLayer):
            config.title = self.r_title.text().strip()
            config.raster_format = self.r_format.currentData()
            config.raster_min_zoom = self.r_min.value() if self.r_min.value() >= 0 else None
            config.raster_max_zoom = self.r_max.value() if self.r_max.value() >= 0 else None
            config.raster_quality = self.r_quality.value()
            config.raster_hidpi = self.r_hidpi.isChecked()
            config.opacity = float(self.r_opacity.value())
            config.legend = self.r_legend.isChecked()
            return
        config.title = self.i_title.text().strip()
        config.display_expression = self.i_display.text().strip()
        config.opacity = float(self.i_opacity.value())
        config.legend = self.i_legend.isChecked()
        config.label_always = self.i_label_always.isChecked()
        config.snap = self.i_snap.isChecked()
        config.deep_links = self.i_links.isChecked()
        popups, search, keys, filters = [], [], [], []
        for row in range(self.i_fields.rowCount()):
            name = self.i_fields.item(row, 0).text()
            if self.i_fields.item(row, 1).checkState() == CHECKED:
                popups.append(PopupField(name, self.i_fields.item(row, 2).text().strip(),
                                         self.i_fields.cellWidget(row, 3).currentData()))
            if self.i_fields.item(row, 4).checkState() == CHECKED:
                search.append(name)
            if self.i_fields.item(row, 5).checkState() == CHECKED:
                keys.append(name)
            kind = self.i_fields.cellWidget(row, 6).currentData()
            if kind:
                filters.append(FilterField(name, kind))
        config.popup_fields, config.search_fields = popups, search
        config.key_fields, config.filter_fields = keys, filters

    def collect(self) -> PublicationProfile:
        self._store_layer_fields()
        profile = self.profile
        profile.title = self.e_title.text().strip()
        profile.slug = self.e_slug.text().strip() or slugify(profile.title)
        profile.description = self.e_description.toPlainText().strip()
        profile.locale = self.e_locale.currentData()
        profile.attribution = self.e_attribution.text().strip()
        profile.logo_path = self.e_logo.text().strip()
        profile.view.min_zoom = self.e_min_zoom.value()
        profile.view.max_zoom = self.e_max_zoom.value()
        profile.view.max_view_zoom = max(self.e_max_view.value(), profile.view.max_zoom)
        layers = []
        for item in self._tree_items():
            layer_id = item.data(0, LAYER_ROLE)
            config = self.layer_configs.get(layer_id) or LayerConfig(layer_id)
            config.included = item.checkState(COL_PUBLISH) == CHECKED
            config.initially_visible = config.included and item.checkState(COL_VISIBLE) == CHECKED
            config.toggleable = not config.included or item.checkState(COL_TOGGLE) == CHECKED
            if config.included and not config.toggleable:
                config.initially_visible = True
            config.min_scale, config.max_scale = item.data(COL_SCALES, SCALES_ROLE) or (0.0, 0.0)
            config.legend = item.checkState(COL_LEGEND) == CHECKED
            layers.append(config)
        profile.layers = layers
        groups = []
        for item in self._group_items():
            toggleable = item.checkState(COL_TOGGLE) == CHECKED
            visible = item.checkState(COL_VISIBLE) == CHECKED or not toggleable
            if not toggleable or not visible:  # only groups that differ from the defaults
                groups.append(GroupConfig(list(item.data(0, GROUP_ROLE)), toggleable=toggleable,
                                          initially_visible=visible))
        profile.groups = groups
        names = [self.themes_list.item(i).text() for i in range(self.themes_list.count())
                 if self.themes_list.item(i).checkState() == CHECKED]
        profile.themes.names = names
        profile.themes.initial = self.theme_initial.currentData() if self.theme_initial.currentData() in names else ""
        if hasattr(self.e_accent, "color"):
            profile.accent_color = self.e_accent.color().name()
        else:
            profile.accent_color = self.e_accent.text().strip() or "#2563eb"
        self._collect_parcel_tab(profile)
        basemap = profile.basemap
        basemap.kind = self.b_kind.currentData()
        basemap.source = self.b_source.text().strip() if self.b_custom.isChecked() else ""
        basemap.flavors = [f for f, box in self.b_flavors.items() if box.isChecked()] or ["light"]
        basemap.xyz = self._xyz_rows()
        basemap.initial = self.b_initial.currentData() or "none"
        xyz_ids = [f"xyz-{n}" for n in range(1, len(basemap.xyz) + 1)]
        if basemap.initial != "none" and basemap.initial not in basemap.flavors + xyz_ids:
            basemap.initial = basemap.flavors[0]
        try:  # the same web basemaps as QGIS XYZ connections
            from .xyz_connections import save_qgis_xyz_connections  # pylint: disable=import-outside-toplevel
            save_qgis_xyz_connections(self._xyz_rows(used_only=False))
        except Exception:  # noqa: BLE001 - settings are a convenience; never block the window
            pass
        basemap.max_zoom = self.b_max.value()
        basemap.padding = self.b_padding.value() / 100.0
        basemap.overview_zoom = min(self.b_overview_zoom.value(), basemap.max_zoom)
        basemap.overview_km = float(self.b_overview_km.value())
        for key, box in self.i_flags.items():
            setattr(profile.interaction, key, box.isChecked())
        profile.interaction.google_api_key = self.i_google_key.text().strip()
        out = profile.output
        out.archive = self.o_archive.currentData()
        out.local_directory = self.o_dir.text().strip()
        out.xyz_package, out.zip = self.o_xyz.isChecked(), self.o_zip.isChecked()
        out.cpu_percent = self.o_cpu.value()
        out.fidelity_mode = self.o_fidelity.currentData()
        out.overzoom = self.o_overzoom.currentData()
        out.polygon_labels_base = self.o_labels.currentData()
        out.include_all_fields = self.o_all_fields.isChecked()
        out.reuse_unchanged = self.o_reuse.isChecked()
        dest = profile.destination
        dest.kind = self.d_kind.currentData()
        dest.account_id = self.d_account.text().strip()
        dest.endpoint = self.d_endpoint.text().strip()
        dest.bucket = self.d_bucket.text().strip()
        dest.prefix = self.d_prefix.text().strip()
        dest.public_base_url = self.d_public.text().strip().rstrip("/")
        dest.retention = self.d_retention.value()
        dest.conditional_writes = self.d_conditional.isChecked()
        dest.credential_ref = self.d_auth.configId() if self.d_auth is not None else ""
        return profile

    # ------------------------------------------------------------------ actions
    def apply_preset(self, module):
        """Fill the settings from ``module`` (a publishing.presets module)."""
        profile = self.collect()
        notes = module.apply(self.project, profile)
        self.profile = profile
        self._populate(profile)
        for note in notes:
            self.log(note)
        self.status.setText(tr("Preset applied: {}. Review the settings before publishing.").format(module.TITLE))
        if notes:
            QMessageBox.information(self, module.TITLE, "\n".join(notes[:30]))

    def log(self, text: str):
        self.logbox.appendPlainText(str(text))
        QgsMessageLog.logMessage(str(text), TAG, Qgis.MessageLevel.Info)

    def save_settings(self, quiet=False) -> bool:
        profile = self.collect()
        problems = validate(profile)
        if problems and not quiet:
            QMessageBox.warning(self, tr("Settings"), "\n".join(problems[:12]))
            return False
        store.save_profile(self.project, profile)
        self.status.setText(tr("Settings saved in the project (save the project file to keep them)."))
        return True

    def export_settings_file(self, path=None) -> bool:
        if not path:
            start = os.path.join(os.path.dirname(self.project.fileName()) or os.path.expanduser("~"),
                                 f"{self.e_slug.text().strip() or 'web-map'}.q2vt.json")
            path, _ = QFileDialog.getSaveFileName(self, tr("Export settings"), start,
                                                  tr("Publication settings (*.q2vt.json *.json)"))
        if not path:
            return False
        profile = self.collect()
        keys = self._exportable_credentials(profile)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(store.export_document(profile, self.project, keys))
        except OSError as error:
            QMessageBox.warning(self, tr("Export settings"), str(error))
            return False
        self.status.setText(
            tr("Settings exported to {} with the object storage keys (secret included: keep the file "
               "private).").format(path) if keys else
            tr("Settings exported to {} (no object storage keys).").format(path))
        return True

    def _exportable_credentials(self, profile):
        """The destination's keys for the settings file (the owner wants them
        there): the pasted session keys, else the saved QGIS configuration."""
        if profile.destination.kind == "local":
            return None
        from ..publishing.providers.base import Credentials  # pylint: disable=import-outside-toplevel
        key, secret = self.d_session_key.text().strip(), self.d_session_secret.text()
        if key and secret:
            return Credentials(key, secret)
        if not profile.destination.credential_ref:
            return None
        try:
            from ..publishing.credentials import from_auth_config  # pylint: disable=import-outside-toplevel
            return from_auth_config(profile.destination.credential_ref)
        except PublishingError as error:
            self.log(tr("The keys were not exported: {}").format(error.message))
            return None

    def import_settings_file(self, path=None, same_map=None) -> bool:
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, tr("Import settings"),
                                                  os.path.dirname(self.project.fileName()) or "",
                                                  tr("Publication settings (*.q2vt.json *.json)"))
        if not path:
            return False
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            profile, notes = store.import_document(text, self.project)
            keys = store.document_credentials(text)
        except (OSError, ValueError, KeyError, PublishingError) as error:
            QMessageBox.warning(self, tr("Import settings"),
                                tr("Not a publication settings file:\n{}").format(error))
            return False
        if same_map is None:
            answer = QMessageBox.question(
                self, tr("Import settings"),
                tr("Yes: keep publishing the same web map as the file (same address).\n"
                   "No: a new, separate web map with these settings."),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            same_map = answer == QMessageBox.StandardButton.Yes
        if not same_map:
            profile = store.as_new_publication(profile)
        known = {config.layer_id for config in profile.layers}
        for node in self.project.layerTreeRoot().findLayers():
            layer = node.layer()
            if publishable(layer) and layer.id() not in known:
                profile.layers.append(LayerConfig(layer.id(), included=False, initially_visible=False))
        if keys is not None:
            profile.destination.credential_ref = ""  # an id of the other computer's QGIS
        self.profile = profile
        self._populate(profile)
        for note in notes:
            self.log(note)
        if keys is not None:
            self._import_credentials(keys)
        if profile.basemap.xyz:  # web basemaps: also QGIS XYZ connections at once
            try:
                from .xyz_connections import save_qgis_xyz_connections  # pylint: disable=import-outside-toplevel
                save_qgis_xyz_connections(profile.basemap.xyz)
                self.log(tr("{} web basemap(s) imported (also as QGIS XYZ connections).").format(
                    len(profile.basemap.xyz)))
            except Exception:  # noqa: BLE001 - settings are a convenience
                pass
        self.status.setText(tr("Settings imported from {}. Check them, then Save settings to keep them "
                               "in the project.").format(os.path.basename(path)))
        return True

    def _import_credentials(self, keys):
        """Keys from a settings file: usable at once (session keys), and saved
        encrypted in QGIS when possible (kept after a restart)."""
        self.d_session_key.setText(keys.access_key_id)
        self.d_session_secret.setText(keys.secret_access_key)
        from ..publishing.credentials import store_auth_config  # pylint: disable=import-outside-toplevel
        bucket = self.d_bucket.text().strip() or "maps"
        name = f"{'R2' if self.d_kind.currentData() == 'r2' else 'S3'} {bucket} (QWebMap)"
        try:
            config_id = store_auth_config(name, keys.access_key_id, keys.secret_access_key)
        except PublishingError as error:
            self.log(tr("Object storage keys imported for this session ({}).").format(error.message))
            return
        if self.d_auth is not None:
            self.d_auth.setConfigId(config_id)
        self.profile.destination.credential_ref = config_id
        self.d_session_key.clear()
        self.d_session_secret.clear()
        self.log(tr("Object storage keys imported and saved encrypted in QGIS as “{}”.").format(name))

    def _busy(self, busy: bool):
        for button in (self.btn_save, self.btn_export, self.btn_publish, self.btn_close):
            button.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)

    def cancel(self):
        if self.feedback is not None:
            self.feedback.cancel()
        if self.task is not None:
            self.task.cancel()

    def _extent(self) -> QgsRectangle:
        extent = self._current_extent_3857()
        if not extent:
            raise PublishingError("Q2VT_PUB_PROFILE_INVALID", tr("Choose an extent."))
        return QgsRectangle(*extent)

    def export_locally(self) -> bool:
        if not self.save_settings():
            return False
        from ..publishing.controller import export_local  # pylint: disable=import-outside-toplevel
        self.feedback = DialogFeedback(self)
        self._busy(True)
        self.progress.setValue(0)
        self.logbox.clear()
        try:
            result = export_local(self.project, self.profile, self._extent(), self.feedback,
                                  stage_callback=lambda s: self.status.setText(tr("Stage: ") + s))
        except PublishingError as error:
            self._fail(error.code, error.message, error.detail)
            return False
        except Exception:  # noqa: BLE001
            self._fail("ERROR", tr("Export failed."), traceback.format_exc())
            return False
        finally:
            self._busy(False)
            self.feedback = None
        self.local_result = result
        self.local_profile_json = store.dumps_profile(self.profile)
        self.status.setText(tr("Local web map ready: {} ({} files, {:.1f} MB).").format(
            result.entry_path, len(result.release.files), result.release.total_bytes / 1e6))
        for warning in result.warnings:
            self.log(f"⚠ {warning}")
        for button in (self.btn_preview, self.btn_folder):
            button.setEnabled(True)
        return True

    def _fail(self, code, message, detail=""):
        self.status.setText(f"{code}: {message}")
        if detail:
            self.log(detail)
        QMessageBox.warning(self, tr("Web map"), f"{message}\n\n{code}")

    def preview(self):
        if not self.local_result:
            return
        from ..publishing.preview_server import PreviewServer  # pylint: disable=import-outside-toplevel
        if self.preview_server is None:
            self.preview_server = PreviewServer(os.path.dirname(self.local_result.publication_dir)).start()
        url = self.preview_server.url(f"{os.path.basename(self.local_result.publication_dir)}/index.html")
        self.log(tr("Preview: {}").format(url))
        QDesktopServices.openUrl(QUrl(url))

    def open_folder(self):
        if self.local_result:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.local_result.publication_dir))

    def open_map(self):
        if self.public_url:
            QDesktopServices.openUrl(QUrl(self.public_url))

    def copy_link(self):
        if self.public_url:
            QGuiApplication.clipboard().setText(self.public_url)
            self.status.setText(tr("Link copied: {}").format(self.public_url))

    def _credentials(self):
        from ..publishing.credentials import from_auth_config  # pylint: disable=import-outside-toplevel
        from ..publishing.providers.base import Credentials  # pylint: disable=import-outside-toplevel
        if self.d_session_key.text().strip() and self.d_session_secret.text():
            return Credentials(self.d_session_key.text().strip(), self.d_session_secret.text())
        return from_auth_config(self.profile.destination.credential_ref)

    def _provider(self):
        from ..publishing.providers import provider_for  # pylint: disable=import-outside-toplevel
        profile = self.profile
        return provider_for(profile.destination, self._credentials(), publication_prefix(profile))

    def test_connection(self):
        profile = self.collect()
        if profile.destination.kind == "local":
            QMessageBox.information(self, tr("Destination"), tr("Local only: nothing to test."))
            return
        try:
            checks = self._provider().inspect()
        except PublishingError as error:
            self._fail(error.code, error.message, error.detail)
            return
        text = "\n".join(f"{'✔' if ok else '✖'} {name}: {detail}" for name, ok, detail in checks)
        QMessageBox.information(self, tr("Connection"), text)

    def show_r2_guide(self):
        from .r2_guide import R2GuideDialog  # pylint: disable=import-outside-toplevel
        self.r2_guide = R2GuideDialog(self)
        self.r2_guide.show()

    def _parse_pasted(self, field):
        """A Cloudflare address pasted into the account, endpoint or bucket
        field: account id (+ bucket, + jurisdiction endpoint) taken from it."""
        from ..publishing.providers.r2 import parse_pasted  # pylint: disable=import-outside-toplevel
        text = field.text().strip()
        found = parse_pasted(text)
        if not found or (field is self.d_bucket and "://" not in text and "." not in text):
            return
        if self.d_kind.currentData() == "local":
            self.d_kind.setCurrentIndex(max(0, self.d_kind.findData("r2")))
        self.d_account.setText(found["account_id"])
        self.d_endpoint.setText(found.get("endpoint", ""))
        if found.get("bucket"):
            self.d_bucket.setText(found["bucket"])
        elif field is self.d_bucket:
            self.d_bucket.clear()

    def _destination_kind_changed(self, *_):
        kind = self.d_kind.currentData()
        for widget, shown in ((self.d_account, kind == "r2"), (self.d_endpoint, kind != "local")):
            widget.setVisible(shown)
            label = self.d_form.labelForField(widget)
            if label is not None:
                label.setVisible(shown)
        label = self.d_form.labelForField(self.d_endpoint)
        if label is not None:
            label.setText(tr("S3 API endpoint (optional)") if kind == "r2" else tr("S3 API endpoint"))
        self._update_address()

    def _update_address(self, *_):
        public = self.d_public.text().strip().rstrip("/")
        if self.d_kind.currentData() == "local" or not public:
            self.d_address.setText(tr("— (enter the public base URL)") if self.d_kind.currentData() != "local"
                                   else tr("— (local only)"))
            return
        from ..publishing.profile import normalize_prefix  # pylint: disable=import-outside-toplevel
        try:
            prefix = self.d_prefix.text().strip()
            prefix = normalize_prefix(prefix) if prefix else \
                f"maps/{self.e_slug.text().strip() or slugify(self.e_title.text().strip())}"
            self.d_address.setText(f"{public}/{prefix}/")
        except PublishingError:
            self.d_address.setText(tr("— (check the prefix)"))

    def save_keys(self):
        """The session keys, stored encrypted in the QGIS authentication
        database as a Basic configuration, then selected."""
        from ..publishing.credentials import store_auth_config  # pylint: disable=import-outside-toplevel
        key, secret = self.d_session_key.text().strip(), self.d_session_secret.text()
        if not key or not secret:
            QMessageBox.information(self, tr("Keys"), tr("Paste the Access Key ID and the Secret Access "
                                                         "Key of the R2 API token first."))
            return
        bucket = self.d_bucket.text().strip() or "maps"
        name = f"{'R2' if self.d_kind.currentData() == 'r2' else 'S3'} {bucket} (QWebMap)"
        try:
            config_id = store_auth_config(name, key, secret)
        except PublishingError as error:
            self._fail(error.code, error.message, error.detail)
            return
        if self.d_auth is not None:
            self.d_auth.setConfigId(config_id)
        self.d_session_key.clear()
        self.d_session_secret.clear()
        self.status.setText(tr("Keys saved encrypted in QGIS as “{}” and selected.").format(name))

    def show_cors(self):
        profile = self.collect()
        origin = profile.destination.public_base_url or "https://maps.example.com"
        from urllib.parse import urlparse  # pylint: disable=import-outside-toplevel
        parsed = urlparse(origin)
        policy = ('[\n  {\n    "AllowedOrigins": ["' + f"{parsed.scheme}://{parsed.netloc}" + '"],\n'
                  '    "AllowedMethods": ["GET", "HEAD"],\n'
                  '    "AllowedHeaders": ["Range", "If-Match", "If-None-Match"],\n'
                  '    "ExposeHeaders": ["Accept-Ranges", "Content-Range", "Content-Length", "ETag"],\n'
                  '    "MaxAgeSeconds": 3600\n  }\n]')
        box = QMessageBox(self)
        box.setWindowTitle(tr("CORS policy"))
        box.setText(tr("Only needed when the viewer is served from another origin than the map data. "
                       "Merge it with the bucket's existing rules in the Cloudflare dashboard (R2 → "
                       "bucket → Settings → CORS policy); the plugin does not change it."))
        box.setDetailedText(policy)
        box.exec()

    def show_history(self):
        try:
            provider = self._provider()
            releases = provider.list_releases()
            pointer, _ = provider.read_pointer()
        except PublishingError as error:
            self._fail(error.code, error.message, error.detail)
            return
        from .deployment_history import DeploymentHistoryDialog  # pylint: disable=import-outside-toplevel
        DeploymentHistoryDialog(self, provider, self.profile, releases,
                                pointer.get("releaseId") if pointer else None).exec()

    def refresh_review(self):
        profile = self.collect()
        lines = [tr("Target: ") + (self._target_url(profile) or tr("local folder only")), ""]
        for config in profile.layers:
            if not config.included:
                continue
            layer = self.project.mapLayer(config.layer_id)
            if layer is None:
                continue
            if isinstance(layer, QgsRasterLayer):
                lines.append(f"■ {config.title or layer.name()} — " + tr(
                    "raster layer: its image as QGIS draws it, inside the extent ({} format)").format(
                    config.raster_format.upper()))
                if (layer.providerType() or "").lower() in ("wms", "xyz", "arcgismapserver", "wcs"):
                    lines.append("   ⚠ " + tr("Online map service: check that its licence allows republishing."))
                if not config.toggleable:
                    lines.append("   " + tr("Always shown (visitors cannot switch it off)"))
                continue
            lines.append(f"■ {config.title or layer.name()} — {layer.featureCount()} "
                         + tr("features in the layer (published: those inside the extent drawn by "
                              "the exported rules)"))
            if not config.toggleable:
                lines.append("   " + tr("Always shown (visitors cannot switch it off)"))
            lines.append("   " + tr("Geometry and generated label/style values: public"))
            if config.popup_fields:
                lines.append("   " + tr("Popup fields: ") + ", ".join(p.field for p in config.popup_fields))
            if config.search_fields:
                lines.append("   " + tr("Searchable fields: ") + ", ".join(config.search_fields))
            if config.filter_fields:
                lines.append("   " + tr("Filter fields (also in the tiles): ")
                             + ", ".join(f.field for f in config.filter_fields))
            lines.append("   " + tr("Feature key: ") + (", ".join(config.key_fields) or
                                                       tr("none (links valid in one version only)")))
            if not config.initially_visible:
                lines.append("   " + tr("Hidden at start (still published)"))
        if profile.output.include_all_fields:
            lines += ["", tr("WARNING: all attribute fields are written to the tiles and can be "
                             "downloaded by anyone.")]
        if profile.basemap.kind == "protomaps":
            lines += ["", tr("Basemap: OpenStreetMap vector tiles of the area, bundled into the release; "
                             "downloaded from {} while exporting.").format(
                profile.basemap.source or "build.protomaps.com")]
        if profile.themes.names:
            lines.append(tr("Views (map themes): ") + ", ".join(profile.themes.names))
        info = profile.parcel_info
        if info.enabled:
            lines += ["", tr("Parcel report (public for every parcel in the extent): area, parts by zone, "
                             "restrictions touching it.")]
            if info.fields:
                lines.append("   " + tr("Parcel fields: ") + ", ".join(f.field for f in info.fields))
            if info.zoning_fields:
                lines.append("   " + tr("Zone fields: ") + ", ".join(f.field for f in info.zoning_fields))
            names = [r.name_field for r in info.restrictions if r.name_field]
            if names:
                lines.append("   " + tr("Restriction name fields: ") + ", ".join(names))
        lines += ["", tr("External resources for visitors: none (no CDN, no third-party tiles, no telemetry)."),
                  tr("Cost: storage of the archive and retained releases plus requests; R2 has no "
                     "egress fees but limits above the free tier are billed — see "
                     "https://developers.cloudflare.com/r2/pricing/ (checked 1 Oct 2026).")]
        if self.local_result:
            lines.append(tr("Last local export: {} files, {:.1f} MB.").format(
                len(self.local_result.release.files), self.local_result.release.total_bytes / 1e6))
        review = needs_review(profile)
        lines += ["", tr("Approval needed (first publication or what becomes public changed).")
                  if review else tr("Approved earlier for this exact data scope.")]
        self.review.setPlainText("\n".join(lines))
        self.approve.setChecked(not review)

    @staticmethod
    def _target_url(profile):
        if profile.destination.kind == "local" or not profile.destination.public_base_url:
            return ""
        from ..publishing.public_verify import join_url  # pylint: disable=import-outside-toplevel
        return join_url(profile.destination.public_base_url, publication_prefix(profile), "index.html")

    def publish(self):
        profile = self.collect()
        if profile.destination.kind == "local":
            if self.export_locally():
                self.public_url = QUrl.fromLocalFile(self.local_result.entry_path).toString()
                self.btn_open.setEnabled(True)
            return
        if needs_review(profile) and not self.approve.isChecked():
            self.tabs.setCurrentIndex(self.tabs.count() - 1)
            self.refresh_review()
            QMessageBox.information(self, tr("Review"), tr("Review what becomes public and tick the "
                                                           "approval before publishing."))
            return
        if needs_review(profile):
            profile.approval.fingerprint = disclosure_fingerprint(profile)
            profile.approval.approved_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        # Reuse a valid local export of identical settings (retry/new destination).
        if self.local_result is None or self.local_profile_json != store.dumps_profile(profile):
            if not self.export_locally():
                return
        try:
            credentials = self._credentials()
            provider = self._provider()
        except PublishingError as error:
            self._fail(error.code, error.message, error.detail)
            return
        from ..publishing.controller import publication_dirs  # pylint: disable=import-outside-toplevel
        _, work_dir = publication_dirs(profile)
        self.task = UploadTask(self.local_result.release, profile, provider, work_dir,
                               credentials.secrets())
        self.task.progressChanged.connect(lambda value: self.progress.setValue(int(value)))
        self.task.message.connect(self.log)
        self.task.taskCompleted.connect(self._published)
        self.task.taskTerminated.connect(self._published)
        self._busy(True)
        self.status.setText(tr("Uploading and verifying…"))
        QgsApplication.taskManager().addTask(self.task)

    def _published(self):
        task, self.task = self.task, None
        self._busy(False)
        if task is None:
            return
        if task.error:
            self._fail("ERROR", tr("Publishing failed."), task.error)
            return
        result = task.result
        for check in result.checks.checks:
            self.log(f"{'✔' if check.ok else '✖'} {check.name}: {check.detail}")
        if result.state == ReleaseState.PUBLISHED:
            self.public_url = result.stable_url
            store.save_profile(self.project, self.profile)
            self.btn_open.setEnabled(True)
            self.btn_copy.setEnabled(True)
            self.status.setText(tr("Published: {}\nThis version: {}").format(result.stable_url,
                                                                             result.versioned_url))
            for warning in result.warnings:
                self.log(f"⚠ {warning}")
        else:
            self._fail(result.code or result.state.value, result.message or result.state.value)

    # Size and position of the window, kept between QGIS sessions (QGIS settings).
    GEOMETRY_KEY = "QGIS2VectorTilesFork/publishDialog/geometry"  # pre-rename key, kept

    def _restore_geometry(self):
        try:
            saved = QgsSettings().value(self.GEOMETRY_KEY)
            if not saved or not self.restoreGeometry(saved):
                return
        except (TypeError, ValueError):
            return
        # A screen that is gone (laptop undocked): back to the default size and place.
        frame = self.frameGeometry()
        if not any(screen.availableGeometry().intersects(frame) for screen in QGuiApplication.screens()):
            self.resize(980, 760)
            primary = QGuiApplication.primaryScreen()
            if primary is not None:
                self.move(primary.availableGeometry().center() - self.rect().center())

    def _save_geometry(self):
        try:
            QgsSettings().setValue(self.GEOMETRY_KEY, self.saveGeometry())
        except (TypeError, ValueError):
            pass

    def done(self, result):  # Escape / reject() close without a closeEvent
        self._save_geometry()
        super().done(result)

    def closeEvent(self, event):  # noqa: N802
        self._save_geometry()
        if self.task is not None:
            QMessageBox.information(self, tr("Web map"), tr("Publishing is still running in the "
                                                            "background; see the task bar."))
        try:
            self.save_settings(quiet=True)
        except Exception:  # noqa: BLE001 - closing must not fail
            pass
        super().closeEvent(event)
