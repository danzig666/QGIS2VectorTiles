"""QGIS2VectorTiles (fork) plugin for QGIS"""

from qgis.core import QgsApplication
from .src.processing.provider import QGIS2VectorTilesPorvider


class QGIS2VectorTiles:
    """QGIS2VectorTiles main class"""

    def __init__(self, iface):
        """Constructor"""
        self.provider = None
        self.iface = iface

    def initProcessing(self):
        """Initialize processing provider"""
        self.provider = QGIS2VectorTilesPorvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

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
        self.publish_action.setToolTip("QGIS2VectorTiles (fork): publish the project as a vector-tile "
                                       "web map (PMTiles)")
        self.publish_action.triggered.connect(self.show_publish_dialog)
        self.iface.addWebToolBarIcon(self.publish_action)
        self.iface.addPluginToWebMenu("QGIS2VectorTiles (fork)", self.publish_action)
        self.dialog = None

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
        QgsApplication.processingRegistry().removeProvider(self.provider)
        action = getattr(self, "publish_action", None)
        if action is not None and self.iface is not None:
            self.iface.removeWebToolBarIcon(action)
            self.iface.removePluginWebMenu("QGIS2VectorTiles (fork)", action)
            action.deleteLater()
        if getattr(self, "dialog", None) is not None:
            self.dialog.close()
            self.dialog = None
        from .src.publishing.preview_server import stop_all  # pylint: disable=import-outside-toplevel
        stop_all()


def classFactory(iface):
    """invoke plugin"""
    return QGIS2VectorTiles(iface)
