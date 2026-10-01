"""
qgis2vectortiles.py

QGIS2VectorTiles — thin orchestrator class that drives the full conversion
pipeline from QGIS project styling to vector tiles:

  1. Flatten rules  (RulesFlattener)
  2. Export rules   (RulesExporter)
  3. Style tiles    (TilesStyler)
  4. Export MapLibre style (QgisMapLibreStyleExporter)
  5. Generate tiles (GDALTilesGenerator)
  6. Validate output and write the fidelity report
  7. Serve tiles via local HTTP server

Every stage reports fidelity problems to one ``DiagnosticCollector``. In
strict mode the export fails *before* publication (no viewer, no server) if
any component is unsupported or only approximated; the diagnostics are
written to ``fidelity_report.json`` / ``fidelity_report.html`` either way.
"""

import json
import traceback
from datetime import datetime
from os import makedirs, listdir
from os.path import join, exists
from shutil import rmtree
from time import perf_counter
from typing import Dict, List, Optional, Tuple
from uuid import uuid4

from qgis.utils import iface
from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsReadWriteContext,
    QgsVectorLayer,
    QgsVectorTileLayer,
    QgsProcessingFeedback,
    QgsProcessingUtils,
    QgsProcessingException,
    QgsMapToPixel,
    QgsRectangle,
    QgsRenderContext,
)
from qgis.PyQt.QtXml import QDomDocument

from .utils.config import _EPSG_CRS
from .utils import crash_log
from .utils.flattened_rule import FlattenedRule
from .utils.zoom_levels import ZoomLevels
from .core.fidelity import zoom as fidelity_zoom
from .core.fidelity.zoom import fit_zoom
from .core.rules_flattener import RulesFlattener
from .core.rules_exporter import RulesExporter
from .core.tiles_generator import GDALTilesGenerator
from .core.tiles_styler import TilesStyler
from .core.maplibre_converter import QgisMapLibreStyleExporter
from .core.server_initializer import ServerInitializer
from .core.fidelity import FIDELITY_SCHEMA_VERSION
from .core.fidelity.bindings import bindings_report
from .core.fidelity.diagnostics import (
    DiagnosticCollector, StrictModeError, render_html_report)
from .core.fidelity.model import ExportProfile, FidelityMode, OverzoomPolicy, ZoomInterval
from .core.fidelity.units import LengthConverter, MapUnitContext
from .core.fidelity.validation import (
    inspect_mbtiles, tile_layer_fields, validate_archive, validate_glyphs, validate_style)


class QGIS2VectorTiles:
    """Orchestrate the conversion from QGIS project styling to vector tiles."""

    def __init__(
        self,
        min_zoom: int = 0,
        max_zoom: int = 5,
        extent=None,
        output_dir: str = None,
        include_required_fields_only=0,
        cpu_percent: int = 100,
        cent_source: int = 2,
        background_type: int = 0,
        viewer: int = 0,
        feedback: QgsProcessingFeedback = None,
        fidelity_mode: int = 0,
        overzoom: int = 0,
        serve: bool = True,
        static_package: bool = False,
        parallel: bool = False,
    ):
        self.min_zoom = min_zoom - viewer
        self.max_zoom = max_zoom - viewer
        self.extent = extent or iface.mapCanvas().extent()
        self.utils_dir = self._get_utils_dir()
        self.output_dir = output_dir or self.utils_dir
        self.include_required_fields_only = include_required_fields_only
        self.cpu_percent = min(cpu_percent, 90)
        self.cent_source = cent_source
        self.background_type = background_type
        self.viewer = viewer
        self.feedback = feedback or QgsProcessingFeedback()
        self.serve = serve
        self.static_package = static_package
        self.parallel = parallel
        self.diagnostics = DiagnosticCollector()
        self.profile = ExportProfile(
            mode=FidelityMode.from_index(fidelity_mode),
            overzoom=[OverzoomPolicy.PERSIST, OverzoomPolicy.STOP][int(overzoom)],
            reference_latitude=self._reference_latitude(),
        )
        self.lengths = self._length_converter()
        # zoom <-> scale: QGIS's scale for the browser's ground resolution in
        # the project CRS (1 for Web Mercator, ~cos(latitude) for EOV etc.).
        ZoomLevels.configure(
            fidelity_zoom.WEB_MERCATOR_TOP_SCALE / self.lengths.map_context.mercator_per_map_unit)
        self.output_path: Optional[str] = None
        self._expected_zooms: Tuple[int, int] = (max(0, self.min_zoom), self.max_zoom)
        self.report: dict = {}

    def convert_project_to_vector_tiles(self) -> Optional[QgsVectorTileLayer]:
        """Run the full conversion pipeline; return the output directory or None."""
        temp_dir = None
        try:
            self._clear_project()
            fingerprint_before = self._project_style_fingerprint()
            temp_dir = self._create_temp_directory()
            crash_log.start(temp_dir, self._log_header())
            self._log(". Starting conversion process...")
            start_time = perf_counter()
            if self.profile.mode == FidelityMode.HYBRID:
                self.diagnostics.add("Q2VT_HYBRID_NOT_AVAILABLE")

            self._log(". Flattening rules...")
            self.feedback.setProgress(1)
            rules = self._flatten_rules()
            if not rules:
                self._log(". No visible vector layers found in project.")
                return None
            self._log(f". Successfully extracted {len(rules)} rules "
                      f"({self._elapsed_minutes(start_time)} minutes).")

            flatten_time = perf_counter()
            self._log(". Exporting rules to datasets...")
            self.feedback.setProgress(5)
            layers, rules = self._export_rules(rules)
            self._log(f". Successfully exported {len(layers)} layers "
                      f"({self._elapsed_minutes(flatten_time)} minutes).")

            self._log(". Styling tiles...")
            self.feedback.setProgress(70)
            styled_layer = self._style_tiles(rules, temp_dir)
            self._log(". Successfully styled tiles.")

            self._log(". Exporting tiles style to client-side style package...")
            self.feedback.setProgress(72)
            exporter = self._export_maplibre_style(temp_dir, styled_layer, rules)
            style = exporter.style
            self._log(". Successfully exported client-side style package.")
            validate_style(style, self.diagnostics,
                           sprite_names=exporter.sprite_names if "sprite" in style else [])
            validate_glyphs(style, join(temp_dir, "style", "glyphs"), self.diagnostics)
            self._enforce_strict(temp_dir)

            export_time = perf_counter()
            archive = None
            if self._has_features(layers):
                self._log(". Generating tiles...")
                self.feedback.setProgress(85)
                self._generate_tiles(layers, temp_dir, style, rules)
                self._log(f". Successfully generated tiles "
                          f"({self._elapsed_minutes(export_time)} minutes).")
                archive = self._validate_tiles(temp_dir, style, exporter.sprite_names)
            else:
                self.diagnostics.add("Q2VT_EXPORT_EMPTY")

            if self._project_style_fingerprint() != fingerprint_before:
                self.diagnostics.add("Q2VT_PROJECT_MUTATED")
            if archive is not None and self.static_package:
                self._write_static_package(temp_dir, style, exporter.source_name)
            self._write_report(temp_dir, style, archive, rules)
            self._enforce_strict(temp_dir)
            self.feedback.setProgress(100)
            self._log(f". Process completed successfully "
                      f"({self._elapsed_minutes(start_time)} minutes).")
            self._clear_project()
            self.output_path = temp_dir
            self.serve_tiles(temp_dir)
            return temp_dir

        except StrictModeError as e:
            self._clear_project()
            raise QgsProcessingException(str(e)) from e
        except QgsProcessingException as e:
            self._log(f". Processing failed: {str(e)}")
            self._clear_project()
            return None
        except BaseException:
            crash_log.note("Export failed:\n" + traceback.format_exc())
            raise
        finally:
            crash_log.stop()

    def _log_header(self) -> str:
        crs = QgsProject.instance().crs().authid()
        layers = len(QgsProject.instance().mapLayers())
        return (f"QGIS {Qgis.version()}, zooms {self.min_zoom + self.viewer}-"
                f"{self.max_zoom + self.viewer}, project CRS {crs}, {layers} layers, "
                f"extent {self.extent.toString(2)}, CPU limit {self.cpu_percent}%, "
                f"{'parallel' if self.parallel else 'serial'} export")

    # --- fidelity helpers -------------------------------------------------
    def _reference_latitude(self) -> float:
        """Latitude of the export extent centre (for map-unit sizes)."""
        try:
            transform = QgsCoordinateTransform(
                QgsCoordinateReferenceSystem(f"EPSG:{_EPSG_CRS}"),
                QgsCoordinateReferenceSystem("EPSG:4326"),
                QgsProject.instance().transformContext(),
            )
            return transform.transform(self.extent.center()).y()
        except Exception:  # noqa: BLE001 - extent may be unset in tests
            return 0.0

    def _length_converter(self) -> LengthConverter:
        crs = QgsProject.instance().crs()
        is_mercator = crs.authid() in ("EPSG:3857", "EPSG:900913")
        units = "degrees" if crs.isGeographic() else getattr(crs.mapUnits(), "name", "meters")
        context = MapUnitContext.for_project(is_mercator, str(units), self.profile.reference_latitude)
        if not context.exact:
            self.diagnostics.add(
                "Q2VT_UNIT_MAP_UNITS_APPROX",
                f"{context.description} (project CRS {crs.authid() or 'unknown'}).")
        return LengthConverter(self.profile.reference_dpi, context)

    def _enforce_strict(self, temp_dir: str):
        """Fail before publication in strict mode; remove only the new output."""
        if not self.profile.strict:
            return
        violations = self.diagnostics.strict_violations()
        if not violations:
            return
        self.diagnostics.add("Q2VT_STRICT_FAILED",
                             f"{len(violations)} component(s) cannot be reproduced exactly.")
        self._write_report(temp_dir, {}, None, [])
        for entry in ("tiles.mbtiles", "style", "utils"):
            path = join(temp_dir, entry)
            if exists(path):
                if entry.endswith(".mbtiles"):
                    from os import remove  # pylint: disable=import-outside-toplevel
                    remove(path)
                else:
                    rmtree(path, ignore_errors=True)
        self.diagnostics.enforce_strict()

    def _validate_tiles(self, temp_dir: str, style: dict, sprite_names) -> Optional[dict]:
        path = join(temp_dir, "tiles.mbtiles")
        if not exists(path):
            return None
        wanted = {layer["source-layer"] for layer in style.get("layers", [])
                  if layer.get("source-layer")}
        archive = inspect_mbtiles(path, wanted)
        validate_archive(archive, self.diagnostics, self._expected_zooms[0],
                         self._expected_zooms[1])
        validate_style(style, self.diagnostics, sprite_names=None,
                       tile_layers=tile_layer_fields(archive),
                       complete=archive.get("complete", True))
        return archive

    def _project_style_fingerprint(self) -> Dict[str, str]:
        """Serialized renderer + labeling of every project vector layer."""
        fingerprint = {}
        for layer in QgsProject.instance().mapLayers().values():
            if not isinstance(layer, QgsVectorLayer):
                continue
            doc = QDomDocument("q2vt")
            root = doc.createElement("style")
            doc.appendChild(root)
            layer.writeSymbology(root, doc, "", QgsReadWriteContext())
            fingerprint[layer.id()] = doc.toString()
        return fingerprint

    def _write_report(self, temp_dir: str, style: dict, archive: Optional[dict],
                      rules: List[FlattenedRule]):
        self.report = {
            "schema_version": FIDELITY_SCHEMA_VERSION,
            "mode": self.profile.mode.value,
            "overzoom": self.profile.overzoom.value,
            "requested_zooms": [self.min_zoom + self.viewer, self.max_zoom + self.viewer],
            "environment": self._environment(),
            "style_layers": len(style.get("layers", [])),
            "rules": len(rules),
            "bindings": bindings_report(style),
        }
        if archive:
            self.report["archive"] = {
                "minzoom": archive.get("minzoom"), "maxzoom": archive.get("maxzoom"),
                "tile_counts": archive.get("tile_counts"),
                "largest_tile_bytes": archive.get("largest_tile_bytes"),
                "source_layers": len(archive.get("vector_layers", {})),
            }
        payload = json.loads(self.diagnostics.to_json(redact=True, extra=self.report))
        with open(join(temp_dir, "fidelity_report.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        with open(join(temp_dir, "fidelity_report.html"), "w", encoding="utf-8") as f:
            f.write(render_html_report(payload))
        counts = payload["counts"]
        self._log(f". Fidelity report: {counts['error']} errors, {counts['warning']} warnings, "
                  f"{counts['info']} notes (fidelity_report.html).")
        for diag in self.diagnostics.items:
            if diag.severity.value == "error":
                self.feedback.reportError(f"{diag.code}: {diag.message}")
            elif diag.severity.value == "warning":
                self.feedback.pushWarning(f"{diag.code}: {diag.message}")

    @staticmethod
    def _environment() -> dict:
        env = {"qgis": Qgis.version()}
        try:
            from osgeo import gdal  # pylint: disable=import-outside-toplevel
            env["gdal"] = gdal.__version__
        except ImportError:
            pass
        try:
            from qgis.PyQt.QtCore import QT_VERSION_STR  # pylint: disable=import-outside-toplevel
            env["qt"] = QT_VERSION_STR
        except ImportError:
            pass
        return env

    # --- pipeline stages --------------------------------------------------
    def _clear_project(self):
        """Remove all map layers not visible in the project legend."""
        legend_ids = {
            node.layer().id()
            for node in QgsProject.instance().layerTreeRoot().findLayers()
        }
        for layer_id in list(QgsProject.instance().mapLayers()):
            if layer_id not in legend_ids:
                QgsProject.instance().removeMapLayer(layer_id)

    def _get_utils_dir(self) -> str:
        """Remove this plugin's old working directories and create a fresh one.

        Only ``q2styledtiles_*`` folders: the rest of the Processing temp
        folder holds other tools' outputs, such as temporary layers that are
        still open in the project.
        """
        for entry in listdir(QgsProcessingUtils.tempFolder()):
            if not entry.startswith("q2styledtiles_"):
                continue
            try:
                rmtree(join(QgsProcessingUtils.tempFolder(), entry))
            except OSError:
                continue
        utils_dir = join(QgsProcessingUtils.tempFolder(), f"q2styledtiles_{uuid4().hex}")
        makedirs(utils_dir, exist_ok=True)
        return utils_dir

    def _create_temp_directory(self) -> str:
        temp_dir = join(self.output_dir, datetime.now().strftime("%d_%m_%Y_%H_%M_%S_%f"))
        makedirs(temp_dir, exist_ok=True)
        return temp_dir

    def _flatten_rules(self) -> List[FlattenedRule]:
        return RulesFlattener(
            self.min_zoom, self.max_zoom, self.utils_dir, self.feedback, self.diagnostics
        ).flatten_all_rules()

    # Largest symbol reach considered for the extent buffer (CSS px).
    _MAX_REACH_PX = 256.0

    @staticmethod
    def _symbol_reach_px(symbol, context) -> float:
        """How far (px) a symbol can draw beyond its feature's geometry."""
        reach = 0.0
        try:
            if symbol.type() == Qgis.SymbolType.Marker:
                # Half the diagonal of the marker box plus its offset
                # (QgsSymbol.bounds needs a started render and can crash).
                for layer in symbol.symbolLayers():
                    size = context.convertToPainterUnits(layer.size(), layer.sizeUnit(),
                                                         layer.sizeMapUnitScale())
                    offset = layer.offset()
                    shift = context.convertToPainterUnits(
                        max(abs(offset.x()), abs(offset.y())), layer.offsetUnit(),
                        layer.offsetMapUnitScale())
                    reach = max(reach, size * 0.7072 + shift)
            for layer in symbol.symbolLayers():
                reach = max(reach, float(layer.estimateMaxBleed(context)))
                sub = layer.subSymbol()
                if sub is not None and layer.layerType() not in ("GeometryGenerator",):
                    reach = max(reach, QGIS2VectorTiles._symbol_reach_px(sub, context))
        except (AttributeError, RuntimeError, TypeError):
            pass
        return reach

    def _extent_with_symbol_reach(self, rules: List[FlattenedRule]) -> QgsRectangle:
        """Export extent grown by the widest symbol reach at the minimum zoom.

        Features just outside the extent whose markers, strokes or offsets
        reach into it are kept, so edge tiles look like QGIS.
        """
        scale = fidelity_zoom.zoom_to_scale(self.min_zoom)
        context = QgsRenderContext()
        context.setScaleFactor(96.0 / 25.4)
        context.setRendererScale(scale)
        metres_per_px = scale * 0.0254 / 96.0 / self.lengths.map_context.mercator_per_map_unit
        context.setMapToPixel(QgsMapToPixel(metres_per_px))
        reach = 0.0
        for rule in rules:
            symbol = rule.rule.symbol() if rule.get_attr("t") == 0 else None
            if symbol is not None:
                reach = max(reach, self._symbol_reach_px(symbol, context))
        # Mercator metres per CSS px at the minimum zoom (512 px tiles).
        buffer = min(reach, self._MAX_REACH_PX) * 40075016.68557849 / (512.0 * 2 ** self.min_zoom)
        buffer = min(buffer, 0.25 * max(self.extent.width(), self.extent.height()))
        extent = QgsRectangle(self.extent)
        if buffer > 0:
            extent.grow(buffer)
        return extent

    def _export_rules(self, rules: List[FlattenedRule]):
        return RulesExporter(
            rules, self._extent_with_symbol_reach(rules), self.include_required_fields_only,
            self.max_zoom, self.utils_dir, self.cent_source, self.feedback,
            cpu_percent=self.cpu_percent, diagnostics=self.diagnostics,
            progress_range=(5.0, 70.0), parallel=self.parallel,
        ).export()

    def _has_features(self, layers: List[QgsVectorLayer]) -> bool:
        return any(layer.featureCount() > 0 for layer in layers)

    @staticmethod
    def _layer_zooms(rules: List[FlattenedRule]) -> Dict[str, Tuple[int, int]]:
        """Explicit tile zoom range per output dataset (union over its rules)."""
        zooms: Dict[str, Tuple[int, int]] = {}
        for rule in rules:
            low, high = rule.get_attr("o"), rule.get_attr("i")
            if rule.output_dataset in zooms:
                old_low, old_high = zooms[rule.output_dataset]
                low, high = min(low, old_low), max(high, old_high)
            zooms[rule.output_dataset] = (low, high)
            polygons = getattr(rule, "visible_polygons", None)
            if polygons:  # a visible-polygon label's polygons: the label's zooms
                old_low, old_high = zooms.get(polygons, (low, high))
                zooms[polygons] = (min(low, old_low), max(high, old_high))
        return zooms

    def _generate_tiles(self, layers: List[QgsVectorLayer], temp_dir: str, style: dict,
                        rules: Optional[List[FlattenedRule]] = None) -> str:
        layer_zooms = self._layer_zooms(rules or [])
        present = [layer_zooms[n] for n in (layer.name() for layer in layers) if n in layer_zooms]
        if present:
            self._expected_zooms = (min(z[0] for z in present), max(z[1] for z in present))
        tiles_uri, min_zoom = GDALTilesGenerator(
            layers, style, temp_dir, self.extent, self.cpu_percent, self.feedback,
            layer_zooms=layer_zooms,
        ).generate()
        self.min_zoom = min_zoom
        return tiles_uri

    def _style_tiles(self, rules, temp_dir) -> Optional[QgsVectorTileLayer]:
        return TilesStyler(rules, temp_dir).apply_styling()

    @staticmethod
    def _visibility_by_style(rules: List[FlattenedRule]) -> Dict[str, ZoomInterval]:
        return {
            rule.rule.description(): rule.visibility
            for rule in rules if rule.visibility is not None
        }

    def _export_maplibre_style(self, temp_dir, styled_layer, rules=None):
        exporter = QgisMapLibreStyleExporter(
            temp_dir, self.utils_dir, styled_layer, self.background_type, self.viewer,
            self.min_zoom, self.max_zoom,
            diagnostics=self.diagnostics, profile=self.profile,
            visibility=self._visibility_by_style(rules or []),
            visible_polygons={rule.rule.description(): (rule.visible_polygons,
                                                        rule.label_per_part)
                              for rule in rules or [] if getattr(rule, "visible_polygons", None)},
            lengths=self.lengths,
            ordered_styles={rule.rule.description() for rule in rules or []
                            if RulesExporter._order_by(rule.layer)},
        )
        exporter.export()
        return exporter

    def _log(self, message: str):
        crash_log.note(message)
        if __name__ != "__console__":
            self.feedback.pushInfo(message)
        else:
            print(message)

    def _elapsed_minutes(self, start: float) -> str:
        """Return elapsed time in minutes since start, rounded to 2 decimal places."""
        return f"{round((perf_counter() - start) / 60, 2)}"

    def _write_static_package(self, temp_dir: str, style: dict, source_name: str) -> None:
        """``web/``: static XYZ tiles + relative-URL style + viewer (any web server)."""
        from os.path import dirname, abspath  # pylint: disable=import-outside-toplevel
        from .core.publisher import write_static_package  # pylint: disable=import-outside-toplevel
        viewer_dir = join(dirname(dirname(abspath(__file__))), "resources", "ml_viewer")
        transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem(f"EPSG:{_EPSG_CRS}"),
            QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance().transformContext())
        center = transform.transform(self.extent.center())
        zoom = fit_zoom(self.extent.width(), self.extent.height(),
                        max(0, self.min_zoom), self.max_zoom)  # MapLibre zooms of the tiles
        path = write_static_package(temp_dir, style, source_name, viewer_dir,
                                    (center.x(), center.y()), zoom)
        self._log(f". Static web package: {path}")

    def serve_tiles(self, temp_dir: str):
        """Serve the generated tiles via a local HTTP server."""
        ServerInitializer(self.extent, self.min_zoom, self.viewer, temp_dir,
                          max_zoom=self.max_zoom + self.viewer).serve_tiles(launch=self.serve)

if __name__ == "__console__":
    adapter = QGIS2VectorTiles(output_dir=QgsProcessingUtils.tempFolder())
    adapter.convert_project_to_vector_tiles()
