"""QWebMap plugin for QGIS (formerly QGIS2VectorTiles fork)"""

import sys as _sys

# A plugin update without restarting QGIS: QGIS forgets only the plugin
# modules it saw imported when the plugin loaded; modules imported later
# (e.g. the export cache, imported when an export runs) stayed in memory in
# their old version next to new ones ("module ... has no attribute ...").
# Every module of this plugin is loaded afresh whenever the plugin is.
for _name in [name for name in _sys.modules if name.startswith(__name__ + ".")]:
    del _sys.modules[_name]

from qgis.core import QgsApplication  # noqa: E402  pylint: disable=wrong-import-position
from .src.processing.provider import QGIS2VectorTilesPorvider  # noqa: E402  pylint: disable=wrong-import-position

PLUGIN_NAME = "QWebMap"
# The plugin's folder before the rename: when both are installed, the old one
# registers the same Processing provider and adds a second menu.
OLD_FOLDER = "QGIS2VectorTilesFork"
PROVIDER_ID = "QGIS2VectorTilesFork"  # QGIS2VectorTilesPorvider.id()


class QGIS2VectorTiles:
    """QWebMap main class"""

    def __init__(self, iface):
        """Constructor"""
        self.provider = None
        self.iface = iface

    def initProcessing(self):
        """Register the Processing provider (once).

        QGIS calls this itself (``hasProcessingProvider=yes``) and initGui
        calls it again for older QGIS versions. A second ``addProvider``
        with the same id makes QGIS delete that provider, so unload crashed
        on it. A provider left registered by an earlier version (its unload
        failed) is replaced, so an upgrade runs the new code."""
        if self.provider is not None:
            return
        registry = QgsApplication.processingRegistry()
        stale = registry.providerById(PROVIDER_ID)
        if stale is not None:
            registry.removeProvider(stale)
        self.provider = QGIS2VectorTilesPorvider()
        if not registry.addProvider(self.provider):
            self.provider = None  # QGIS deleted it

    def initGui(self):
        """Initialize GUI: Processing provider + "Publish Web Map" window."""
        self.initProcessing()
        self.publish_action = None
        if self.iface is None:
            return
        from os.path import dirname, join  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtGui import QIcon  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtWidgets import QAction  # pylint: disable=import-outside-toplevel
        self.publish_action = QAction(QIcon(join(dirname(__file__), "icon.png")),
                                      "Publish Web Map…", self.iface.mainWindow())
        self.publish_action.setObjectName("q2vtPublishWebMap")
        self.publish_action.setToolTip(f"{PLUGIN_NAME}: publish the project as a web map")
        self.publish_action.triggered.connect(self.show_publish_dialog)
        self.iface.addWebToolBarIcon(self.publish_action)
        self.iface.addPluginToWebMenu(PLUGIN_NAME, self.publish_action)
        self.dialog = None
        self._warn_old_plugin()

    def _warn_old_plugin(self):
        """Tell the user to remove the plugin's pre-rename copy if it is installed."""
        try:
            from qgis.utils import available_plugins  # pylint: disable=import-outside-toplevel
        except ImportError:
            return
        if OLD_FOLDER not in available_plugins or __name__ == OLD_FOLDER:
            return
        from qgis.core import Qgis  # pylint: disable=import-outside-toplevel
        self.iface.messageBar().pushMessage(
            PLUGIN_NAME, "QGIS2VectorTiles (fork) is the old name of QWebMap and is still installed. "
            "Uninstall it in Plugins > Manage and Install Plugins (your settings are kept).",
            Qgis.MessageLevel.Warning, 0)

    def show_publish_dialog(self):
        """Open (or raise) the Publish Web Map window."""
        from .src.gui.publish_dialog import PublishDialog  # pylint: disable=import-outside-toplevel
        if self.dialog is None:
            self.dialog = PublishDialog(self.iface)
            self.dialog.finished.connect(self._dialog_closed)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def _dialog_closed(self):
        self.dialog = None

    def unload(self):
        """Unload plugin: remove the provider, the action and stop previews."""
        if self.provider is not None:
            # By id: never touches a deleted Python wrapper.
            QgsApplication.processingRegistry().removeProvider(PROVIDER_ID)
            self.provider = None
        action = getattr(self, "publish_action", None)
        if action is not None and self.iface is not None:
            self.iface.removeWebToolBarIcon(action)
            self.iface.removePluginWebMenu(PLUGIN_NAME, action)
            action.deleteLater()
        if getattr(self, "dialog", None) is not None:
            self.dialog.close()
            self.dialog = None
        from .src.publishing.preview_server import stop_all  # pylint: disable=import-outside-toplevel
        stop_all()


def classFactory(iface):
    """invoke plugin"""
    return QGIS2VectorTiles(iface)
