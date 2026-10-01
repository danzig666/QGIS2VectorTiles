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
                       QgsLayerTreeGroup, QgsLayerTreeLayer, QgsMessageLog,
                       QgsProcessingFeedback, QgsProject, QgsRectangle, QgsTask, QgsVectorLayer)
from qgis.PyQt.QtCore import QCoreApplication, Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices, QGuiApplication
from qgis.PyQt.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                                 QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout,
                                 QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                                 QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
                                 QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                                 QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..publishing.errors import PublishingError
from ..publishing.models import (FIELD_TYPES, FilterField, LayerConfig, PopupField,
                                 PublicationProfile, ReleaseState, slugify)
from ..publishing.profile import disclosure_fingerprint, needs_review, publication_prefix, validate
from . import publication_profiles as store

TAG = "QGIS2VectorTiles"
CHECKED = Qt.CheckState.Checked
UNCHECKED = Qt.CheckState.Unchecked


def tr(text: str) -> str:
    return QCoreApplication.translate("PublishDialog", text)


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
        self.setWindowTitle(tr("Publish Web Map — QGIS2VectorTiles (fork)"))
        self.resize(980, 760)
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
            if isinstance(layer, QgsVectorLayer) and layer.isSpatial():
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
            if isinstance(layer, QgsVectorLayer) and layer.isSpatial() and layer.id() not in known:
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
        for button in (self.btn_save, self.btn_export, self.btn_preview, self.btn_publish,
                       self.btn_open, self.btn_copy, self.btn_folder):
            buttons.addWidget(button)
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
        extent_row = QHBoxLayout()
        self.extent_label = QLabel()
        self.extent_label.setWordWrap(True)
        extent_button = QPushButton(tr("Use the map canvas extent"))
        extent_button.clicked.connect(self._use_canvas_extent)
        extent_button.setEnabled(self.iface is not None)
        extent_row.addWidget(self.extent_label, 1)
        extent_row.addWidget(extent_button)
        form.addRow(tr("Extent"), extent_row)
        layout.addWidget(form_box, 1)
        layers_box = QGroupBox(tr("Layers (publishing does not change the project's layer visibility)"))
        layers_layout = QVBoxLayout(layers_box)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([tr("Layer"), tr("Publish"), tr("Visible at start")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        layers_layout.addWidget(self.tree)
        layout.addWidget(layers_box, 1)
        return widget

    def _choose_logo(self):
        path, _ = QFileDialog.getOpenFileName(self, tr("Logo"), "", "Images (*.png *.jpg *.jpeg *.webp)")
        if path:
            self.e_logo.setText(path)

    def _use_canvas_extent(self):
        self.profile.view.extent = self._canvas_extent_3857()
        self._show_extent()

    def _show_extent(self):
        extent = self.profile.view.extent
        self.extent_label.setText(tr("canvas extent") if not extent else
                                  "EPSG:3857 " + ", ".join(f"{v:.0f}" for v in extent))

    def _fill_tree(self, profile):
        self.tree.clear()
        configs = {c.layer_id: c for c in profile.layers}

        def add(group, parent):
            for child in group.children():
                if isinstance(child, QgsLayerTreeGroup):
                    item = QTreeWidgetItem(parent, [child.name()])
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
                    add(child, item)
                    item.setExpanded(True)
                elif isinstance(child, QgsLayerTreeLayer):
                    layer = child.layer()
                    if not isinstance(layer, QgsVectorLayer) or not layer.isSpatial():
                        continue
                    config = configs.get(layer.id()) or LayerConfig(layer.id(), included=False,
                                                                     initially_visible=False)
                    item = QTreeWidgetItem(parent, [layer.name()])
                    item.setData(0, Qt.ItemDataRole.UserRole, layer.id())
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(1, _check(config.included))
                    item.setCheckState(2, _check(config.initially_visible))
        add(self.project.layerTreeRoot(), self.tree.invisibleRootItem())
        self.tree.itemChanged.connect(self._tree_changed)

    def _tree_changed(self, item, column):
        if column == 2 and item.checkState(2) == CHECKED:
            item.setCheckState(1, CHECKED)
        if column == 1 and item.checkState(1) == UNCHECKED:
            item.setCheckState(2, UNCHECKED)

    def _tree_items(self):
        stack = [self.tree.invisibleRootItem()]
        while stack:
            item = stack.pop()
            for i in range(item.childCount()):
                child = item.child(i)
                stack.append(child)
                if child.data(0, Qt.ItemDataRole.UserRole):
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
                ("print", tr("Print"))]):
            box = QCheckBox(label)
            self.i_flags[key] = box
            grid.addWidget(box, index // 3, index % 3)
        layout.addWidget(options)
        split = QSplitter()
        self.i_layers = QListWidget()
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
        form.addRow(tr("Title in the viewer"), self.i_title)
        form.addRow(tr("Feature title (display expression)"), self.i_display)
        form.addRow(tr("Initial opacity"), self.i_opacity)
        form.addRow("", self.i_legend)
        form.addRow("", self.i_links)
        right_layout.addLayout(form)
        self.i_fields = QTableWidget(0, 7)
        self.i_fields.setHorizontalHeaderLabels([tr("Field"), tr("Popup"), tr("Popup title"), tr("Type"),
                                                 tr("Search"), tr("Feature key"), tr("Filter")])
        self.i_fields.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.i_fields.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        right_layout.addWidget(QLabel(tr("Only checked fields become public (popup, search, filter). "
                                         "The feature key should be unique and never empty, e.g. a "
                                         "parcel number; without one, feature links only work in the "
                                         "same version of the map.")))
        right_layout.addWidget(self.i_fields, 1)
        split.addWidget(right)
        split.setStretchFactor(1, 3)
        layout.addWidget(split, 1)
        return widget

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
        form.addRow(tr("Archive"), self.o_archive)
        form.addRow(tr("Local output folder"), dir_row)
        form.addRow("", self.o_xyz)
        form.addRow("", self.o_zip)
        form.addRow(tr("CPU limit (%)"), self.o_cpu)
        form.addRow(tr("Fidelity"), self.o_fidelity)
        form.addRow(tr("Beyond the maximum zoom"), self.o_overzoom)
        form.addRow(tr("Polygon labels"), self.o_labels)
        form.addRow("", self.o_all_fields)
        form.addRow("", QLabel(tr("Publishing never uses raster tiles: the map is vector tiles (MVT) "
                                  "in a PMTiles archive. Sprites, patterns, legend swatches and fonts "
                                  "are styling assets.")))
        return widget

    def _choose_dir(self):
        path = QFileDialog.getExistingDirectory(self, tr("Local output folder"), self.o_dir.text())
        if path:
            self.o_dir.setText(path)

    def _destination_tab(self):
        widget = QWidget()
        form = QFormLayout(widget)
        self.d_kind = QComboBox()
        self.d_kind.addItem(tr("Local only (no upload)"), "local")
        self.d_kind.addItem(tr("Cloudflare R2"), "r2")
        self.d_kind.addItem(tr("Other S3-compatible storage"), "s3")
        self.d_account = QLineEdit()
        self.d_endpoint = QLineEdit()
        self.d_endpoint.setPlaceholderText(tr("R2: derived from the account id"))
        self.d_bucket = QLineEdit()
        self.d_prefix = QLineEdit()
        self.d_prefix.setPlaceholderText("maps/<slug>")
        self.d_public = QLineEdit()
        self.d_public.setPlaceholderText("https://maps.example.com")
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
        form.addRow(tr("Destination"), self.d_kind)
        form.addRow(tr("Cloudflare account id"), self.d_account)
        form.addRow(tr("S3 API endpoint"), self.d_endpoint)
        form.addRow(tr("Bucket"), self.d_bucket)
        form.addRow(tr("Prefix in the bucket"), self.d_prefix)
        form.addRow(tr("Public base URL (custom domain)"), self.d_public)
        form.addRow(tr("Saved credentials (QGIS authentication: user = access key id, "
                       "password = secret)"), auth_row)
        session = QHBoxLayout()
        self.d_session_key = QLineEdit()
        self.d_session_key.setPlaceholderText(tr("Access key id (this session only)"))
        self.d_session_secret = QLineEdit()
        self.d_session_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self.d_session_secret.setPlaceholderText(tr("Secret access key (this session only)"))
        session.addWidget(self.d_session_key)
        session.addWidget(self.d_session_secret)
        form.addRow(tr("Or keys for this session only"), session)
        form.addRow(tr("Releases to keep"), self.d_retention)
        form.addRow("", self.d_conditional)
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
        form.addRow("", QLabel(tr("Use bucket-scoped API tokens. The S3 API endpoint is not the public "
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
        self._show_extent()
        self._fill_tree(profile)  # extent label refreshed by _use_canvas_extent
        for key, box in self.i_flags.items():
            box.setChecked(bool(getattr(profile.interaction, key)))
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

    def _tab_changed(self, index):
        if self.tabs.widget(index) is not None and self.tabs.tabText(index) == tr("Interaction"):
            self._fill_interaction_layers()
        if self.tabs.tabText(index) == tr("Review"):
            self.refresh_review()

    def _included_ids(self):
        return [item.data(0, Qt.ItemDataRole.UserRole) for item in self._tree_items()
                if item.checkState(1) == CHECKED]

    def _fill_interaction_layers(self):
        self._store_layer_fields()
        self.i_layers.blockSignals(True)
        self.i_layers.clear()
        for layer_id in self._included_ids():
            layer = self.project.mapLayer(layer_id)
            item = QListWidgetItem(layer.name())
            item.setData(Qt.ItemDataRole.UserRole, layer_id)
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
        self.i_title.setText(config.title)
        self.i_display.setText(config.display_expression)
        self.i_opacity.setValue(config.opacity)
        self.i_legend.setChecked(config.legend)
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
        config.title = self.i_title.text().strip()
        config.display_expression = self.i_display.text().strip()
        config.opacity = float(self.i_opacity.value())
        config.legend = self.i_legend.isChecked()
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
            layer_id = item.data(0, Qt.ItemDataRole.UserRole)
            config = self.layer_configs.get(layer_id) or LayerConfig(layer_id)
            config.included = item.checkState(1) == CHECKED
            config.initially_visible = config.included and item.checkState(2) == CHECKED
            layers.append(config)
        profile.layers = layers
        for key, box in self.i_flags.items():
            setattr(profile.interaction, key, box.isChecked())
        out = profile.output
        out.archive = self.o_archive.currentData()
        out.local_directory = self.o_dir.text().strip()
        out.xyz_package, out.zip = self.o_xyz.isChecked(), self.o_zip.isChecked()
        out.cpu_percent = self.o_cpu.value()
        out.fidelity_mode = self.o_fidelity.currentData()
        out.overzoom = self.o_overzoom.currentData()
        out.polygon_labels_base = self.o_labels.currentData()
        out.include_all_fields = self.o_all_fields.isChecked()
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
        extent = self.profile.view.extent or (self._canvas_extent_3857() if self.iface else None)
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
            lines.append(f"■ {config.title or layer.name()} — {layer.featureCount()} "
                         + tr("features in the layer (published: those inside the extent drawn by "
                              "the exported rules)"))
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
        lines += ["", tr("External resources: none (no CDN, no basemap, no telemetry)."),
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
            self.tabs.setCurrentIndex(4)
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

    def closeEvent(self, event):  # noqa: N802
        if self.task is not None:
            QMessageBox.information(self, tr("Web map"), tr("Publishing is still running in the "
                                                            "background; see the task bar."))
        try:
            self.save_settings(quiet=True)
        except Exception:  # noqa: BLE001 - closing must not fail
            pass
        super().closeEvent(event)
