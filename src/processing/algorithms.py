"""QGIS Processing algorithm of the QWebMap plugin (vector tile package export)."""

from os.path import join
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    Qgis,
    QgsProcessingAlgorithm,
    QgsProcessingParameterNumber,
    QgsProcessingParameterExtent,
    QgsProcessingParameterFolderDestination,
    QgsCoordinateReferenceSystem,
    QgsProcessingParameterEnum,
    QgsProcessingParameterBoolean,
    QgsProcessingOutputFile,
    QgsProcessingOutputFolder,
)
from qgis.utils import iface
from ..qgis2vectortiles import QGIS2VectorTiles
from ..utils.config import _PLUGIN_DIR, _EPSG_CRS

_ICON = QIcon(join(_PLUGIN_DIR, "icon.png"))


class QGIS2VectorTilesAlgorithm(QgsProcessingAlgorithm):
    """
    QGIS Processing Algorithm for generating styled tiles from project layers.
    This wrapper provides a user interface for the tiles generation process
    through the QGIS Processing Toolbox.
    """

    # Parameter names (constants for consistency)
    MIN_ZOOM = "MIN_ZOOM"
    MAX_ZOOM = "MAX_ZOOM"
    EXTENT = "EXTENT"
    CPU_PERCENT = "CPU_PERCENT"
    OUTPUT_DIR = "OUTPUT_DIR"
    REQUIRED_FIELDS_ONLY = "FIELDS_INCLUDED"
    OUTPUT_TYPE = "OUTPUT_TYPE"
    POLYGONS_LABELS_BASE = "POLYGONS_LABELS_BASE"
    VIEWER = "VIEWER"
    BACKGROUND_TYPE = "BACKGROUND_TYPE"
    FIDELITY_MODE = "FIDELITY_MODE"
    OVERZOOM = "OVERZOOM"
    STATIC_PACKAGE = "STATIC_PACKAGE"
    PARALLEL = "PARALLEL"
    TILE_ARCHIVE_FORMAT = "TILE_ARCHIVE_FORMAT"
    ARCHIVE_FORMATS = ("mbtiles", "pmtiles", "both")

    def __init__(self):
        """Initialize the algorithm"""
        super().__init__()

    def tr(self, string):
        """
        Returns a translatable string with the self.tr() function.
        """
        return QCoreApplication.translate("Processing", string)

    def createInstance(self):
        """
        Returns a new instance of the algorithm. Required by QGIS Processing framework.
        """
        return QGIS2VectorTilesAlgorithm()

    def name(self):
        """
        Returns the algorithm name, used for identifying the algorithm.
        This string should be fixed for the algorithm, and must not be localized.
        """
        return "QGIS2VectorTiles_action"

    def displayName(self):
        """
        Returns the translated algorithm name, which should be used for any
        user-visible display of the algorithm name.
        """
        return self.tr("Export vector tile package")

    def flags(self):
        """
        Runs on QGIS's main thread: the export reads the project and adds the
        result layer to it, which QGIS only allows from the main thread (from
        a background thread QGIS can close without a message). The heavy work
        still runs in worker threads.
        """
        flag_enum = getattr(Qgis, "ProcessingAlgorithmFlag", None)
        no_threading = getattr(flag_enum, "NoThreading", None)
        if no_threading is None:  # QGIS < 3.36
            no_threading = QgsProcessingAlgorithm.FlagNoThreading
        return super().flags() | no_threading

    def group(self):
        """
        Returns the name of the group this algorithm belongs to.
        """
        return None  # No inner group as requested

    def groupId(self):
        """
        Returns the unique ID of the group this algorithm belongs to.
        """
        return None  # No inner group as requested

    def icon(self):
        """
        Returns the algorithm icon.
        """
        return _ICON

    def shortHelpString(self):
        """
        Returns a localised short helper string for the algorithm.
        """
        return self.tr(
            "Converts a QGIS project into a vector tile package with a single vector tile source, a web style matching the original QGIS styling, and a ready-to-use web viewer.\nIt enables fast client-side rendering, lightweight publishing, and easy sharing - without servers or third-party libraries.\nTo publish a full web map, use Web > QWebMap: Publish Web Map….\nQWebMap started as a fork of QGIS2VectorTiles (https://github.com/GallPeters/QGIS2VectorTiles): https://github.com/danzig666/QGIS2VectorTiles"
        )

    def initAlgorithm(self, config=None):  # pylint: disable=W0613
        """
        Define the inputs and outputs of the algorithm.
        """

        # Minimum zoom level parameter
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MIN_ZOOM,
                self.tr("Minimum Zoom (Inclusive)"),
                type=QgsProcessingParameterNumber.Type.Integer,
                defaultValue=0,
                minValue=0,
                maxValue=22,
            )
        )

        # Maximum zoom level parameter
        self.addParameter(
            QgsProcessingParameterNumber(
                self.MAX_ZOOM,
                self.tr("Maximum Zoom (Inclusive)"),
                type=QgsProcessingParameterNumber.Type.Integer,
                defaultValue=10,
                minValue=0,
                maxValue=22,
            )
        )

        # Extent parameter - defaults to current map canvas extent
        extent_param = QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Tiles Extent"), optional=False
        )
        # Set default to current map canvas extent if available
        if iface and iface.mapCanvas():
            extent_param.setDefaultValue(iface.mapCanvas().extent())
        self.addParameter(extent_param)

        # CPU Percent parameter
        self.addParameter(
            QgsProcessingParameterNumber(
                self.CPU_PERCENT,
                self.tr("CPU Usage Limit for parallel export (%)"),
                type=QgsProcessingParameterNumber.Type.Integer,
                defaultValue=100,
                minValue=0,
                maxValue=100,
            )
        )
        self.addParameter(
            QgsProcessingParameterEnum(
                self.REQUIRED_FIELDS_ONLY,
                self.tr("Included Fields"),
                options=["Required Fields Only", "All Fields"],
                defaultValue=0,  # Default to Required Fields Only
                optional=False,
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.POLYGONS_LABELS_BASE,
                self.tr("Polygon Labels Base"),
                options=[
                    "Whole Polygon",
                    "Visible Polygon (the MapLibre viewer places labels on the visible part)",
                    "As set in each layer's labels (QGIS 'Centroid: whole / visible polygon')",
                ],
                defaultValue=2,
                optional=False,
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.BACKGROUND_TYPE,
                self.tr("Background"),
                options=["OpenStreetMap", "NASA's BlueMarble Imagery", "Project Background Color"],
                defaultValue=0,  # Default to Required Fields Only
                optional=False,
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.VIEWER,
                self.tr("Output Viewer"),
                options=["MapLibre (Recommended)", "OpenLayers"],
                defaultValue=0,  # Default to Required Fields Only
                optional=False,
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.FIDELITY_MODE,
                self.tr("Fidelity Mode"),
                options=[
                    "Vector-first: approximate where declared and report (Recommended)",
                    "Strict: fail if any component is unsupported or approximated",
                    "Hybrid: allow raster fallback for non-text cartography (not yet available)",
                ],
                defaultValue=0,
                optional=False,
            )
        )

        self.addParameter(
            QgsProcessingParameterEnum(
                self.OVERZOOM,
                self.tr("Beyond Maximum Zoom"),
                options=["Keep showing the last generated tiles (overzoom)", "Hide all layers"],
                defaultValue=0,
                optional=False,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.STATIC_PACKAGE,
                self.tr("Also write a static web package (XYZ tiles, relative URLs)"),
                defaultValue=False,
            )
        )

        self.addParameter(
            QgsProcessingParameterBoolean(
                self.PARALLEL,
                self.tr("Parallel export (faster; can crash QGIS, seen on QGIS 3.44 / Windows)"),
                defaultValue=False,
            )
        )

        archive = QgsProcessingParameterEnum(
            self.TILE_ARCHIVE_FORMAT,
            self.tr("Tile archive format"),
            options=[
                "MBTiles (as before; opens in QGIS)",
                "PMTiles (same vector tiles for static web hosting; QGIS 3.34 cannot open it, "
                "so no result layer is added)",
                "Both MBTiles and PMTiles",
            ],
            defaultValue=0,
            optional=True,
        )
        self.addParameter(archive)
        # Output directory parameter
        self.addParameter(
            QgsProcessingParameterFolderDestination(
                self.OUTPUT_DIR, self.tr("Output Directory"), optional=False
            )
        )

        self.addOutput(QgsProcessingOutputFolder("OUTPUT_FOLDER", self.tr("Export folder")))
        self.addOutput(QgsProcessingOutputFile("MBTILES", self.tr("MBTiles archive")))
        self.addOutput(QgsProcessingOutputFile("PMTILES", self.tr("PMTiles archive")))
        self.addOutput(QgsProcessingOutputFile("STYLE", self.tr("MapLibre style")))
        self.addOutput(QgsProcessingOutputFile("REPORT", self.tr("Fidelity report")))

    def checkParameterValues(self, parameters, context):
        """
        Validate parameter values before processing.
        Returns tuple (is_valid, error_message)
        """
        min_zoom = self.parameterAsInt(parameters, self.MIN_ZOOM, context)
        max_zoom = self.parameterAsInt(parameters, self.MAX_ZOOM, context)

        # Check that min_zoom <= max_zoom
        if min_zoom > max_zoom:
            return False, self.tr(
                "Minimum zoom level must be less than or equal to maximum zoom level"
            )

        return super().checkParameterValues(parameters, context)

    def processAlgorithm(self, parameters, context, feedback):
        """
        Main processing method. This is where your existing vector tiles generation logic
        should be called.

        Args:
            parameters: Dictionary containing parameter values
            context: QgsProcessingContext object
            feedback: QgsProcessingFeedback object for progress reporting

        Returns:
            Dictionary with results (can be empty for this use case)
        """

        # Extract parameter values
        min_zoom = self.parameterAsInt(parameters, self.MIN_ZOOM, context)
        max_zoom = self.parameterAsInt(parameters, self.MAX_ZOOM, context)
        extent = self.parameterAsExtent(
            parameters, self.EXTENT, context, QgsCoordinateReferenceSystem(f"EPSG:{_EPSG_CRS}")
        )
        cpu_percent = self.parameterAsInt(parameters, self.CPU_PERCENT, context)
        output_dir = self.parameterAsString(parameters, self.OUTPUT_DIR, context)
        include_required_fields_only = self.parameterAsBool(
            parameters, self.REQUIRED_FIELDS_ONLY, context
        )
        polygon_labels_base = self.parameterAsInt(parameters, self.POLYGONS_LABELS_BASE, context)
        background_type = self.parameterAsInt(parameters, self.BACKGROUND_TYPE, context)
        viewer = self.parameterAsInt(parameters, self.VIEWER, context)
        fidelity_mode = self.parameterAsInt(parameters, self.FIDELITY_MODE, context)
        overzoom = self.parameterAsInt(parameters, self.OVERZOOM, context)
        static_package = self.parameterAsBool(parameters, self.STATIC_PACKAGE, context)
        parallel = self.parameterAsBool(parameters, self.PARALLEL, context)
        archive_format = self.ARCHIVE_FORMATS[0]
        if parameters.get(self.TILE_ARCHIVE_FORMAT) is not None:
            archive_format = self.ARCHIVE_FORMATS[
                self.parameterAsEnum(parameters, self.TILE_ARCHIVE_FORMAT, context)]
        try:
            # Your existing vector tile generator class would be called here
            tiles_generator = QGIS2VectorTiles(
                min_zoom=min_zoom,
                max_zoom=max_zoom,
                extent=extent,
                cpu_percent=cpu_percent,
                output_dir=output_dir,
                include_required_fields_only=include_required_fields_only,
                cent_source=polygon_labels_base,
                background_type=background_type,
                viewer=viewer,
                feedback=feedback,
                fidelity_mode=fidelity_mode,
                overzoom=overzoom,
                static_package=static_package,
                parallel=parallel,
                archive_format=archive_format,
            )

            # Run the generation process
            output_path = tiles_generator.convert_project_to_vector_tiles()
            if output_path:
                feedback.pushInfo(". Vector tiles package generation completed successfully")
                results = {"OUTPUT_FOLDER": output_path, self.OUTPUT_DIR: output_dir}
                from os.path import exists  # pylint: disable=import-outside-toplevel
                for key, name in (("MBTILES", "tiles.mbtiles"), ("PMTILES", "tiles.pmtiles"),
                                  ("STYLE", join("style", "style.json")),
                                  ("REPORT", "fidelity_report.json")):
                    if exists(join(output_path, name)):
                        results[key] = join(output_path, name)
                return results

        except (NameError, ValueError, AttributeError, TypeError) as e:
            feedback.reportError(f"Error during Vector tiles package generation: {str(e)}")
            return {}

        # Return empty results dictionary (modify as needed for your use case)
        return {}
