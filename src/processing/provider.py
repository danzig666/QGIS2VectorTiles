"""QWebMap Processing provider."""

from qgis.core import QgsProcessingProvider
from .algorithms import QGIS2VectorTilesAlgorithm, _ICON


# Create a proper temporary provider class
class QGIS2VectorTilesPorvider(QgsProcessingProvider):
    """QWebMap's Processing provider, integrating the QGIS2VectorTilesAlgorithm
    into the QGIS Processing framework."""

    def __init__(self):
        super().__init__()

    def id(self):
        """Returns the unique ID of the provider (distinct from the official
        QGIS2VectorTiles plugin, so both can be installed; the pre-rename id,
        kept so saved models and scripts still find the algorithm)."""
        return "QGIS2VectorTilesFork"

    def name(self):
        """Returns the display name of the provider."""
        return "QWebMap"

    def icon(self):
        """Returns the provider icon."""
        return _ICON

    def loadAlgorithms(self):
        """Loads the algorithms provided by this provider."""
        self.addAlgorithm(QGIS2VectorTilesAlgorithm())
