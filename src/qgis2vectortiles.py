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
import queue
import threading
import traceback
from dataclasses import dataclass
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
from .utils import crash_log, main_thread
from .utils.flattened_rule import FlattenedRule
from .utils.zoom_levels import ZoomLevels
from .core.fidelity import zoom as fidelity_zoom
from .core.fidelity.zoom import fit_zoom
from .core.rules_flattener import RulesFlattener
from .core.rules_exporter import RulesExporter
from .core.tiles_generator import GDALTilesGenerator
from .core import export_cache
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


class _ThreadFeedback:
    """The tile generator's feedback while it runs in the background: its
    messages reach the real feedback on the main thread (BackgroundTiles.
    flush); its progress is not shown (other stages own the bar then)."""

    def __init__(self, job: "BackgroundTiles"):
        self.job = job

    def setProgress(self, value):  # noqa: N802
        self.job.progress = value  # shown by wait()

    def pushInfo(self, text):  # noqa: N802
        self.job.messages.put(("pushInfo", text))

    def reportError(self, text, fatalError=False):  # noqa: N802,N803
        self.job.messages.put(("reportError", text))

    def isCanceled(self):  # noqa: N802
        return self.job.cancelled.is_set()


class BackgroundTiles:
    """GDALTilesGenerator.generate() in a thread: its ogr2ogr processes make
    the tiles while the main thread goes on; the thread only starts and
    polls them (and merges their tiles at the end)."""

    def __init__(self, generator: GDALTilesGenerator):
        self.generator = generator
        self.messages: "queue.Queue" = queue.Queue()
        self.cancelled = threading.Event()
        self.result = None
        self.error: Optional[BaseException] = None
        self.progress = 0.0
        generator.feedback = _ThreadFeedback(self)
        generator.progress_range = (0.0, 100.0)  # a stage of its own while waited for
        self.thread = threading.Thread(target=self._run, name="q2vt-tiles", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            self.result = self.generator.generate()
        except BaseException as error:  # noqa: BLE001 - raised again by wait()
            self.error = error

    def flush(self, feedback) -> None:
        while True:
            try:
                kind, text = self.messages.get_nowait()
            except queue.Empty:
                return
            if feedback is not None:
                getattr(feedback, kind)(text)

    def wait(self, feedback):
        """(tiles uri, min zoom) once done; Cancel on ``feedback`` stops it."""
        while self.thread.is_alive():
            self.thread.join(0.1)
            self.flush(feedback)
            if feedback is not None:
                feedback.setProgress(self.progress)
                if feedback.isCanceled():
                    self.cancelled.set()
            main_thread.keep_responsive()
        self.flush(feedback)
        if self.error is not None:
            raise self.error
        return self.result

    def abort(self) -> None:
        """Stop the ogr2ogr processes and the thread (cancelled or failed export)."""
        self.cancelled.set()
        self.thread.join(60)


@dataclass
class _Conversion:
    """What completing a conversion needs (after its tiles)."""
    temp_dir: str
    start_time: float
    export_time: float
    has_features: bool
    job: Optional[BackgroundTiles] = None


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
        layer_ids=None,
        archive_format: str = "mbtiles",
        add_result_layer: bool = True,
        feature_keys=None,
        extra_tile_fields=None,
        cache=None,
        scale_limits=None,
        background_tiles: bool = False,
        fast_markers: bool = False,
    ):
        """``cache``: a core.export_cache.ExportCache — datasets and tiles of
        layers unchanged since an earlier export are reused (None: off, as
        before). ``layer_ids``: export exactly these vector layers (also hidden
        ones; the layer tree is not touched); None = the visible layers, as
        before. ``archive_format``: "mbtiles" (default, as before), "pmtiles"
        or "both" (the same MVT tiles repackaged as PMTiles v3).
        ``add_result_layer``: add the "Vector Tiles" result layer and the
        local viewer files (the publishing workflow turns this off so the
        project is not changed). ``feature_keys`` ({layer id: QGIS
        expression}) and ``extra_tile_fields`` ({layer id: [field]}) are the
        publishing identity/filter fields (see RulesExporter).
        ``background_tiles``: convert_project_to_vector_tiles() returns once
        the tile processes are started; finish_tiles() waits for them and
        completes the export (the publishing workflow goes on meanwhile)."""
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
        self.layer_ids = None if layer_ids is None else set(layer_ids)
        # {layer id: (min scale, max scale)}: extra web-only scale range.
        self.scale_limits = dict(scale_limits or {})
        if archive_format not in ("mbtiles", "pmtiles", "both"):
            raise ValueError(f"archive_format must be mbtiles, pmtiles or both, not {archive_format!r}")
        self.archive_format = archive_format
        self.add_result_layer = add_result_layer
        self.feature_keys = dict(feature_keys or {})
        self.extra_tile_fields = dict(extra_tile_fields or {})
        self.cache = cache
        self.background_tiles = background_tiles
        # Screen-size interval marker lines placed by the browser (faster, approximate).
        self.fast_markers = fast_markers
        self._pending: Optional[_Conversion] = None
        self.dataset_keys: Dict[str, str] = {}
        # Results of the last run (the publishing workflow builds its
        # ExportBundle from them; see export_bundle()).
        self.rules: List[FlattenedRule] = []
        self.style: dict = {}
        self.style_exporter = None
        self.pmtiles = None
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
        self.layer_seconds: Dict[str, Dict[str, float]] = {}
        self.tile_pieces: Dict[str, int] = {}  # ogr2ogr jobs per layer id
        self.export_folder = ""  # this run's folder (also when it fails)

    def convert_project_to_vector_tiles(self) -> Optional[QgsVectorTileLayer]:
        """Run the full conversion pipeline; return the output directory or None.
        With ``background_tiles`` the tiles are still being made when this
        returns: finish_tiles() completes the export."""
        temp_dir = None
        background = False
        try:
            self._clear_project()
            fingerprint_before = self._project_style_fingerprint()
            temp_dir = self._create_temp_directory()
            self.export_folder = temp_dir
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

            # Shares of the bar: tile generation takes most of the time.
            self._log(". Styling tiles...")
            self.feedback.setProgress(self._bar(30))
            styled_layer = self._style_tiles(rules, temp_dir)
            self._log(". Successfully styled tiles.")

            self._log(". Exporting tiles style to client-side style package...")
            self.feedback.setProgress(self._bar(32))
            exporter = self._export_maplibre_style(temp_dir, styled_layer, rules)
            style = exporter.style
            self._log(". Successfully exported client-side style package.")
            validate_style(style, self.diagnostics,
                           sprite_names=exporter.sprite_names if "sprite" in style else [])
            validate_glyphs(style, join(temp_dir, "style", "glyphs"), self.diagnostics)
            self._enforce_strict(temp_dir)

            # The tiles do not touch the project: checked before they are made.
            if self._project_style_fingerprint() != fingerprint_before:
                self.diagnostics.add("Q2VT_PROJECT_MUTATED")
            self.rules, self.style, self.style_exporter = rules, style, exporter
            state = _Conversion(temp_dir, start_time, perf_counter(), self._has_features(layers))
            if state.has_features:
                self._log(". Generating tiles...")
                self.feedback.setProgress(self._bar(33))
                if self.background_tiles:
                    state.job = BackgroundTiles(self._tiles_generator(layers, temp_dir, style, rules))
                    self._pending, self.output_path, background = state, temp_dir, True
                    return temp_dir
                self._generate_tiles(layers, temp_dir, style, rules)
            return self._complete(state)

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
            if not background:
                crash_log.stop()

    def _bar(self, value: float) -> float:
        """A point of the progress bar before the tiles: with background tiles
        the bar of this run ends where they start (their bar is their own)."""
        return value if not self.background_tiles else 5.0 + (value - 5.0) * 95.0 / 28.0

    def _complete(self, state: _Conversion) -> str:
        """Validate the tiles, package and report: the end of a conversion."""
        temp_dir, style, exporter = state.temp_dir, self.style, self.style_exporter
        archive = None
        if state.has_features:
            self._log(f". Successfully generated tiles "
                      f"({self._elapsed_minutes(state.export_time)} minutes).")
            archive = self._validate_tiles(temp_dir, style, exporter.sprite_names)
        else:
            self.diagnostics.add("Q2VT_EXPORT_EMPTY")
        if archive is not None and self.static_package:
            self._write_static_package(temp_dir, style, exporter.source_name)
        if archive is not None and self.archive_format in ("pmtiles", "both"):
            self._write_pmtiles(temp_dir)
        self._write_report(temp_dir, style, archive, self.rules)
        self._enforce_strict(temp_dir)
        self.feedback.setProgress(100)
        self._log(f". Process completed successfully "
                  f"({self._elapsed_minutes(state.start_time)} minutes).")
        self._clear_project()
        self.output_path = temp_dir
        if self.archive_format == "pmtiles" and self.pmtiles is not None:
            # QGIS cannot open PMTiles: no result layer / MBTiles viewer.
            self._remove_mbtiles(temp_dir)
        elif self.add_result_layer:
            self.serve_tiles(temp_dir)
        return temp_dir

    def finish_tiles(self) -> Optional[str]:
        """``background_tiles``: wait for the tiles, then complete the export
        as convert_project_to_vector_tiles() does without it; the export
        folder, or None when it failed."""
        state, self._pending = self._pending, None
        if state is None:
            return self.output_path
        try:
            self._log(". Waiting for the tiles..." if state.job.thread.is_alive() else ". Tiles ready.")
            _, min_zoom = state.job.wait(self.feedback)
            self._tiles_done(state.job.generator, min_zoom)
            return self._complete(state)
        except StrictModeError as e:
            self._clear_project()
            raise QgsProcessingException(str(e)) from e
        except QgsProcessingException as e:
            self._log(f". Processing failed: {str(e)}")
            self._clear_project()
            self.output_path = None
            return None
        except BaseException:
            crash_log.note("Export failed:\n" + traceback.format_exc())
            raise
        finally:
            crash_log.stop()

    def abort_tiles(self) -> None:
        """Stop background tiles that are no longer wanted (no-op otherwise)."""
        state, self._pending = self._pending, None
        if state is not None:
            state.job.abort()
            crash_log.note("Export stopped before its tiles were finished.")
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
            self.min_zoom, self.max_zoom, self.utils_dir, self.feedback, self.diagnostics,
            layer_ids=self.layer_ids, scale_limits=self.scale_limits, extent=self.extent,
            fast_markers=self.fast_markers, lengths=self.lengths,
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
        exporter = RulesExporter(
            rules, self._extent_with_symbol_reach(rules), self.include_required_fields_only,
            self.max_zoom, self.utils_dir, self.cent_source, self.feedback,
            cpu_percent=self.cpu_percent, diagnostics=self.diagnostics,
            progress_range=(5.0, self._bar(30)), parallel=self.parallel,
            feature_keys=self.feature_keys, extra_tile_fields=self.extra_tile_fields,
            cache=self.cache, light_results=True,
        )
        result = exporter.export()
        self.dataset_keys = dict(exporter.dataset_keys)
        self.layer_seconds = {"datasets": dict(exporter.layer_seconds)}
        return result

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

    def _tiles_generator(self, layers: List[QgsVectorLayer], temp_dir: str, style: dict,
                         rules: Optional[List[FlattenedRule]] = None) -> GDALTilesGenerator:
        layer_zooms = self._layer_zooms(rules or [])
        present = [layer_zooms[n] for n in (layer.name() for layer in layers) if n in layer_zooms]
        if present:
            self._expected_zooms = (min(z[0] for z in present), max(z[1] for z in present))
        generator = GDALTilesGenerator(
            layers, style, temp_dir, self.extent, self.cpu_percent, self.feedback,
            layer_zooms=layer_zooms, cache=self.cache, dataset_keys=self.dataset_keys,
            layer_groups=export_cache.source_layer_groups(rules or []),
            progress_range=(33.0, 97.0),
        )
        self.min_zoom = generator._get_global_min_zoom()  # pylint: disable=protected-access
        return generator

    def _tiles_done(self, generator: GDALTilesGenerator, min_zoom: int) -> None:
        if self.cache is not None:
            self._log(f". Export {self.cache.summary()}.")
        self.layer_seconds["tiles"] = dict(generator.layer_seconds)
        self.tile_pieces = dict(generator.layer_pieces)
        self._log_slowest_layers()
        self.min_zoom = min_zoom

    def _generate_tiles(self, layers: List[QgsVectorLayer], temp_dir: str, style: dict,
                        rules: Optional[List[FlattenedRule]] = None) -> str:
        generator = self._tiles_generator(layers, temp_dir, style, rules)
        tiles_uri, min_zoom = generator.generate()
        self._tiles_done(generator, min_zoom)
        return tiles_uri

    def _log_slowest_layers(self, count: int = 8) -> None:
        """The layers that took longest (their datasets, their tiles): where
        to look first when an export is slow. A layer's tiles made in pieces
        side by side count every piece's seconds."""
        datasets = self.layer_seconds.get("datasets", {})
        tiles = self.layer_seconds.get("tiles", {})
        total = {lid: datasets.get(lid, 0.0) + tiles.get(lid, 0.0) for lid in set(datasets) | set(tiles)}
        slowest = sorted((lid for lid in total if total[lid] >= 1.0), key=lambda lid: -total[lid])[:count]
        if not slowest:
            return
        project = QgsProject.instance()

        def name(lid):
            layer = project.mapLayer(lid)
            return layer.name() if layer is not None else lid
        def pieces(lid):
            count = self.tile_pieces.get(lid, 0)
            return f" in {count} parallel pieces" if count > 1 else ""
        self._log(". Slowest layers: " + "; ".join(
            f"{name(lid)} {total[lid]:.0f} s (datasets {datasets.get(lid, 0.0):.0f} s, "
            f"tiles {tiles.get(lid, 0.0):.0f} s{pieces(lid)})" for lid in slowest))

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
                                                        rule.label_per_part,
                                                        getattr(rule, "visible_kind", "polygon"))
                              for rule in rules or [] if getattr(rule, "visible_polygons", None)},
            lengths=self.lengths,
            ordered_styles={rule.rule.description() for rule in rules or []
                            if RulesExporter._order_by(rule.layer)},
            heatmaps={rule.rule.description(): rule.heatmap for rule in rules or [] if rule.heatmap},
            translates={rule.rule.description(): rule.translate
                        for rule in rules or [] if getattr(rule, "translate", None)},
            effect_roles={rule.rule.description(): rule.effect_role
                          for rule in rules or [] if getattr(rule, "effect_role", None)},
            inner_effects={rule.rule.description(): rule.inner_effect
                           for rule in rules or [] if getattr(rule, "inner_effect", None)},
            z_orders={rule.rule.description(): rule.z_order
                      for rule in rules or [] if getattr(rule, "z_order", None)},
            feature_filters={rule.rule.description(): rule.feature_filter
                             for rule in rules or [] if getattr(rule, "feature_filter", None)},
            label_windows={rule.rule.description() for rule in rules or []
                           if rule.recipe is not None and rule.recipe.kind == "label_windows"},
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

    def _write_pmtiles(self, temp_dir: str) -> None:
        """``tiles.pmtiles``: the validated MBTiles tiles repackaged (PUB-05)."""
        from .publishing.pmtiles_builder import build_pmtiles  # pylint: disable=import-outside-toplevel
        self._log(". Packaging the vector tiles as PMTiles...")
        self.pmtiles = build_pmtiles(join(temp_dir, "tiles.mbtiles"), join(temp_dir, "tiles.pmtiles"),
                                     feedback=self.feedback)
        for warning in self.pmtiles.warnings:
            self._log(f". PMTiles: {warning}")
        self._log(f". PMTiles archive: tiles.pmtiles ({self.pmtiles.addressed_tiles} tiles, "
                  f"{self.pmtiles.size_bytes} bytes, validated).")

    def _remove_mbtiles(self, temp_dir: str) -> None:
        from os import remove  # pylint: disable=import-outside-toplevel
        try:
            remove(join(temp_dir, "tiles.mbtiles"))
            self._log(". PMTiles only: tiles.mbtiles removed (QGIS cannot open PMTiles; "
                      "choose 'Both' to keep the QGIS result layer).")
        except OSError as error:
            self._log(f". Could not remove tiles.mbtiles: {error}")

    def export_bundle(self, publication_id: str = ""):
        """The private ExportBundle of the last successful run (paths, style,
        zooms, view). The logical layer/rule model is added by
        ``publishing.qgis_model``."""
        from .publishing.models import ExportBundle  # pylint: disable=import-outside-toplevel
        if not self.output_path:
            raise RuntimeError("No successful export to describe.")
        transform = QgsCoordinateTransform(
            QgsCoordinateReferenceSystem(f"EPSG:{_EPSG_CRS}"),
            QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance().transformContext())
        box = transform.transformBoundingBox(self.extent)
        center = transform.transform(self.extent.center())
        zoom = fit_zoom(self.extent.width(), self.extent.height(),
                        max(0, self.min_zoom), self.max_zoom)
        style_dir = join(self.output_path, "style")
        return ExportBundle(
            export_dir=self.output_path,
            mbtiles_path=join(self.output_path, "tiles.mbtiles"),
            style_path=join(style_dir, "style.json"),
            style=self.style,
            source_name=getattr(self.style_exporter, "source_name", "q2vt_tiles"),
            sprite_dir=join(style_dir, "sprite") if exists(join(style_dir, "sprite")) else "",
            glyphs_dir=join(style_dir, "glyphs") if exists(join(style_dir, "glyphs")) else "",
            report_path=join(self.output_path, "fidelity_report.json"),
            log_path=join(self.output_path, "export_log.txt"),
            publication_id=publication_id,
            runtime_versions=self._environment(),
            bounds_wgs84=(box.xMinimum(), box.yMinimum(), box.xMaximum(), box.yMaximum()),
            view={"center": [center.x(), center.y()], "zoom": zoom,
                  "minZoom": max(0, self.min_zoom), "maxZoom": self.max_zoom},
            tile_zooms=self._expected_zooms,
            diagnostics_summary=self.diagnostics.counts(),
        )

    def serve_tiles(self, temp_dir: str):
        """Serve the generated tiles via a local HTTP server."""
        ServerInitializer(self.extent, self.min_zoom, self.viewer, temp_dir,
                          max_zoom=self.max_zoom + self.viewer).serve_tiles(launch=self.serve)

if __name__ == "__console__":
    adapter = QGIS2VectorTiles(output_dir=QgsProcessingUtils.tempFolder())
    adapter.convert_project_to_vector_tiles()
