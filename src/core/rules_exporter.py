"""
rules_exporter.py — Production-grade exporter for FlattenedRules.

Architecture
============
This module exports a large number of FlattenedRules to GeoParquet datasets
in a way that respects QGIS's strict thread-affinity rules and parallelises
*only* the work that is genuinely thread-safe to parallelise.

The legacy implementation crashed with native access violations on Windows
and suffered from "stuck task" deadlocks. The root causes were:

  1.  Live QgsVectorLayer objects (especially Postgres-backed) were passed
      from the main thread into worker QgsTask threads, then handed to
      processing.run(...). QObject thread-affinity rules forbid this; the
      Postgres provider in particular is not safe under cross-thread access
      and will eventually corrupt its connection state, producing access
      violations somewhere deep inside libpq.

  2.  Each worker called QgsProject.instance().createExpressionContext(),
      reaching into a main-thread QObject from a background thread.

  3.  QgsTask.run() was not returning a bool, and Python exceptions raised
      inside run() left tasks in inconsistent states. That is the
      "clock-icon, never starts" stuck-task symptom.

  4.  The polling loop with QCoreApplication.processEvents() introduced
      re-entrancy hazards when export() was itself running inside a
      QgsProcessingAlgorithm.

The redesign separates the export pipeline into clearly typed phases:

   ┌──────────────────────────────────────────────────────────────────────┐
   │ Phase 0 — Caller-thread snapshot                                     │
   │   * Mutate rule symbols / labeling settings (resolve @map_scale).    │
   │   * Capture every QObject we need into plain-Python data.            │
   │   * After this phase NO QObject crosses a thread boundary.           │
   ├──────────────────────────────────────────────────────────────────────┤
   │ Phase 1 — Source materialisation (SERIAL, caller thread)             │
   │   * For each unique source layer (Postgres or otherwise), construct  │
   │     a fresh QgsVectorLayer FROM URI inside this single thread, then  │
   │     dump it to a local Parquet file via fixgeometries(METHOD=0).     │
   │   * Postgres / remote providers are not parallel-safe; we never read │
   │     more than one source concurrently.                               │
   ├──────────────────────────────────────────────────────────────────────┤
   │ Phase 2 — Base-layer pipeline (PARALLEL, file → file)                │
   │   * For each materialised source: fixgeometries(METHOD=1) →          │
   │     reproject → clip → singleparts → simplify.                       │
   │   * All inputs and outputs are file paths; no live layers cross      │
   │     threads.                                                         │
   ├──────────────────────────────────────────────────────────────────────┤
   │ Phase 3 — Rule export (PARALLEL, file → file)                        │
   │   * For each rule group: optional filter → refactor fields →         │
   │     geometry transform → remove nulls → singleparts.                 │
   │   * Inputs are base-layer file paths and pure-data RuleGroupSnapshot │
   │     objects. No QObject access.                                      │
   ├──────────────────────────────────────────────────────────────────────┤
   │ Phase 4 — Result collection (caller thread)                          │
   │   * Wrap output Parquet files in QgsVectorLayer for the caller.      │
   │   * Feature-order strata of overlapping features (style only).       │
   │   * Cleanup of temp files.                                           │
   └──────────────────────────────────────────────────────────────────────┘

Phases 2 and 3 use a concurrent.futures.ThreadPoolExecutor rather than
QgsTask. This is intentional:

   * Predictable lifecycle: futures complete or raise — no "clock-icon"
     limbo state.
   * Per-future timeouts prevent any single hung algorithm from stalling
     the whole export.
   * Cancellation is a single shared-flag check between processing calls.
   * No QGIS task-manager re-entrancy with the parent processing
     algorithm.
   * No QCoreApplication.processEvents() polling loop.
"""

import json
import math
import dataclasses
import os
import queue
import re
import threading
import time
import traceback
import platform
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from os.path import exists, join
from typing import Any, Dict, Iterator, List, Optional, Tuple
from uuid import uuid4
from qgis.PyQt.QtCore import QVariant
from processing import run as run_processing
from qgis.core import (
    Qgis,
    QgsApplication,
    QgsCoordinateReferenceSystem,
    QgsExpressionContext,
    QgsExpression,
    QgsExpressionContextScope,
    QgsExpressionContextUtils,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsProcessingException,
    QgsFeatureRequest,
    QgsField,
    QgsGeometry,
    QgsMemoryProviderUtils,
    QgsPalLayerSettings,
    QgsProperty,
    QgsRectangle,
    QgsRuleBasedRenderer,
    QgsCoordinateTransform,
    QgsVectorFileWriter,
    QgsVectorLayer,
    QgsWkbTypes,
    QgsProject,
)

from ..utils import crash_log, main_thread
from ..utils.config import _DATA_SIMPLIFICATION_TOLERANCE, _EPSG_CRS, _FIELD_PREFIX
from ..utils.flattened_rule import FlattenedRule
from ..utils.zoom_levels import ZoomLevels
from .ddp_fetcher import DataDefinedPropertiesFetcher
from . import export_cache
from .datasets import DatasetInfo, ExportedDataset, gpkg_info
from . import label_lines
from . import marker_points
from .fidelity.diagnostics import DiagnosticCollector, Severity
from .fidelity import feature_order as fo
from .fidelity.qgis_expr import bind_geometry, in_layer_crs, substitute_geometry, with_map_scale
from .fidelity import html_labels
from .fidelity import materialize as mat
from .materializer import pattern_anchor_kind
from .fidelity.materialize import Recipe

def _enum_value(value) -> int:
    return int(getattr(value, "value", value))


# Drawing rank of each feature under the renderer's order-by clauses.
ORDER_FIELD = f"{_FIELD_PREFIX}_draw_order"
# Stable original-feature key of published layers (string; see
# publishing/identifiers.py), carried through every derived dataset.
FEATURE_KEY_FIELD = f"{_FIELD_PREFIX}_feature_key"
# Data-defined label position of a callout leader (layer CRS).
CALLOUT_X_FIELD = f"{_FIELD_PREFIX}_callout_x"
CALLOUT_Y_FIELD = f"{_FIELD_PREFIX}_callout_y"


def callout_leader_expression(label_point: str, source_geometry: int, anchor: int) -> str:
    """Straight leader from the feature's callout anchor to the label point.

    ``anchor`` is QgsCallout.AnchorPoint: 0 pole of inaccessibility,
    1 point on exterior, 2 point on surface, 3 centroid. Lines use the point
    closest to the label and points the point itself, as QGIS does.
    """
    if source_geometry == 0:
        origin = "@geometry"
    elif source_geometry == 1:
        origin = "closest_point(@geometry, @q2vt_label)"
    else:
        origin = {
            0: "pole_of_inaccessibility(@geometry, 0.5)",
            1: "closest_point(boundary(@geometry), @q2vt_label)",
            2: "point_on_surface(@geometry)",
            3: "centroid(@geometry)",
        }.get(anchor, "pole_of_inaccessibility(@geometry, 0.5)")
    return (f"with_variable('q2vt_label', {label_point}, "
            f"make_line({origin}, @q2vt_label))")


# ============================================================================
# Module-level configuration
# ============================================================================

# Per processing.run() hard timeout. If a single algorithm exceeds this, the
# rule that triggered it is dropped from the export rather than allowed to
# stall the pipeline. Guarantees export() returns in bounded time regardless
# of bad data, network blips, or upstream bugs.
_PER_ALG_TIMEOUT_S = 600  # 10 minutes

# Hard cap on parallel workers, irrespective of cpu_percent. Beyond this
# point Windows runs out of OS handles, GDAL contention dominates, and the
# export actually gets slower. Empirically 4-6 is the sweet spot.
_MAX_WORKERS_HARD_CAP = 6

# Providers we treat as "must read serially" — anything backed by a remote
# database or HTTP endpoint where the underlying client library is not
# robust to concurrent use from multiple threads.
_SERIAL_READ_PROVIDERS = frozenset(
    {"postgres", "mssql", "oracle", "wfs", "spatialite", "hana", "db2"}
)
# Providers a worker process reads itself (files); any other source (a
# database, a web service, a virtual layer of project layers) is read by the
# main process.
_WORKER_READ_PROVIDERS = frozenset({"ogr", "delimitedtext", "gpx"})

# Temp files prefered to be parquet but in linux which not support parquet they are became gpkg.
_TEMP_LAYER_FORMAT = 'sqlite'
_TEMP_RULE_FORMAT = 'gpkg'
# In-memory chains (serial export): the steps of a rule group hand memory
# layers to each other instead of temporary GeoPackages (opening and writing
# a file cost ~15-20 ms per step; a memory step ~1 ms), and a step identical
# to one already run in this export (the same filter or the same outline at
# another zoom) is not run again. A step over more input features than this
# writes a file as before (memory use stays bounded); the totals bound the
# features kept for shared steps.
_MEM_PREFIX = "q2vtmem:"
_MEMORY_MAX_FEATURES = 50_000
_SHARED_MAX_FEATURES = 1_000_000
# Steps whose result depends only on their parameters (no randomness).
_SHAREABLE_ALGORITHMS = frozenset({
    "native:extractbyexpression", "native:polygonstolines", "native:multiparttosingleparts",
    "native:fieldcalculator", "native:geometrybyexpression", "native:removenullgeometries",
    "native:refactorfields", "native:dropmzvalues", "native:extractvertices", "native:explodelines",
    "native:deletecolumn", "native:collect", "native:dissolve", "native:keepnbiggestparts",
    "native:mergevectorlayers"})
_NONDETERMINISTIC = re.compile(r"\b(rand|randf|uuid|now|random)\s*\(", re.IGNORECASE)
# The expression parameter of each expression-driven step.
_EXPRESSION_PARAMETERS = {"fieldcalculator": "FORMULA", "extractbyexpression": "EXPRESSION",
                          "geometrybyexpression": "EXPRESSION"}
# "Label every feature" layers (publication): each polygon's roomiest point
# (pole of inaccessibility), its free radius and the polygon's direction
# (main angle), in EPSG:3857 metres / degrees: the viewer puts a label that
# cannot fit there at once instead of searching (a narrow or tiny parcel).
LABEL_ANCHOR_MARKER = "q2vt:label-anchor"
_POLE = "pole_of_inaccessibility($geometry, 0.2)"
LABEL_ANCHOR_FIELDS = [
    (6, f"round(x({_POLE}), 2)", "q2vt_pole_x"),
    (6, f"round(y({_POLE}), 2)", "q2vt_pole_y"),
    (6, f"round(distance({_POLE}, boundary($geometry)), 2)", "q2vt_pole_r"),
    (6, "round(main_angle($geometry), 1)", "q2vt_pole_a"),
]
# ============================================================================
# Snapshots — pure-Python data, no QObject references
# ============================================================================

@dataclass(frozen=True)
class _SourceSnapshot:
    """Everything a worker needs to read a base layer; carries no Qt objects."""
    layer_id: str
    name: str
    source_uri: str
    provider: str
    # Renderer feature order ("Control feature rendering order"):
    # ((expression, ascending, nulls_first), ...); empty when disabled.
    order_by: Tuple[Tuple[str, bool, bool], ...] = ()
    # The layer's CRS as the project has it (WKT). A data source without a
    # .prj, or with its CRS overridden in the project, reopens without it.
    crs_wkt: str = ""
    # Publishing: QGIS expression of the stable feature key ("" = none).
    feature_key: str = ""
    # Size/mtime digest of the source files (export cache); None: not
    # file based, never reused.
    data_fingerprint: Optional[str] = None
    # The layer's own URI for cache keys when source_uri is a per-export copy
    # (a memory layer's features); "" = source_uri.
    key_uri: str = ""

    @property
    def needs_serial_read(self) -> bool:
        return self.provider in _SERIAL_READ_PROVIDERS

    @property
    def read_in_main(self) -> bool:
        """Read by the main process even when workers export (export_workers)."""
        return self.provider not in _WORKER_READ_PROVIDERS


@dataclass
class _RuleGroupSnapshot:
    """Everything a worker needs to export one output dataset.

    Every field is a primitive type, string, list or dataclass — no
    QObjects, no QgsVectorLayer references. Safe to consume from any thread.
    """
    output_dataset: str
    layer_id: str
    rule_type: int
    filter_expression: Optional[str]
    geometry_target: int
    geometry_expression: str
    expression_fields: List[Tuple[int, str, str]]
    description: str
    include_required_fields_only: int
    # Geometry recipe (fidelity.materialize.Recipe) — immutable plain data.
    recipe: Optional[Recipe]
    # Source geometry type of the layer (0 point, 1 line, 2 polygon).
    source_geometry: int
    # Fields re-evaluated on every exported part (geometry-dependent
    # data-defined properties of a geometry generator's sub-symbol).
    part_fields: List[Tuple[int, str, str]]
    # Kept ONLY to drive the success/failure return value of export(); workers
    # MUST NOT read any live state from these.
    flat_rules: List[FlattenedRule]
    # Line generator applied first (see FlattenedRule.pre_generator) and the
    # fields computed on each generated part right after it.
    pre_generator: Optional[str] = None
    generated_fields: List[Tuple[int, str, str]] = field(default_factory=list)
    # For messages (workers must not read the live layer).
    layer_name: str = ""
    # The polygons of a "visible polygon" label (see _visible_polygon_group):
    # every part is kept; the viewer places the label in the visible part.
    visible_polygons: bool = False
    # FlattenedRule.merge: the features drawn together ("merge": their union;
    # "invert": the export area outside them).
    merge: str = ""
    # FlattenedRule.point_group (point cluster / displacement at one zoom).
    point_group: Optional[tuple] = None
    # Lowest tile zoom of the data ("o"): colour band edges keep clear of
    # vertices at its coordinate grid.
    data_min_zoom: int = 0
    # Feature-aligned pattern fill (materializer.pattern_anchor_kind) and the
    # map CRS its anchor is measured in: each feature carries the anchor.
    pattern_anchor: str = ""
    anchor_crs: str = ""
    # Only each feature's biggest part (from flat_rules; set for a worker
    # process, which has no rules: RulesExporter._keep_biggest_part).
    keep_biggest_part: Optional[bool] = None


_RING_FIELD = f"{_FIELD_PREFIX}_ring_cw"
# 1 when the (first) exterior ring is clockwise; is_polygon_clockwise() is
# not available before QGIS 3.36.
_RING_CLOCKWISE_EXPRESSION = (
    "with_variable('q2vt_p', if(is_multipart(@geometry), geometry_n(@geometry, 1), @geometry), "
    "if(geom_to_wkt(exterior_ring(force_polygon_cw(@q2vt_p))) = "
    "geom_to_wkt(exterior_ring(@q2vt_p)), 1, 0))"
)


def _open_file(path: str, name: str, transform_context) -> QgsVectorLayer:
    """A dataset file as a layer, without looking for a default style (the
    datasets have none; the search costs a third of every open)."""
    options = QgsVectorLayer.LayerOptions(transform_context)
    options.loadDefaultStyle = False
    return QgsVectorLayer(path, name, "ogr", options)


class _InlineFuture(Future):
    """A job of the serial executor: run on the main thread when its turn
    comes in _iter_completed (no worker thread at all)."""

    def __init__(self, fn, args):
        super().__init__()
        self._call = (fn, args)

    def execute(self) -> None:
        if not self.set_running_or_notify_cancel():
            return
        fn, args = self._call
        try:
            self.set_result(fn(*args))
        except BaseException as error:  # noqa: BLE001 - re-raised by result()
            self.set_exception(error)


class _InlineExecutor:
    """Drop-in for ThreadPoolExecutor that runs every job on the calling
    (main) thread, one after another. QGIS Processing is not safe to run in
    parallel Python threads: on QGIS 3.44 / Windows it corrupted the heap
    (0xc0000374) and QGIS closed without a message."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @staticmethod
    def submit(fn, *args) -> _InlineFuture:
        return _InlineFuture(fn, args)


class _Cancelled(Exception):
    """Raised inside workers when the caller has signalled cancellation."""


# ============================================================================
# RulesExporter
# ============================================================================

class RulesExporter:
    """Export FlattenedRules to GeoParquet datasets, safely and in parallel.

    Public API is unchanged from the legacy implementation:

        exporter = RulesExporter(...)
        layers, rules = exporter.export()

    Internally the pipeline is split into phases that respect QGIS's strict
    thread-affinity rules. See module docstring for the architecture.
    """

    FIELD_PREFIX = "q2vt"

    def __init__(
        self,
        flattened_rules: List[FlattenedRule],
        extent: QgsRectangle,
        include_required_fields_only: int,
        max_zoom,
        utils_dir: str,
        cent_source: int,
        feedback: QgsProcessingFeedback,
        cpu_percent: int = 100,
        diagnostics: Optional[DiagnosticCollector] = None,
        progress_range: Tuple[float, float] = (0.0, 100.0),
        parallel: bool = False,
        feature_keys: Optional[Dict[str, str]] = None,
        extra_tile_fields: Optional[Dict[str, List[str]]] = None,
        cache: Optional["export_cache.ExportCache"] = None,
        light_results: bool = False,
    ):
        self.flattened_rules = flattened_rules
        # Datasets of unchanged layers reused from earlier exports (None: off).
        self.cache = cache
        # Results as ExportedDataset handles (path, name, feature count; the
        # tile export needs no more) instead of QGIS layers.
        self.light_results = light_results
        # {output dataset: cache key} of the datasets of this export (also
        # read by the tile generator for its per-layer tile cache).
        self.dataset_keys: Dict[str, str] = {}
        self._group_diagnostics: Dict[str, list] = {}
        self._failed_groups: set = set()
        # Publishing: {layer id: QGIS expression of the stable feature key}
        # (written to FEATURE_KEY_FIELD of every dataset of the layer) and
        # {layer id: approved source fields kept in the tiles (filters)}.
        self.feature_keys = dict(feature_keys or {})
        self.extra_tile_fields = {k: list(v) for k, v in (extra_tile_fields or {}).items()}
        # QGIS draws in the project CRS: pattern anchors are measured there.
        project_crs = QgsProject.instance().crs()
        self._map_crs = project_crs.authid() if project_crs.isValid() and project_crs.authid() \
            else f"EPSG:{_EPSG_CRS}"
        # Share of the Processing progress bar this export fills.
        self._progress_range = progress_range
        # Messages from worker threads, written by the main thread
        # (QgsProcessingFeedback keeps a log that is not thread-safe).
        self._messages: "queue.SimpleQueue[Tuple[str, str]]" = queue.SimpleQueue()
        self._layer_names: Dict[str, str] = {}
        # QgsRectangle is a value type — safe to share across threads.
        self.extent = extent
        self.include_required_fields_only = include_required_fields_only
        self.max_zoom = max_zoom
        # Point cluster / displacement groups per (source, zoom): every
        # role and rule of the layer at a zoom shares one grouping.
        self._point_group_cache: dict = {}
        self._point_group_lock = threading.Lock()
        self.cent_source = cent_source
        self.utils_dir = utils_dir
        self.feedback = feedback
        self.cpu_percent = cpu_percent
        # Parallel worker threads are opt-in (see _InlineExecutor).
        self.parallel = parallel
        self.diagnostics = diagnostics or DiagnosticCollector()
        # Measurement settings of the project (caller thread snapshot): QGIS
        # measures $area/$length ellipsoidally when an ellipsoid is set and
        # planimetrically in the layer CRS otherwise.
        project = QgsProject.instance()
        self._ellipsoid = project.ellipsoid()
        self._planar = not self._ellipsoid or self._ellipsoid.upper() == "NONE"
        self._distance_unit = project.distanceUnits()
        self._area_unit = project.areaUnits()
        # Expression scopes and transform context, built here on the main
        # thread; workers only copy them. QgsProject.createExpressionContext()
        # rebuilds a cached project scope (reset whenever a layer is added
        # or removed) without a lock, and the global scope fills static user
        # name caches: called from parallel workers, both corrupted the heap
        # (QGIS 3.44 / Windows: 0xc0000374, QGIS closed without a message).
        self._global_scope = QgsExpressionContextUtils.globalScope()
        self._project_scope = QgsExpressionContextUtils.projectScope(project)
        self._transform_context = project.transformContext()
        self._scope_lock = threading.Lock()

        self.processed_layers: List[QgsVectorLayer] = []

        # Temp-file tracking for cleanup.
        self._temp_files: set = set()
        self._temp_files_lock = threading.Lock()

        # In-memory chains (see _MEM_PREFIX): on in the serial export, inside
        # rule groups only (base layers and final datasets stay files).
        self._memory_chains = not parallel and os.environ.get("Q2VT_FILE_CHAINS") != "1"
        self._memory_active = False
        self._mem: Dict[str, QgsVectorLayer] = {}       # token -> memory layer
        self._shared: Dict[str, str] = {}                # step key -> token
        self._shared_tokens: set = set()
        self._shared_features = 0
        self._group_tokens: List[str] = []
        self._mem_counter = 0
        self._file_counts: Dict[str, int] = {}
        self.step_stats = {"run": 0, "shared": 0, "file": 0, "direct": 0}
        # Seconds spent per QGIS layer (its base layer and its datasets), for
        # the export log's slowest layers.
        self.layer_seconds: Dict[str, float] = {}
        self._timing_lock = threading.Lock()

        # Single lock used to serialise reads from "needs_serial_read"
        # providers, regardless of how many workers exist. Conservative but
        # absolutely safe — Postgres/WFS/etc. are read one at a time, full stop.
        self._serial_read_lock = threading.Lock()

        # Cancellation flag. Workers check this between processing.run calls.
        self._cancelled = threading.Event()
        # Worker processes of this export (export_workers), when used.
        self._pool = None
        self._missing_variables: Optional[List[str]] = None
        self._output_info: Dict[str, object] = {}  # a worker's gpkg_info of its outputs

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------
    # The export's GeoPackages are work files: SQLite need not wait for the
    # disk after every write (thread-local GDAL options, the export's thread).
    _SQLITE_OPTIONS = (("OGR_SQLITE_SYNCHRONOUS", "OFF"), ("OGR_SQLITE_JOURNAL", "MEMORY"))

    def export(self) -> Tuple[List[QgsVectorLayer], List[FlattenedRule]]:
        """Run the full export pipeline. Synchronous. Always returns."""
        from osgeo import gdal  # pylint: disable=import-outside-toplevel
        previous = [(name, gdal.GetThreadLocalConfigOption(name, None)) for name, _ in self._SQLITE_OPTIONS]
        for name, value in self._SQLITE_OPTIONS:
            gdal.SetThreadLocalConfigOption(name, value)
        try:
            return self._export()
        finally:
            for name, value in previous:
                gdal.SetThreadLocalConfigOption(name, value)

    def _export(self) -> Tuple[List[QgsVectorLayer], List[FlattenedRule]]:
        try:
            # Phase 0 — caller-thread snapshot.
            if self._is_cancelled():
                return [], []
            sources, rule_groups = self._snapshot_caller_thread()
            cached_outputs = self._reuse_cached(sources, rule_groups)
            pending = [grp for grp in rule_groups if grp.output_dataset not in cached_outputs]
            needed = {grp.layer_id for grp in pending}

            # Phase 1 — serial source materialisation (caller thread).
            if self._is_cancelled():
                return [], []

            # Worker processes start while the sources are read (they need
            # a few seconds to start QGIS).
            self._pool = self._start_workers(pending)
            try:
                needed_sources = {lid: src for lid, src in sources.items() if lid in needed}
                if self._workers_ready():  # each worker reads a source and builds its base layer
                    base_layers = self._sources_on_workers(needed_sources)
                else:
                    materialized = self._materialize_sources_serial(needed_sources)

                    # Phase 2 — parallel base-layer pipeline (file → file).
                    if self._is_cancelled():
                        return [], []

                    base_layers = self._build_base_layers_parallel(materialized)

                # Phase 3 — parallel rule export (file → file).
                if self._is_cancelled():
                    return [], []

                rule_outputs = self._export_rules_parallel(pending, base_layers)
            finally:
                self._stop_workers()
            self._store_cached(pending, rule_outputs)
            rule_outputs.update(cached_outputs)
            # Phase 4 — collect results on caller thread.
            layers, rules = self._collect_results(rule_groups, rule_outputs)
            return layers, self._keep_feature_order(rules)
        finally:
            self._release_all_layers()
            self._cleanup_temp_files()

    # -------------------------------------------------------------------
    # Cancellation
    # -------------------------------------------------------------------
    def _is_cancelled(self) -> bool:
        if self._cancelled.is_set():
            return True
        if self.feedback is not None and self.feedback.isCanceled():
            self._cancelled.set()
            return True
        return False

    def _check_cancel(self) -> None:
        if self._is_cancelled():
            raise _Cancelled()

    # -------------------------------------------------------------------
    # Phase 0 — snapshot (caller thread only)
    # -------------------------------------------------------------------
    def _snapshot_caller_thread(
        self,
    ) -> Tuple[Dict[str, _SourceSnapshot], List[_RuleGroupSnapshot]]:
        """Convert all live-QObject state into pure-Python snapshots.

        After this call, no worker thread will ever read from a live
        QgsVectorLayer, QgsRuleBasedRenderer.Rule, or QgsProject instance.
        """
        # Mutate rule symbols / labeling settings to bake in zoom scale.
        # MUST run on caller thread because it touches QObjects.
        self._resolve_map_scale_in_rules(self.flattened_rules)

        # Group rules by output dataset.
        rules_by_dataset: Dict[str, List[FlattenedRule]] = {}
        for r in self.flattened_rules:
            rules_by_dataset.setdefault(r.output_dataset, []).append(r)

        # Snapshot unique sources.
        sources: Dict[str, _SourceSnapshot] = {}
        for r in self.flattened_rules:
            lid = r.layer.id()
            if lid in sources:
                continue
            source_uri, provider = r.layer.source(), r.layer.providerType()
            fingerprint = export_cache.source_fingerprint(provider, source_uri) \
                if self.cache else None
            key_uri = ""
            reason = self._snapshot_reason(r.layer)
            if reason:
                # Reopening the URI would lose data the project shows (see
                # _snapshot_reason): its features are copied here, on the
                # caller thread, as QGIS has them.
                key_uri = f"{reason}:{self._stable_source(source_uri)}"
                source_uri, fingerprint = self._memory_snapshot(r.layer)
                provider = "ogr"
            sources[lid] = _SourceSnapshot(
                layer_id=lid,
                name=r.layer.name(),
                source_uri=source_uri,
                provider=provider,
                order_by=self._order_by(r.layer),
                crs_wkt=r.layer.crs().toWkt(),
                feature_key=self.feature_keys.get(lid, ""),
                data_fingerprint=fingerprint if self.cache else None,
                key_uri=key_uri,
            )

        # Snapshot rule groups.
        rule_groups: List[_RuleGroupSnapshot] = []
        for output_dataset, flat_rules in rules_by_dataset.items():
            primary = flat_rules[0]

            if primary.get_attr("t") == 1:
                self._single_line_label_as_point(primary)
            # Compute expression fields (data-defined properties, optional
            # label field) — these read from the rule symbol/settings.
            expr_fields = self._create_expression_fields(flat_rules)
            if primary.recipe is not None and primary.recipe.kind == "callout":
                # Label position read in the layer CRS, like QGIS.
                expr_fields = list(expr_fields) + [
                    (6, primary.recipe.param("x"), CALLOUT_X_FIELD),
                    (6, primary.recipe.param("y"), CALLOUT_Y_FIELD)]
            label_text = None
            if primary.get_attr("t") == 1:
                expr_fields = self._add_label_expression_field(
                    primary, expr_fields
                )
                label_text = next((expr for _, expr, name in expr_fields
                                   if name == f"{_FIELD_PREFIX}_label"), None)

            # Evaluate scalar expressions on layer-CRS geometry, as QGIS does.
            layer_crs = primary.layer.crs().authid()
            part_fields = self._part_fields(primary, expr_fields, layer_crs)
            pre_generator, generated_fields = None, []
            if primary.pre_generator:
                # Lines generated first: geometry-dependent properties are
                # computed on each generated part, then carried along.
                pre_generator = self._generator_in_layer_crs(primary.pre_generator, primary)
                generated_fields = self._part_fields(primary, expr_fields, layer_crs, True)
                carried = {name for _, _, name in generated_fields}
                expr_fields = [(ftype, f'"{name}"' if name in carried else expr, name)
                               for ftype, expr, name in expr_fields]
            expr_fields = [
                (ftype, in_layer_crs(expr, f"EPSG:{_EPSG_CRS}", layer_crs, self._planar), name)
                for ftype, expr, name in expr_fields
            ]
            filter_expression = in_layer_crs(
                primary.rule.filterExpression(), f"EPSG:{_EPSG_CRS}", layer_crs, self._planar)
            if label_text:
                filter_expression = self._with_label_text(
                    filter_expression,
                    in_layer_crs(label_text, f"EPSG:{_EPSG_CRS}", layer_crs, self._planar))

            # Compute geometry transformation tuple.
            transformation = self._get_geometry_transformation(primary)
            if transformation is None:
                # No geometry transformation → cannot be exported.
                continue
            geom_target, geom_expr = transformation

            rule_groups.append(_RuleGroupSnapshot(
                output_dataset=primary.output_dataset,
                layer_id=primary.layer.id(),
                rule_type=primary.get_attr("t"),
                filter_expression=filter_expression or None,
                geometry_target=geom_target,
                geometry_expression=geom_expr,
                expression_fields=expr_fields,
                description=primary.get_description(),
                include_required_fields_only=self.include_required_fields_only,
                recipe=primary.recipe,
                source_geometry=primary.get_attr("g"),
                part_fields=part_fields,
                flat_rules=flat_rules,
                pre_generator=pre_generator,
                generated_fields=generated_fields,
                layer_name=primary.layer.name(),
                merge=primary.merge,
                point_group=primary.point_group,
                data_min_zoom=int(primary.get_attr("o") or 0),
                pattern_anchor=self._pattern_anchor(primary, geom_target),
                anchor_crs=self._map_crs if self._pattern_anchor(primary, geom_target) else "",
            ))
            if self._labels_visible_polygon(primary, geom_target):
                rule_groups.append(self._visible_polygon_group(rule_groups[-1], primary))
            elif getattr(primary, "line_label_midpoint", False) and primary.get_attr("t") == 1:
                rule_groups.append(self._visible_line_group(rule_groups[-1], primary))

        return sources, rule_groups

    _GEOMETRY_REFERENCE = re.compile(
        r"\$(geometry|length|area|perimeter|x|y)\b|@geometry\b|@geometry_part_(num|count)\b",
        re.IGNORECASE)

    def _part_fields(self, flat_rule: FlattenedRule, fields, layer_crs: str,
                     generated: bool = False):
        """Data-defined properties of a geometry generator's sub-symbol that
        read the geometry: QGIS evaluates them on the generated geometry, for
        each part it draws (``length(geometry_n($geometry,
        @geometry_part_num))`` = the length of one generated segment), not on
        the source feature. They are computed again on every exported part."""
        symbol = flat_rule.rule.symbol() if flat_rule.get_attr("t") == 0 else None
        if symbol is None or not symbol.symbolLayerCount() or (
                not generated and symbol.symbolLayer(0).layerType() != "GeometryGenerator"):
            return []
        result = []
        for ftype, expr, name in fields:
            if not expr or not self._GEOMETRY_REFERENCE.search(expr):
                continue
            # $geometry: the part as a one-part collection, so geometry_n(..., 1)
            # and the measures work; in the layer CRS like every expression.
            per_part = in_layer_crs(substitute_geometry(expr, "collect_geometries(@geometry)"),
                                    f"EPSG:{_EPSG_CRS}", layer_crs, self._planar)
            result.append((ftype, "with_variable('geometry_part_num', 1, with_variable("
                                  f"'geometry_part_count', 1, {per_part}))", name))
        return result

    @staticmethod
    def _order_by(layer) -> Tuple[Tuple[str, bool, bool], ...]:
        renderer = layer.renderer()
        try:
            if renderer is None or not renderer.orderByEnabled():
                return ()
            return tuple((clause.expression().expression(), bool(clause.ascending()),
                          bool(clause.nullsFirst())) for clause in renderer.orderBy().list())
        except (AttributeError, RuntimeError):
            return ()

    @staticmethod
    def _snapshot_reason(layer) -> str:
        """Why the layer must be copied from the project rather than reopened
        from its URI by a worker ("" = reopen): a temporary (memory) layer
        reopens empty (e.g. one restored by the Memory Layer Saver plugin);
        unsaved edits, joined fields (also auxiliary storage, e.g. moved
        labels) and virtual (expression) fields are not in the source."""
        if layer.providerType() == "memory":
            return "memory"
        if layer.isModified():
            return "edits"
        from qgis.core import QgsFields  # pylint: disable=import-outside-toplevel
        lost = {Qgis.FieldOrigin.Join, Qgis.FieldOrigin.Expression, Qgis.FieldOrigin.Edit} \
            if hasattr(Qgis, "FieldOrigin") else {QgsFields.OriginJoin, QgsFields.OriginExpression,
                                                  QgsFields.OriginEdit}
        fields = layer.fields()
        if any(fields.fieldOrigin(i) in lost for i in range(fields.count())):
            return "fields"
        return ""

    @staticmethod
    def _stable_source(uri: str) -> str:
        """The layer source without what changes on every project load: QGIS
        gives a memory layer a new random ``uid={...}`` each time (the
        content digest tells whether its features changed)."""
        return re.sub(r"[&?]uid=\{[^}]*\}", "", uri)

    def _memory_snapshot(self, layer) -> Tuple[str, Optional[str]]:
        """Caller thread: the layer's features as the project has them
        (feature ids kept; joined and virtual fields, unsaved edits) in a
        GeoPackage, and a fingerprint of its content for the export cache."""
        import hashlib  # pylint: disable=import-outside-toplevel
        from qgis.core import QgsVectorFileWriter  # pylint: disable=import-outside-toplevel
        path = join(self.utils_dir, f"memory_{layer.id()}.gpkg")
        if exists(path):
            os.remove(path)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName, options.layerName = "GPKG", "memory"
        # A GeoPackage source's own "fid" column would be written as the
        # copy's key: an unsaved feature's provisional value collides.
        options.attributes = [i for i, field in enumerate(layer.fields())
                              if field.name().lower() not in ("fid", "ogc_fid")]
        error = QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, path, QgsProject.instance().transformContext(), options)
        if error[0] != QgsVectorFileWriter.NoError:
            self.feedback.reportError(f"Cannot copy temporary layer '{layer.name()}': {error[1]}")
        digest = hashlib.sha256(self._stable_source(layer.source()).encode("utf-8"))
        digest.update(repr(layer.fields().names()).encode("utf-8"))
        for feature in layer.getFeatures():
            digest.update(str(feature.id()).encode())
            digest.update(bytes(feature.geometry().asWkb()))
            digest.update(repr(feature.attributes()).encode("utf-8"))
        return f"{path}|layername=memory", digest.hexdigest()

    @staticmethod
    def _add_order_field(path: str, order_by) -> None:
        """Store each feature's QGIS drawing rank in ``ORDER_FIELD``.

        The rank comes from iterating the (source-CRS) copy with the
        renderer's order-by clauses, exactly as QGIS requests features; the
        style uses it as the sort key of the layer's style layers.
        """
        layer = QgsVectorLayer(path, "order", "ogr")
        if not layer.isValid():
            return
        clauses = [QgsFeatureRequest.OrderByClause(expr, asc, nulls)
                   for expr, asc, nulls in order_by]
        request = QgsFeatureRequest().setOrderBy(QgsFeatureRequest.OrderBy(clauses))
        request.setFlags(QgsFeatureRequest.Flag.NoGeometry)
        ranks = [feature.id() for feature in layer.getFeatures(request)]
        provider = layer.dataProvider()
        provider.addAttributes([QgsField(ORDER_FIELD, QVariant.Int)])
        layer.updateFields()
        index = layer.fields().indexFromName(ORDER_FIELD)
        provider.changeAttributeValues({fid: {index: rank} for rank, fid in enumerate(ranks)})

    # -------------------------------------------------------------------
    # Phase 1 — serial source materialisation
    # -------------------------------------------------------------------
    def _materialize_sources_serial(
        self, sources: Dict[str, _SourceSnapshot]
    ) -> Dict[str, str]:
        """For each unique source, dump a fresh provider read to local Parquet.

        Runs on the caller thread, ONE source at a time. This is the only
        place in the pipeline where we touch a database/network provider.
        """
        materialized: Dict[str, str] = {}
        self._layer_names = {src.layer_id: src.name for src in sources.values()}
        for number, src in enumerate(sources.values(), 1):
            self._check_cancel()
            self._post("pushInfo", f"   Reading layer {number}/{len(sources)}: {src.name}")
            self._progress(0.0, 0.15, number - 1, len(sources))
            main_thread.keep_responsive()
            out_path = self._materialize_one(src)
            if out_path:
                materialized[src.layer_id] = out_path
        return materialized

    def _materialize_one(self, src: _SourceSnapshot) -> Optional[str]:
        """One source read into a local file (its path), None when it cannot be."""
        out_path = join(self.utils_dir, f"materialized_{src.layer_id}.{_TEMP_LAYER_FORMAT}")
        began = time.perf_counter()
        if exists(out_path):
            # Idempotent restart support.
            return out_path
        try:
            # Open a FRESH layer in this thread. The original
            # FlattenedRule.layer reference may have main-thread affinity;
            # here we deliberately don't reuse it. The newly constructed
            # layer is owned by this thread.
            layer = QgsVectorLayer(src.source_uri, src.name, src.provider)
            if layer.isValid() and src.crs_wkt:
                # Épületek (a shapefile without .prj, EPSG:23700 set in the
                # project) reopened without a CRS: the extent filter then
                # dropped all its features without a message.
                crs = QgsCoordinateReferenceSystem.fromWkt(src.crs_wkt)
                if crs.isValid() and crs != layer.crs():
                    layer.setCrs(crs)
            if not layer.isValid():
                self._post("pushWarning",
                    f"Cannot open source '{src.name}' "
                    f"(provider={src.provider}); skipping."
                )
                return None

            # Materialise via fixgeometries(METHOD=0): does the first
            # geometry-cleaning pass AND dumps provider data to Parquet
            # in one shot. Published layers first get their stable
            # feature key, computed on the source (provider FIDs and
            # attributes as the project has them).
            source = layer
            if src.feature_key:
                source = self._run_alg_safe(
                    "fieldcalculator", "native", INPUT=layer, FIELD_NAME=FEATURE_KEY_FIELD,
                    FIELD_TYPE=2, FIELD_LENGTH=0, FORMULA=src.feature_key)
            if src.needs_serial_read:
                with self._serial_read_lock:
                    self._run_alg_safe(
                        "fixgeometries", "native",
                        INPUT=source, METHOD=0, OUTPUT=out_path,
                    )
            else:
                self._run_alg_safe(
                    "fixgeometries", "native",
                    INPUT=source, METHOD=0, OUTPUT=out_path,
                )
            if src.order_by:
                self._add_order_field(out_path, src.order_by)
            self._spent(src.layer_id, began)
            return out_path
        except _Cancelled:
            raise
        except Exception:  # noqa: BLE001  (we want to swallow per-source)
            self.feedback.reportError(
                f"Failed to export source '{src.name}':\n"
                f"{traceback.format_exc()}"
            )
        return None

    # -------------------------------------------------------------------
    # Phase 2 — parallel base-layer pipeline (file → file)
    # -------------------------------------------------------------------
    def _build_base_layers_parallel(
        self, materialized: Dict[str, str]
    ) -> Dict[str, str]:
        """Run fix-structure → reproject → clip → singleparts → simplify."""
        target_paths: Dict[str, str] = {
            lid: join(self.utils_dir, f"map_layer_{lid}.{_TEMP_LAYER_FORMAT}")
            for lid in materialized
        }
        # Idempotent skip.
        todo = {
            lid: src for lid, src in materialized.items()
            if not exists(target_paths[lid])
        }

        if not todo:
            return target_paths

        max_workers = self._compute_pool_size(len(todo))
        keep = self._vertex_sensitive_layers()
        anchored = {rule.layer.id() for rule in self.flattened_rules or []
                    if self._pattern_anchor(rule, 2) == "feature"}

        with self._executor(max_workers, "rules-base") as pool:
            futures: Dict[Future, str] = {
                pool.submit(
                    self._build_one_base_layer, src_path, target_paths[lid], lid in keep,
                    lid in anchored
                ): lid
                for lid, src_path in todo.items()
            }
            names = {lid: f": {name}" for lid, name in self._layer_names.items()}
            for done_count, fut in enumerate(self._iter_completed(futures), 1):
                lid = futures[fut]
                self._progress(0.15, 0.3, done_count, len(futures))
                self._post("pushInfo", f"   Prepared layer {done_count}/{len(futures)}"
                                       f"{names.get(lid, '')}")
                try:
                    fut.result(timeout=_PER_ALG_TIMEOUT_S)
                except _Cancelled:
                    self.feedback.pushInfo("Base-layer build cancelled.")
                    return target_paths
                except Exception:  # noqa: BLE001
                    self.feedback.reportError(
                        f"Base-layer build failed for layer_id={lid}:\n"
                        f"{traceback.format_exc()}"
                    )
        return target_paths

    def transform_extent(self, src_path):
        """Transforms a QgsRectangle from a source CRS to a destination CRS."""
        source_crs = QgsCoordinateReferenceSystem(_EPSG_CRS)
        dest_crs = QgsVectorLayer(src_path).crs()
        if source_crs == dest_crs:
            return self.extent
        
        transformer = QgsCoordinateTransform(source_crs, dest_crs, self._transform_context)
        transformed_extent = transformer.transformBoundingBox(self.extent)
        return transformed_extent
    
    def _vertex_sensitive_layers(self) -> set:
        """Layers drawn by symbols built from their vertices (arrows, markers
        on vertices or segment centres): their lines keep every vertex, as
        QGIS draws them (simplification drops nearly collinear vertices)."""
        keep = set()
        for rule in self.flattened_rules or []:
            recipe = getattr(rule, "recipe", None)
            if recipe is None:
                continue
            if recipe.kind == "arrow_polygons" or (recipe.kind == "marker_points" and any(
                    p in mat.VERTEX_PLACEMENTS or p == "SegmentCenter" for p in recipe.placements)):
                keep.add(rule.layer.id())
        return keep

    def _pattern_anchor(self, rule: FlattenedRule, geometry_target: int) -> str:
        """materializer.pattern_anchor_kind of a polygon rule drawn as a pattern
        texture on its own polygons ("" otherwise)."""
        if rule.recipe is not None or rule.pre_generator or rule.get_attr("t") != 0 \
                or rule.get_attr("g") != 2 or geometry_target != 2:
            return ""
        symbol = rule.rule.symbol()
        if symbol is None or symbol.symbolLayerCount() != 1:
            return ""
        return pattern_anchor_kind(symbol.symbolLayer(0))

    _BASE_ANCHOR_X = f"{_FIELD_PREFIX}_fanchor_x"
    _BASE_ANCHOR_Y = f"{_FIELD_PREFIX}_fanchor_y"

    def _anchor_expression(self, crs: str, axis: str, top: bool) -> str:
        """x or y (EPSG:3857) of the bottom-left (``top``: top-left) corner of
        the geometry's bounding box measured in ``crs``."""
        export = f"EPSG:{_EPSG_CRS}"
        y_edge = "y_max" if top else "y_min"
        if not crs or crs == export:
            return f"x_min(@geometry)" if axis == "x" else f"{y_edge}(@geometry)"
        geom = f"transform(@geometry, '{export}', '{crs}')"
        return (f"{axis}(transform(make_point(x_min({geom}), {y_edge}({geom})), "
                f"'{crs}', '{export}'))")

    def _build_one_base_layer(self, src_path: str, dst_path: str,
                              keep_vertices: bool = False, feature_anchor: bool = False) -> None:
        """Worker: run the cleanup chain on a local Parquet file."""
        began = time.perf_counter()
        try:
            self._build_base_layer(src_path, dst_path, keep_vertices, feature_anchor)
        finally:
            self._spent(os.path.basename(dst_path)[len("map_layer_"):].rsplit(".", 1)[0], began)

    def _build_base_layer(self, src_path: str, dst_path: str, keep_vertices: bool,
                          feature_anchor: bool) -> None:
        self._check_cancel()
        crash_log.note(f"Base layer {dst_path}")
        transform_extent = self.transform_extent(src_path)
        clipped = self._run_alg_safe(
            "extractbyextent", "native",
            INPUT=src_path, EXTENT=transform_extent, CLIP=False,
        )
        self._check_cancel()
        geometry_type = _enum_value(QgsVectorLayer(clipped, "check", "ogr").geometryType())
        is_polygon = geometry_type == 2
        # Finish the geometry fix of Phase 1 for *invalid* geometries only:
        # fixgeometries(METHOD=1) also rewrites valid polygons (new start
        # vertex, rewound rings), while QGIS draws directional outline
        # symbols (marker intervals, arrows, offsets, dashes) along the
        # source rings as stored.
        fixed = "make_valid(@geometry, method:='structure')"
        if is_polygon:
            # A repaired polygon keeps the orientation of its exterior ring.
            clipped = self._run_alg_safe(
                "fieldcalculator", "native", INPUT=clipped, FIELD_NAME=_RING_FIELD,
                FIELD_TYPE=1, FORMULA=_RING_CLOCKWISE_EXPRESSION)
            fixed = (f"with_variable('q2vt_f', {fixed}, if(\"{_RING_FIELD}\" = 1, "
                     f"force_polygon_cw(@q2vt_f), if(\"{_RING_FIELD}\" = 0, "
                     f"force_polygon_ccw(@q2vt_f), @q2vt_f)))")
        fixed_struct = self._run_alg_safe(
            "geometrybyexpression", "native", INPUT=clipped,
            OUTPUT_GEOMETRY={0: 2, 1: 1, 2: 0}.get(geometry_type, 0),
            EXPRESSION=f"if(@geometry IS NULL OR is_valid(@geometry), @geometry, {fixed})")
        if is_polygon:
            fixed_struct = self._run_alg_safe(
                "deletecolumn", "native", INPUT=fixed_struct, COLUMN=[_RING_FIELD])
        self._check_cancel()
        reprojected = self._run_alg_safe(
            "reprojectlayer", "native",
            INPUT=fixed_struct,
            TARGET_CRS=QgsCoordinateReferenceSystem(f"EPSG:{_EPSG_CRS}"),
        )
        orig_id = self._run_alg_safe(
            "fieldcalculator", "native",
            INPUT=reprojected,
            FIELD_NAME=f'{_FIELD_PREFIX}_orig_id',
            FIELD_TYPE=0,
            FORMULA='to_int(@id)'
            )
        if feature_anchor:
            # QGIS starts a point / line / SVG pattern at the bottom-left of
            # the whole feature: measured before the parts are split.
            for name, axis in ((self._BASE_ANCHOR_X, "x"), (self._BASE_ANCHOR_Y, "y")):
                orig_id = self._run_alg_safe(
                    "fieldcalculator", "native", INPUT=orig_id, FIELD_NAME=name, FIELD_TYPE=0,
                    FIELD_LENGTH=24, FIELD_PRECISION=6,
                    FORMULA=self._anchor_expression(self._map_crs, axis, False))
        self._check_cancel()
        singleparted = self._run_alg_safe(
            "multiparttosingleparts", "native", INPUT=orig_id
        )
        self._check_cancel()
        if keep_vertices:
            self._run_alg_safe("savefeatures", "native", INPUT=singleparted, OUTPUT=dst_path)
            return
        self._run_alg_safe(
            "simplifygeometries", "native",
            INPUT=singleparted,
            METHOD=0,
            TOLERANCE=self._simplification_tolerance(),
            OUTPUT=dst_path,
        )

    def _simplification_tolerance(self) -> float:
        """Simplification tolerance in EPSG:3857 metres: a fraction of one
        tile unit (1/4096 of a tile) at the max zoom, so that simplification
        never shows beyond the tiles' own coordinate rounding."""
        world = 2 * math.pi * 6378137.0
        return _DATA_SIMPLIFICATION_TOLERANCE * world / (2 ** int(self.max_zoom) * 4096)

    # -------------------------------------------------------------------
    # Phase 3 — parallel rule export (file → file)
    # -------------------------------------------------------------------
    def _export_rules_parallel(
        self,
        rule_groups: List[_RuleGroupSnapshot],
        base_layers: Dict[str, str],
    ) -> Dict[str, Optional[str]]:
        """For each rule group, run filter → refactor → geometry chain in parallel."""
        outputs: Dict[str, Optional[str]] = {}
        if not rule_groups:
            return outputs
        if self._workers_ready():
            rule_groups = self._groups_on_workers(rule_groups, base_layers, outputs)
            if not rule_groups or self._is_cancelled():
                for grp in rule_groups:
                    outputs.setdefault(grp.output_dataset, None)
                return outputs

        max_workers = self._compute_pool_size(len(rule_groups))

        with self._executor(max_workers, "rules-export") as pool:
            futures: Dict[Future, _RuleGroupSnapshot] = {}
            for grp in rule_groups:
                src_path = base_layers.get(grp.layer_id)
                if not src_path or not exists(src_path):
                    outputs[grp.output_dataset] = None
                    self._failed_groups.add(grp.output_dataset)  # source unreadable: not "empty"
                    continue
                fut = pool.submit(
                    self._export_one_rule_group_recorded, grp, src_path
                )
                futures[fut] = grp

            total = len(futures)
            step = max(1, total // 25)
            last_message = time.monotonic()
            for done_count, fut in enumerate(self._iter_completed(futures), 1):
                grp = futures[fut]
                self._progress(0.3, 1.0, done_count, total)
                if done_count % step == 0 or done_count == total or \
                        time.monotonic() - last_message > 5:
                    last_message = time.monotonic()
                    self._post("pushInfo", f"   Exported {done_count}/{total} datasets "
                                           f"(last: {grp.layer_name or grp.layer_id})")
                try:
                    outputs[grp.output_dataset] = fut.result(
                        timeout=_PER_ALG_TIMEOUT_S
                    )
                except _Cancelled:
                    self.feedback.pushInfo("Rule export cancelled.")
                    for pending_fut, pending_grp in futures.items():
                        outputs.setdefault(pending_grp.output_dataset, None)
                    return outputs
                except Exception as error:  # noqa: BLE001
                    self._failed_groups.add(grp.output_dataset)
                    self.feedback.reportError(
                        f"Rule export failed for '{grp.output_dataset}':\n"
                        f"{traceback.format_exc()}"
                    )
                    self.diagnostics.add(
                        "Q2VT_RULE_EXPORT_FAILED", f"{grp.description}: {error}",
                        layer_id=grp.layer_id, component=grp.output_dataset,
                        detail=traceback.format_exc(limit=-3))
                    outputs[grp.output_dataset] = None
        return outputs

    # -------------------------------------------------------------------
    # Worker processes (export_workers)
    # -------------------------------------------------------------------
    # From this many datasets to export on: below, starting QGIS in the
    # workers (seconds on Windows) takes about as long as they save.
    _MIN_GROUPS_FOR_WORKERS = 150

    def _worker_count(self, pending: List[_RuleGroupSnapshot]) -> int:
        """Worker processes for this export: Q2VT_WORKERS=n sets it (0: none)."""
        forced = os.environ.get("Q2VT_WORKERS", "")
        if forced.isdigit():
            return int(forced) if pending else 0
        if self.parallel or not self._memory_chains or len(pending) < self._MIN_GROUPS_FOR_WORKERS:
            return 0
        cpu = max(1, int((os.cpu_count() or 1) * self.cpu_percent / 100))
        return min(cpu, _MAX_WORKERS_HARD_CAP) if cpu > 1 else 0

    def _start_workers(self, pending: List[_RuleGroupSnapshot]):
        count = self._worker_count(pending)
        if count <= 0:
            return None
        from . import export_workers  # pylint: disable=import-outside-toplevel
        try:
            state = export_workers.worker_state(self)
            pool = export_workers.WorkerPool(count, state, self.utils_dir)
        except Exception as error:  # noqa: BLE001 - the export goes on in this process
            crash_log.note(f"Worker processes not started: {error}")
            return None
        pool.ready_workers = None  # known once they answer (_workers_ready)
        self._missing_variables = state["missing_variables"]
        return pool

    def _workers_ready(self) -> bool:
        """Whether worker processes take part (waiting for them to start)."""
        pool = getattr(self, "_pool", None)
        if pool is None:
            return False
        if pool.ready_workers is None:
            from qgis.core import Qgis as _Qgis  # pylint: disable=import-outside-toplevel
            pool.ready_workers = pool.wait_ready(_Qgis.version(), self._is_cancelled) \
                if pool.workers else []
            if pool.ready_workers:
                self._post("pushInfo", f"   {len(pool.ready_workers)} worker processes export the datasets")
            else:
                crash_log.note(f"Worker processes unavailable ({pool.error}): this process exports")
                self._stop_workers()
                return False
        return bool(pool.ready_workers) and any(pool.alive[n] for n in pool.ready_workers)

    def _stop_workers(self) -> None:
        pool = getattr(self, "_pool", None)
        self._pool = None
        if pool is not None:
            pool.close(kill=self._is_cancelled())

    def _run_on_workers(self, tasks: list, handle) -> list:
        """Hand ``tasks`` ((task message, items)) to the idle workers;
        ``handle(message)`` takes each result and tells whether it finished
        the worker's task. The items of the tasks no worker finished (a
        worker stopped) are returned, to be done in this process."""
        pool = self._pool
        idle = [n for n in pool.ready_workers if pool.alive[n]]
        waiting, running, leftovers = list(tasks), {}, []
        while waiting or running:
            while waiting and idle:
                number = idle.pop(0)
                task = waiting.pop(0)
                if pool.submit(number, task[0]):
                    running[number] = task
                else:
                    waiting.insert(0, task)
            if not running:
                break  # every worker stopped
            try:
                number, message = pool.results.get(timeout=0.2)
            except queue.Empty:
                message = None
            self._flush_messages()
            main_thread.keep_responsive()
            if self._is_cancelled():
                return []
            if message is None:
                continue
            if message[0] == "exit":
                pool.alive[number] = False
                task = running.pop(number, None)
                if task is not None:
                    leftovers.extend(task[1])
                crash_log.note(f"Worker {number} stopped (exit code {message[1]})")
                continue
            for kind, text in (message[-1] if message[0] in ("base-done", "group-done") else []):
                self._post(kind, text)
            if handle(message, running.get(number)):
                running.pop(number, None)
                idle.append(number)
        return leftovers + [item for task in waiting for item in task[1]]

    def _sources_on_workers(self, sources: Dict[str, _SourceSnapshot]) -> Dict[str, str]:
        """The sources read and their base layers built by the workers (a
        database, web service or virtual layer read here first, as before);
        what a worker could not do is done here. {layer id: base layer}."""
        self._layer_names = {src.layer_id: src.name for src in sources.values()}
        targets = {lid: join(self.utils_dir, f"map_layer_{lid}.{_TEMP_LAYER_FORMAT}") for lid in sources}
        keep = self._vertex_sensitive_layers()
        anchored = {rule.layer.id() for rule in self.flattened_rules or []
                    if self._pattern_anchor(rule, 2) == "feature"}
        read_here = [src for src in sources.values() if src.read_in_main]
        materialized = {}
        for number, src in enumerate(read_here, 1):
            self._check_cancel()
            self._post("pushInfo", f"   Reading layer {number}/{len(read_here)}: {src.name}")
            path = self._materialize_one(src)
            if path:
                materialized[src.layer_id] = path
        tasks = []
        for lid, src in sources.items():
            if exists(targets[lid]) or (src.read_in_main and lid not in materialized):
                continue
            tasks.append((("source", lid, None if src.read_in_main else src, materialized.get(lid),
                           targets[lid], lid in keep, lid in anchored), [lid]))
        done, left = [0], {}

        def handle(message, task):
            _, lid, error, seconds, _ = message
            done[0] += 1
            self._progress(0.0, 0.3, done[0], len(tasks))
            self._post("pushInfo", f"   Prepared layer {done[0]}/{len(tasks)}: {self._layer_names.get(lid, lid)}")
            with self._timing_lock:
                self.layer_seconds[lid] = self.layer_seconds.get(lid, 0.0) + seconds
            if error:  # done again here (the same steps, this QGIS)
                crash_log.note(f"Layer {lid} failed in a worker:\n{error}")
                left[lid] = sources[lid]
                read = join(self.utils_dir, f"materialized_{lid}.{_TEMP_LAYER_FORMAT}")
                for path in [targets[lid]] + ([read] if lid not in materialized else []):
                    if exists(path):
                        os.remove(path)
            return True
        for lid in self._run_on_workers(tasks, handle):
            left[lid] = sources[lid]
        if left and not self._is_cancelled():
            read = {lid: materialized[lid] for lid in left if lid in materialized}
            read.update(self._materialize_sources_serial(
                {lid: src for lid, src in left.items() if lid not in materialized}))
            self._layer_names = {src.layer_id: src.name for src in sources.values()}
            self._build_base_layers_parallel(read)
        return targets

    def _groups_on_workers(self, rule_groups: List[_RuleGroupSnapshot], base_layers: Dict[str, str],
                           outputs: Dict[str, Optional[str]]) -> List[_RuleGroupSnapshot]:
        """Rule groups exported by the workers, a layer's groups together (they
        share steps), the biggest layers first; the groups left to this
        process (they need its project, or a worker could not do them)."""
        from . import export_workers  # pylint: disable=import-outside-toplevel
        here, by_layer = [], {}
        for grp in rule_groups:
            source = base_layers.get(grp.layer_id)
            if not source or not exists(source):
                outputs[grp.output_dataset] = None
                self._failed_groups.add(grp.output_dataset)  # source unreadable: not "empty"
            elif export_workers.needs_main_process(grp, self._missing_variables):
                here.append(grp)
            else:
                by_layer.setdefault(grp.layer_id, []).append(grp)
        workers = sum(1 for n in self._pool.ready_workers if self._pool.alive[n])
        total_groups = sum(len(groups) for groups in by_layer.values())
        chunk = max(8, total_groups // max(1, 4 * workers))
        tasks = []
        for lid, groups in by_layer.items():
            weight = os.path.getsize(base_layers[lid]) if exists(base_layers[lid]) else 0
            for start in range(0, len(groups), chunk):
                part = groups[start:start + chunk]
                items = [(dataclasses.replace(g, flat_rules=[], keep_biggest_part=self._keep_biggest_part(g)),
                          base_layers[lid]) for g in part]
                tasks.append(((("groups", items)), part, weight * len(part)))
        tasks.sort(key=lambda task: -task[2])
        by_name = {grp.output_dataset: grp for grp in rule_groups}
        total, done, last = total_groups + len(here), [0], [time.monotonic()]
        before = len(self.diagnostics.items)

        def handle(message, task):
            if message[0] == "task-done":
                return True
            grp = by_name[message[1]]
            if message[0] == "group-main":  # a function only this QGIS knows
                here.append(grp)
                return False
            _, name, output, error, diagnostics, seconds, info, _ = message
            with self._timing_lock:
                self.layer_seconds[grp.layer_id] = self.layer_seconds.get(grp.layer_id, 0.0) + seconds
            if error:  # done again here: the same steps, this QGIS
                crash_log.note(f"Rule group {name} failed in a worker:\n{error}")
                path = join(self.utils_dir, f"{name}.{_TEMP_RULE_FORMAT}")
                if exists(path):
                    os.remove(path)
                here.append(grp)
                return False
            restored = DiagnosticCollector()
            export_cache.replay_diagnostics(restored, diagnostics, name, name)
            self._group_diagnostics[name] = restored.items
            self.diagnostics.extend(restored.items)
            outputs[name] = output
            if info is not None:
                self._output_info[name] = DatasetInfo(*info)
            done[0] += 1
            self._progress(0.3, 1.0, done[0], total)
            if time.monotonic() - last[0] > 5 or done[0] == total_groups:
                last[0] = time.monotonic()
                self._post("pushInfo", f"   Exported {done[0]}/{total} datasets "
                                       f"(last: {grp.layer_name or grp.layer_id})")
            return False
        left = self._run_on_workers([(task[0], task[1]) for task in tasks], handle)
        reported = set(outputs)
        here += [grp for grp in left if grp.output_dataset not in reported and grp not in here]
        self._order_diagnostics(before, rule_groups)
        order = {grp.output_dataset: index for index, grp in enumerate(rule_groups)}
        return sorted(here, key=lambda grp: order[grp.output_dataset])

    def _order_diagnostics(self, before: int, rule_groups: List[_RuleGroupSnapshot]) -> None:
        """The workers' diagnostics in the order of the groups (as one process
        adds them), not in the order they finished."""
        order = {grp.output_dataset: index for index, grp in enumerate(rule_groups)}
        self.diagnostics.sort_from(before, lambda d: order.get(getattr(d, "component", None), len(order)))

    def _keep_biggest_part(self, grp: _RuleGroupSnapshot) -> bool:
        """Only the biggest part of each feature: a label not placed on
        every part, a centroid fill not on every part."""
        if grp.keep_biggest_part is not None:
            return grp.keep_biggest_part
        if grp.recipe is not None and grp.recipe.kind == "label_windows":
            return False  # every window is a label (_label_windows keeps the longest part)
        if grp.rule_type == 1 and not grp.visible_polygons:
            settings = grp.flat_rules[0].rule.settings()
            if settings and not settings.labelPerPart:
                return True
        if grp.rule_type == 0:
            symbol_layer = grp.flat_rules[0].rule.symbol().symbolLayers()[0]
            if symbol_layer.layerType() == 'CentroidFill' and not symbol_layer.pointOnAllParts():
                return True
        return False

    def _spent(self, layer_id: str, began: float) -> None:
        with self._timing_lock:
            self.layer_seconds[layer_id] = self.layer_seconds.get(layer_id, 0.0) + \
                time.perf_counter() - began

    def _export_one_rule_group_recorded(self, grp: _RuleGroupSnapshot, source_path: str):
        """_export_one_rule_group, keeping the diagnostics it adds (stored
        with the dataset in the export cache and replayed on reuse)."""
        before = len(self.diagnostics.items)
        began = time.perf_counter()
        try:
            if not self._memory_chains:
                return self._export_one_rule_group(grp, source_path)
            self._memory_active = True
            try:
                return self._export_one_rule_group(grp, source_path)
            except _Cancelled:
                raise
            except Exception as error:  # noqa: BLE001 - redone as before, with files
                crash_log.note(f"Rule group {grp.output_dataset}: memory chain failed ({error}); "
                               "redone with files")
                del self.diagnostics.items[before:]
                output_path = join(self.utils_dir, f"{grp.output_dataset}.{_TEMP_RULE_FORMAT}")
                if exists(output_path):
                    os.remove(output_path)
                self._memory_active = False
                return self._export_one_rule_group(grp, source_path)
            finally:
                self._memory_active = False
                self._release_group_layers()
        finally:
            added = self.diagnostics.items[before:]
            if self.parallel:  # other groups add concurrently: only this group's
                added = [d for d in added if d.component == grp.output_dataset]
            self._group_diagnostics[grp.output_dataset] = added
            self._spent(grp.layer_id, began)

    # -------------------------------------------------------------------
    # Export cache
    # -------------------------------------------------------------------
    def _cache_context(self, used: Optional[str] = None) -> dict:
        """Settings every dataset depends on (besides its rule and source).
        Of the project and global variables only those named in ``used`` (the
        rules' expressions, as text): other plugins keep some up to date (a
        time tracker's @time_tracker_total_minutes), which redid every layer."""
        extent = self.extent
        project_scope = QgsExpressionContextUtils.projectScope(QgsProject.instance())
        global_scope = QgsExpressionContextUtils.globalScope()
        variables = {}
        for scope in (global_scope, project_scope):
            for name in scope.variableNames():
                if name.startswith(("project_last_saved", "project_path", "project_home",
                                    "project_basename", "project_filename", "user_", "_")):
                    continue
                if used is not None and not re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", used):
                    continue
                value = export_cache.stable_value(scope.variable(name))
                if value is not export_cache.UNSTABLE:
                    variables[name] = value
        operations = self._transform_context.coordinateOperations() \
            if hasattr(self._transform_context, "coordinateOperations") else {}
        return {
            "extent": [extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()],
            "required_fields_only": self.include_required_fields_only,
            "max_zoom": self.max_zoom, "cent_source": self.cent_source,
            "ellipsoid": self._ellipsoid, "planar": self._planar,
            "units": [int(getattr(self._distance_unit, "value", self._distance_unit)),
                      int(getattr(self._area_unit, "value", self._area_unit))],
            "variables": variables,
            "operations": {f"{k[0]}>{k[1]}" if isinstance(k, tuple) else str(k): str(v)
                           for k, v in dict(operations).items()},
        }

    def _dataset_key_parts(self, src: _SourceSnapshot, grp: _RuleGroupSnapshot):
        group = {f.name: getattr(grp, f.name) for f in dataclasses.fields(grp)
                 if f.name not in ("flat_rules", "output_dataset", "layer_name")}
        if grp.recipe is None or grp.recipe.kind != "color_bands":
            # Only colour bands depend on the first zoom (bands under a pixel
            # there are merged); other datasets are reused under a new range.
            group.pop("data_min_zoom", None)
        source = {f.name: getattr(src, f.name) for f in dataclasses.fields(src)
                  if f.name not in ("layer_id", "name", "feature_key", "key_uri")}
        if src.key_uri:  # a per-export copy: the layer's own URI
            source["source_uri"] = src.key_uri
        return source, src.feature_key, self.extra_tile_fields.get(grp.layer_id, []), group

    def _dataset_key(self, context: dict, src: _SourceSnapshot, grp: _RuleGroupSnapshot) -> str:
        source, feature_key, extra, group = self._dataset_key_parts(src, grp)
        return export_cache.make_key("dataset", context, source, feature_key, extra, group)

    def _reuse_cached(self, sources: Dict[str, _SourceSnapshot],
                      rule_groups: List[_RuleGroupSnapshot]) -> Dict[str, Optional[str]]:
        """Copy the datasets of unchanged layers from the cache; returns
        {output dataset: path or None (empty result)} of the reused ones."""
        self.dataset_keys = {}
        reused: Dict[str, Optional[str]] = {}
        if self.cache is None:
            return reused
        used = json.dumps([export_cache._plain(self._dataset_key_parts(sources[grp.layer_id], grp))  # pylint: disable=protected-access
                           for grp in rule_groups if grp.layer_id in sources],
                          ensure_ascii=False, default=repr)
        context = self._cache_context(used)
        last = self.cache.last_components()
        last_context = last.get("_context") or {}
        components = {"_context": context}
        code, context_hash = export_cache.code_fingerprint()[:16], export_cache.part_hash(context)
        redone: Dict[str, set] = {}
        for grp in rule_groups:
            src = sources.get(grp.layer_id)
            if src is None or not src.data_fingerprint:
                if src is not None:  # said, not silently redone every time
                    reason = ("cannot read the file's change stamps"
                              if src.provider in export_cache.FILE_PROVIDERS
                              else "database or web layer: changes cannot be detected")
                    redone.setdefault(f"not cached: {reason}", set()).add(grp.layer_name or grp.layer_id)
                continue
            key = self._dataset_key(context, src, grp)
            self.dataset_keys[grp.output_dataset] = key
            source, feature_key, extra, group = self._dataset_key_parts(src, grp)
            now = {"code": code, "context": context_hash, "source": export_cache.part_hash(source),
                   "key": export_cache.part_hash(feature_key),
                   "rule": export_cache.part_hash([extra, group])}
            components[grp.output_dataset] = now
            target = join(self.utils_dir, f"{grp.output_dataset}.{_TEMP_RULE_FORMAT}")
            meta = self.cache.get_dataset(key, target)
            if meta is None:
                for reason in export_cache.miss_reasons(last.get(grp.output_dataset), now,
                                                        last_context, context):
                    redone.setdefault(reason, set()).add(grp.layer_name or grp.layer_id)
                continue
            reused[grp.output_dataset] = None if meta.get("empty") else target
            stored = meta.get("diagnostics") or []
            old = next((d.get("component") for d in stored if d.get("component")), "")
            export_cache.replay_diagnostics(self.diagnostics, stored, old, grp.output_dataset)
        try:
            self.cache.save_components(components)
        except (OSError, TypeError, ValueError):
            pass
        if reused:
            self._post("pushInfo", f"   Reused {len(reused)} of {len(rule_groups)} datasets of "
                                   "unchanged layers (export cache)")
        for reason, layers in sorted(redone.items()):  # why the others are redone
            names = sorted(layers)
            self._post("pushInfo", f"   Redone ({reason}): " + ", ".join(names[:12])
                       + (f" and {len(names) - 12} more" if len(names) > 12 else ""))
        return reused

    def _store_cached(self, groups: List[_RuleGroupSnapshot],
                      outputs: Dict[str, Optional[str]]) -> None:
        if self.cache is None or self._is_cancelled():
            return
        for grp in groups:
            key = self.dataset_keys.get(grp.output_dataset)
            if not key or grp.output_dataset in self._failed_groups \
                    or grp.output_dataset not in outputs:
                continue
            path = outputs.get(grp.output_dataset)
            self.cache.put_dataset(key, path if path and exists(path) else None,
                                   export_cache.diagnostics_to_dicts(
                                       self._group_diagnostics.get(grp.output_dataset, [])))

    def validate_expression(self, grp, expr_str: str):
        layer_name = grp.layer_name or grp.layer_id
        rule_type = 'labeling' if grp.rule_type == 1 else 'symbology'
        warning_msg = f'The expression "{expr_str}" within the {rule_type} of the "{layer_name}" layer'

        if not isinstance(expr_str, str):
            self._post("pushWarning", f"{warning_msg} must be a string.")

        expr_str = expr_str.strip()

        if not expr_str:
            return None

        expr = QgsExpression(expr_str)

        if expr.hasParserError():
            self._post("pushWarning", f"{warning_msg} is not valid.")
            self.diagnostics.add(
                "Q2VT_EXPR_INVALID", f"{warning_msg} is not valid: {expr.parserErrorString()}",
                layer_id=grp.layer_id, component=grp.output_dataset, detail=expr_str)
            return None

        return expr_str

    def _export_one_rule_group(
        self, grp: _RuleGroupSnapshot, source_path: str
    ) -> Optional[str]:
        """Worker: run the rule-export chain entirely from local files."""
        self._check_cancel()
        crash_log.note(f"Rule group {grp.output_dataset}: {grp.description}")
        output_path = join(self.utils_dir, f"{grp.output_dataset}.{_TEMP_RULE_FORMAT}")
        if exists(output_path):
            return output_path

        current_input: str = source_path

        # Point cluster / displacement: the whole layer grouped at this zoom
        # (before any rule filter: QGIS groups every drawn point).
        if grp.point_group:
            current_input = self._point_groups(current_input, grp.point_group)
            if current_input is None:
                return None

        # Optional filter step.
        if grp.filter_expression:
            self._check_cancel()
            filt_out = self._temp_path("filt")

            if not self.validate_expression(grp, grp.filter_expression):
                return None

            filt = self._run_alg_safe(
                "extractbyexpression", "native",
                INPUT=current_input,
                EXPRESSION=grp.filter_expression,
                OUTPUT=filt_out,
            )
            check = self._open(filt, "check")
            if not check.isValid() or check.featureCount() <= 0:
                return None
            current_input = filt

        if grp.pre_generator:
            current_input = self._generated_lines(current_input, grp)
            if current_input is None:
                return None

        # Merged features / inverted polygons: the symbol's features drawn
        # together, before any recipe (a gradient fills the merged shape).
        if grp.merge and grp.rule_type == 0:
            current_input = self._merged_features(current_input, grp)
            if current_input is None:
                return None

        # Materialized marker positions: derive point features (with the
        # line azimuth) from the complete original lines before any field
        # expressions or tiling.
        if grp.recipe is not None and grp.recipe.kind == "marker_points":
            current_input = self._materialize_marker_points(
                current_input, grp.recipe, grp.source_geometry)
            check = self._open(current_input, "check")
            if not check.isValid() or check.featureCount() <= 0:
                return None

        # Patterns on large or detailed polygons: pieces, or native points.
        if grp.recipe is not None and grp.recipe.kind == "grid_points" and \
                grp.source_geometry == 2 and mat.grid_splittable(grp.recipe):
            current_input = self._pattern_pieces(current_input, grp.recipe)
        elif grp.recipe is not None and grp.recipe.kind == "random_points":
            current_input = self._random_points(current_input, grp.recipe)
            check = self._open(current_input, "check")
            if not check.isValid() or check.featureCount() <= 0:
                return None
        elif grp.recipe is not None and grp.recipe.kind == "color_bands":
            current_input = self._color_bands(current_input, grp.recipe, grp.data_min_zoom)
            if current_input is None:
                return None
        elif grp.recipe is not None and grp.recipe.kind == "interpolated_segments":
            current_input = self._interpolated_segments(current_input, grp.recipe)
            if current_input is None:
                return None
        elif grp.recipe is not None and grp.recipe.kind == "arrow_polygons":
            current_input = self._arrow_polygons(current_input, grp.recipe)
            if current_input is None:
                return None
        elif grp.recipe is not None and grp.recipe.kind == "direction_runs":
            current_input = self._direction_runs(current_input, grp.recipe, grp.source_geometry)
            if current_input is None:
                return None
        elif grp.recipe is not None and grp.recipe.kind == "label_windows":
            current_input = self._label_windows(current_input, grp)
            if current_input is None:
                return None

        # Field mapping.
        field_mapping = self._build_field_mapping(grp, current_input)

        self._check_cancel()
        field_mapping = list({tuple(sorted(d.items())): d for d in field_mapping}.values())
        refactored = self._run_alg_safe(
            "refactorfields", "native",
            INPUT=current_input,
            FIELDS_MAPPING=field_mapping,
        )

        # Geometry transformation.
        self._check_cancel()
        transbase = refactored
        keep_biggest_part = self._keep_biggest_part(grp)
        if keep_biggest_part and grp.source_geometry != 2:
            # Lines and points: regroup the parts of each feature (split in the
            # base layer) without dissolving. Dissolve nodes a self-crossing
            # line into pieces and keepnbiggestparts (polygons only) drops
            # every multi-part line or point, with its label. A line label
            # then sits on the longest part (_LONGEST_PART), as in QGIS.
            transbase = self._run_alg_safe(
                "collect", "native", INPUT=refactored, FIELD=[f"{_FIELD_PREFIX}_orig_id"])
            if grp.source_geometry == 1:  # QGIS labels the longest part only
                transbase = self._run_alg_safe(
                    "geometrybyexpression", "native", INPUT=transbase, OUTPUT_GEOMETRY=1,
                    EXPRESSION=self._LONGEST_PART)
        elif keep_biggest_part:
            dissolved = self._run_alg_safe(
                "dissolve", "native",
                INPUT=refactored,
                FIELD=[f'{_FIELD_PREFIX}_orig_id'],
                SEPARATE_DISJOINT=False
            )
            singlepart = self._run_alg_safe(
                "keepnbiggestparts", "native",
                POLYGONS=dissolved,
                PARTS=1
            )
            transbase = singlepart
        # layer.geometryType() returns 0 for point and 2 for polygon
        # but geometrybyexpression processing treats 0 as polygon and 2 as point
        # so we need to flip these values to get the correct geometry type.
        if not self.validate_expression(grp, grp.geometry_expression):
                return None
        geom_target = abs(grp.geometry_target - 2)
        # Pattern markers in QGIS's drawing order: their rank rides as z.
        ranked = grp.recipe is not None and grp.recipe.kind == "grid_points" and \
            bool(grp.recipe.param("ordered")) and geom_target == 2
        transformed = self._run_alg_safe(
            "geometrybyexpression", "native",
            INPUT=transbase,
            OUTPUT_GEOMETRY=geom_target,
            EXPRESSION=grp.geometry_expression,
            **({"WITH_Z": True} if ranked else {}),
        )
        check = self._open(transformed, "check")
        if not check.isValid() or check.featureCount() <= 0:
            self._report_empty_output(grp, transbase)
            return None
        if ranked:
            del check
            transformed = self._ranked_points(transformed)
            if transformed is None:
                self._report_empty_output(grp, transbase)
                return None
            check = self._open(transformed, "check")
        anchors = [name for name in (mat.ANCHOR_X_FIELD, mat.ANCHOR_Y_FIELD)
                   if check.fields().indexFromName(name) >= 0]
        if anchors:  # only needed to build the pieces' grids
            transformed = self._run_alg_safe("deletecolumn", "native", INPUT=transformed,
                                             COLUMN=anchors)

        self._check_cancel()
        # Random fill points stay one multipoint per polygon: vector tiles
        # carry multipoints and the viewer places a symbol at every point, so
        # splitting hundreds of thousands of them would only cost time.
        multipoints = grp.recipe is not None and grp.recipe.kind == "random_points"
        cleaned = self._run_alg_safe(
            "removenullgeometries", "native",
            INPUT=transformed,
            REMOVE_EMPTY=True,
            **({"OUTPUT": output_path} if multipoints else {}),
        )
        check = self._open(cleaned, "check")
        if not check.isValid() or check.featureCount() <= 0:
            del check
            if multipoints and exists(output_path):
                os.remove(output_path)  # an existing output counts as done
            self._report_empty_output(grp, transbase)
            return None
        del check
        if multipoints:
            return cleaned
        self._check_cancel()
        if not grp.part_fields:
            return self._run_alg_safe(
                "multiparttosingleparts", "native",
                INPUT=cleaned,
                OUTPUT=output_path,
            )
        out = self._run_alg_safe("multiparttosingleparts", "native", INPUT=cleaned)
        for index, (ftype, expr, name) in enumerate(grp.part_fields):
            last = index == len(grp.part_fields) - 1
            out = self._run_alg_safe(
                "fieldcalculator", "native", INPUT=out, FIELD_NAME=name,
                FIELD_TYPE={6: 0, 2: 1, 4: 1}.get(ftype, 2), FIELD_LENGTH=0,
                FIELD_PRECISION=0, FORMULA=expr, **({"OUTPUT": output_path} if last else {}))
        return out

    def _generated_lines(self, source: str, grp: _RuleGroupSnapshot) -> Optional[str]:
        """Worker: the lines of a line-symbol geometry generator, one feature
        per part (QGIS draws the sub-symbol on every part), with the
        geometry-dependent properties evaluated on each part."""
        # geometrybyexpression: OUTPUT_GEOMETRY 1 = line
        out = self._run_alg_safe("geometrybyexpression", "native", INPUT=source,
                                 OUTPUT_GEOMETRY=1, EXPRESSION=grp.pre_generator)
        out = self._run_alg_safe("removenullgeometries", "native", INPUT=out, REMOVE_EMPTY=True)
        out = self._run_alg_safe("multiparttosingleparts", "native", INPUT=out)
        for ftype, expr, name in grp.generated_fields:
            out = self._run_alg_safe(
                "fieldcalculator", "native", INPUT=out, FIELD_NAME=name,
                FIELD_TYPE={6: 0, 2: 1, 4: 1}.get(ftype, 2), FIELD_LENGTH=0,
                FIELD_PRECISION=0, FORMULA=expr)
        check = self._open(out, "check")
        if not check.isValid() or check.featureCount() <= 0:
            return None
        return out

    def _report_empty_output(self, grp: _RuleGroupSnapshot, source: str) -> None:
        matched = self._open(source, "matched")
        count = matched.featureCount() if matched.isValid() else 0
        if count > 0:
            self.diagnostics.add(
                "Q2VT_RULE_OUTPUT_EMPTY",
                f"{grp.description}: {count} matching feature(s) but the geometry "
                f"expression produced no geometry.",
                layer_id=grp.layer_id, component=grp.output_dataset,
                detail=grp.geometry_expression)

    def _ranked_points(self, source: str) -> Optional[str]:
        """Worker: pattern markers as single points in QGIS's drawing order,
        so the viewer stacks overlapping markers as QGIS does (symbol-z-order
        "source"): feature by feature, part by part, then by the rank each
        point carries as z (column by column, rows from the top, see
        mat.grid_expression). Written without an index or feature ids, which
        would restore the old order."""
        from qgis.core import (QgsFeature, QgsFields, QgsGeometry, QgsPointXY,  # pylint: disable=import-outside-toplevel
                               QgsProject, QgsVectorFileWriter, QgsWkbTypes)
        layer = self._open(source, "ranked_src")
        if not layer.isValid():
            return None
        names = layer.fields().names()
        anchors = [name for name in (mat.ANCHOR_X_FIELD, mat.ANCHOR_Y_FIELD) if name in names]
        rank_field = ORDER_FIELD if ORDER_FIELD in names else f"{_FIELD_PREFIX}_orig_id"
        fields = QgsFields()
        for field in layer.fields():
            if field.name().lower() not in ("fid", "ogc_fid") and field.name() not in anchors:
                fields.append(field)

        def number(value):
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0
        first_of_part, attributes, points = {}, {}, []
        for feature in layer.getFeatures():
            self._check_cancel()
            rank = number(feature[rank_field]) if rank_field in names else 0.0
            # The pieces of one polygon part share its grid anchor.
            part = (rank,) + tuple(number(feature[name]) for name in anchors) if anchors \
                else (rank, feature.id())
            first = first_of_part.setdefault(part, feature.id())
            attributes[feature.id()] = [feature[field.name()] for field in fields]
            for vertex in feature.geometry().vertices():
                z = vertex.z()
                points.append((rank, first, 0.0 if z != z else z, vertex.x(), vertex.y(),
                               feature.id()))
        if not points:
            return None
        points.sort(key=lambda item: item[:3])
        out = self._temp_path("ranked")[:-len(_TEMP_RULE_FORMAT)] + "fgb"
        with self._temp_files_lock:
            self._temp_files.add(out)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "FlatGeobuf"
        options.layerOptions = ["SPATIAL_INDEX=NO"]  # an index reorders the points
        writer = QgsVectorFileWriter.create(out, fields, QgsWkbTypes.Point, layer.crs(),
                                            QgsProject.instance().transformContext(), options)
        for _, _, _, x, y, source_id in points:
            out_feature = QgsFeature(fields)
            out_feature.setAttributes(attributes[source_id])
            out_feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
            writer.addFeature(out_feature)
        del writer
        return out

    def _pattern_pieces(self, source: str, recipe: Recipe) -> str:
        """Worker: polygons cut into pieces for a pattern grid. Each keeps its
        feature's grid anchor, so the grid is continuous across pieces; a
        piece has at most ``mat.PIECE_CELLS`` cells per side and
        ``mat.PIECE_MAX_NODES`` vertices, so clipping and point-in-polygon
        tests stay cheap however large or detailed the polygon."""
        export_crs = f"EPSG:{_EPSG_CRS}"
        anchor_x, anchor_y = mat.grid_anchor_expressions(recipe, export_crs)
        out = source
        for name, formula in ((mat.ANCHOR_X_FIELD, anchor_x), (mat.ANCHOR_Y_FIELD, anchor_y)):
            out = self._run_alg_safe("fieldcalculator", "native", INPUT=out, FIELD_NAME=name,
                                     FIELD_TYPE=0, FIELD_LENGTH=0, FIELD_PRECISION=0,
                                     FORMULA=formula)  # exact: markers on the edge stay
        out = self._run_alg_safe("geometrybyexpression", "native", INPUT=out,
                                 OUTPUT_GEOMETRY=0,
                                 EXPRESSION=mat.piece_cut_expression(recipe, export_crs))
        out = self._run_alg_safe("multiparttosingleparts", "native", INPUT=out)
        out = self._run_alg_safe("subdivide", "native", INPUT=out,
                                 MAX_NODES=mat.PIECE_MAX_NODES)
        return self._run_alg_safe("multiparttosingleparts", "native", INPUT=out)

    def _color_bands(self, source: str, recipe: Recipe, min_zoom: int = 0) -> Optional[str]:
        """Worker: every colour band of a gradient / shapeburst fill as its
        own polygon (BAND_FIELD, COLOR_FIELD), all in one dataset, bands in
        order (see materialize.color_bands_recipe)."""
        from qgis.core import (QgsFeature, QgsField, QgsFields, QgsGeometry,  # pylint: disable=import-outside-toplevel
                               QgsProject, QgsVectorFileWriter, QgsWkbTypes)
        from .fidelity.bands import BandBuilder  # pylint: disable=import-outside-toplevel
        layer = self._open(source, "bands_src")
        if not layer.isValid():
            return None
        # Bands under a pixel wide are merged, two zooms past the archive.
        builder = BandBuilder(recipe, f"EPSG:{_EPSG_CRS}", float(self.max_zoom) + 2.0, float(min_zoom))
        colors = recipe.param("colors")
        fields = QgsFields()
        for field in layer.fields():
            if field.name().lower() not in ("fid", "ogc_fid"):
                fields.append(field)
        fields.append(QgsField(mat.BAND_FIELD, QVariant.Int))
        fields.append(QgsField(mat.COLOR_FIELD, QVariant.String))
        out = self._temp_path("bands")[:-len(_TEMP_RULE_FORMAT)] + "fgb"
        with self._temp_files_lock:
            self._temp_files.add(out)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "FlatGeobuf"
        options.layerOptions = ["SPATIAL_INDEX=NO"]  # an index reorders the bands
        writer = QgsVectorFileWriter.create(out, fields, QgsWkbTypes.MultiPolygon, layer.crs(),
                                            QgsProject.instance().transformContext(), options)
        # Band order: the whole layer's band 0 first, then band 1, ...
        # (also fill-sort-key in the style).
        per_band = [[] for _ in colors]
        for feature in layer.getFeatures():
            self._check_cancel()
            for band, geometry in builder.build(feature.geometry()):
                if QgsWkbTypes.geometryType(geometry.wkbType()) != QgsWkbTypes.PolygonGeometry:
                    parts = [g for g in geometry.asGeometryCollection()
                             if QgsWkbTypes.geometryType(g.wkbType()) == QgsWkbTypes.PolygonGeometry]
                    if not parts:
                        continue
                    geometry = QgsGeometry.collectGeometry(parts)
                geometry.convertToMultiType()
                out_feature = QgsFeature(fields)
                for field in fields:
                    if field.name() not in (mat.BAND_FIELD, mat.COLOR_FIELD):
                        out_feature[field.name()] = feature[field.name()]
                out_feature[mat.BAND_FIELD] = band
                out_feature[mat.COLOR_FIELD] = colors[band]
                out_feature.setGeometry(geometry)
                per_band[band].append(out_feature)
        written = 0
        for features in per_band:
            for out_feature in features:
                writer.addFeature(out_feature)
                written += 1
        del writer
        return out if written else None

    def _point_grouping(self, source: str, zoom: int, params: dict):
        """(layer, features in drawing order, their points and groups in the
        grouping CRS, transforms) of a point cluster / displacement layer at
        one zoom; computed once per source and zoom."""
        from qgis.core import QgsFeatureRequest  # pylint: disable=import-outside-toplevel
        from .fidelity import point_groups as pg  # pylint: disable=import-outside-toplevel
        key = (source, zoom, params.get("tolerance"), params.get("crs"))
        with self._point_group_lock:
            cached = self._point_group_cache.get(key)
            if cached is not None:
                return cached
            layer = self._open(source, "point_groups")
            if not layer.isValid():
                return None
            target = QgsCoordinateReferenceSystem(params.get("crs") or layer.crs().authid())
            to_crs = QgsCoordinateTransform(layer.crs(), target, self._transform_context)
            from_crs = QgsCoordinateTransform(target, layer.crs(), self._transform_context)
            order_field = f"{_FIELD_PREFIX}_orig_id"
            request = QgsFeatureRequest()
            if layer.fields().indexOf(order_field) >= 0:  # QGIS draws in feature order
                request.setOrderBy(QgsFeatureRequest.OrderBy([
                    QgsFeatureRequest.OrderByClause(order_field, True)]))
            features, points = [], []
            for feature in layer.getFeatures(request):
                geometry = feature.geometry()
                if geometry.isEmpty():
                    continue
                point = geometry.centroid().asPoint() if geometry.isMultipart() else geometry.asPoint()
                point = to_crs.transform(point)
                features.append(feature)
                points.append((point.x(), point.y()))
            groups = pg.group_points(points, float(params.get("tolerance") or 0.0))
            cached = (layer, features, points, groups, from_crs)
            self._point_group_cache[key] = cached
            return cached

    def _point_groups(self, source: str, point_group: tuple) -> Optional[str]:
        """Worker: one role of a point cluster / displacement renderer at one
        zoom (FlattenedRule.point_group = (mode, role, zoom, params)):

        * members: cluster - the points left alone; displacement - every
          point, the grouped ones moved around their group's centre;
        * cluster / center: one point per group of two or more at its
          centroid, with CLUSTER_SIZE_FIELD (the first member's attributes);
        * circle / grid: the displacement circle or grid lines."""
        from qgis.core import (QgsFeature, QgsField, QgsFields, QgsGeometry, QgsPointXY,  # pylint: disable=import-outside-toplevel
                               QgsProject, QgsVectorFileWriter, QgsWkbTypes)
        from .fidelity import point_groups as pg  # pylint: disable=import-outside-toplevel
        mode, role, zoom, params = point_group
        params = dict(params)
        grouping = self._point_grouping(source, zoom, params)
        if grouping is None:
            return None
        layer, features, points, groups, from_crs = grouping
        fields = QgsFields()
        for field in layer.fields():
            if field.name().lower() not in ("fid", "ogc_fid"):
                fields.append(field)
        if role in ("cluster", "center"):
            fields.append(QgsField(mat.CLUSTER_SIZE_FIELD, QVariant.Int))
            fields.append(QgsField(mat.CLUSTER_COLOR_FIELD, QVariant.String))
        kind = QgsWkbTypes.LineString if role in ("circle", "grid") else QgsWkbTypes.Point
        out = self._temp_path("groups")[:-len(_TEMP_RULE_FORMAT)] + "fgb"
        with self._temp_files_lock:
            self._temp_files.add(out)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "FlatGeobuf"
        options.layerOptions = ["SPATIAL_INDEX=NO"]  # keep the drawing order
        writer = QgsVectorFileWriter.create(out, fields, kind, layer.crs(),
                                            QgsProject.instance().transformContext(), options)

        def back(x, y):
            return from_crs.transform(QgsPointXY(x, y))

        def write(source_feature, geometry, size=None):
            feature = QgsFeature(fields)
            for field in fields:
                if field.name() not in (mat.CLUSTER_SIZE_FIELD, mat.CLUSTER_COLOR_FIELD):
                    feature[field.name()] = source_feature[field.name()]
            if size is not None:
                feature[mat.CLUSTER_SIZE_FIELD] = size
            feature.setGeometry(geometry)
            writer.addFeature(feature)

        written = 0
        placement = params.get("placement", pg.RING)
        for members in groups:
            self._check_cancel()
            if len(members) < 2:
                if role == "members":
                    write(features[members[0]], features[members[0]].geometry())
                    written += 1
                continue
            center = pg.centroid(points, members)
            if role in ("cluster", "center"):
                write(features[members[0]], QgsGeometry.fromPointXY(back(*center)), len(members))
                written += 1
                continue
            if mode != "displacement":
                continue  # clustered points are drawn by the cluster symbol only
            positions, radius, size = pg.displaced(
                center, len(members), placement, params.get("symbol_diagonal", 0.0),
                params.get("center_diagonal", 0.0), params.get("addition", 0.0))
            if role == "members":
                for index, (x, y) in zip(members, positions):
                    write(features[index], QgsGeometry.fromPointXY(back(x, y)))
                    written += 1
            elif role == "circle" and radius:
                ring = [back(center[0] + radius * math.sin(2 * math.pi * k / 72),
                             center[1] + radius * math.cos(2 * math.pi * k / 72)) for k in range(73)]
                write(features[members[0]], QgsGeometry.fromPolylineXY(ring))
                written += 1
            elif role == "grid" and size:
                for a, b in pg.grid_lines(positions, size):
                    write(features[members[0]], QgsGeometry.fromPolylineXY([back(*a), back(*b)]))
                    written += 1
        del writer
        return out if written else None

    def _merged_features(self, source: str, grp: _RuleGroupSnapshot) -> Optional[str]:
        """Worker: one feature for the whole rule group, as QGIS draws it.
        "merge" (merged feature renderer): the union of the features (lines
        merged into continuous lines). "invert" (inverted polygon renderer):
        an area well beyond the export extent minus the union, so its outer
        edge never reaches a tile and only the polygon edges are outlined."""
        from qgis.core import (QgsFeature, QgsFields, QgsGeometry, QgsProject,  # pylint: disable=import-outside-toplevel
                               QgsVectorFileWriter, QgsWkbTypes)
        layer = self._open(source, "merge_src")
        if not layer.isValid():
            return None
        geometries = [f.geometry() for f in layer.getFeatures() if not f.geometry().isEmpty()]
        self._check_cancel()
        union = QgsGeometry.unaryUnion(geometries) if geometries else QgsGeometry()
        kind = QgsWkbTypes.geometryType(layer.wkbType())
        if grp.merge == "invert" and grp.geometry_target == 1:
            # The outline of the inverted area is the polygons' own edge.
            if union.isEmpty():
                return None
            result = union
        elif grp.merge == "invert":
            if kind != QgsWkbTypes.PolygonGeometry:
                return source
            area = QgsRectangle(self.extent)
            area.grow(max(area.width(), area.height()))
            crs = layer.crs()
            if crs.authid() != f"EPSG:{_EPSG_CRS}":
                area = QgsCoordinateTransform(QgsCoordinateReferenceSystem(f"EPSG:{_EPSG_CRS}"), crs,
                                              self._transform_context).transformBoundingBox(area)
            if crs.isGeographic():
                area = area.intersect(QgsRectangle(-180, -85.06, 180, 85.06))
            outside = QgsGeometry.fromRect(area)
            result = outside.difference(union) if not union.isEmpty() else outside
        else:
            if union.isEmpty():
                return None
            result = union.mergeLines() if kind == QgsWkbTypes.LineGeometry else union
        if result.isEmpty():
            return None
        result.convertToMultiType()
        out = self._temp_path("merged")[:-len(_TEMP_RULE_FORMAT)] + "fgb"
        with self._temp_files_lock:
            self._temp_files.add(out)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "FlatGeobuf"
        fields = QgsFields()
        for field in layer.fields():
            if field.name().lower() not in ("fid", "ogc_fid"):
                fields.append(field)
        writer = QgsVectorFileWriter.create(out, fields, result.wkbType(), layer.crs(),
                                            QgsProject.instance().transformContext(), options)
        feature = QgsFeature(fields)
        first = next(iter(layer.getFeatures()), None)
        if first is not None:  # the symbol's own data-defined values (one per group)
            for field in fields:
                feature[field.name()] = first[field.name()]
        feature.setGeometry(result)
        writer.addFeature(feature)
        del writer
        return out

    def _interpolated_segments(self, source: str, recipe: Recipe) -> Optional[str]:
        """Worker: an interpolated line as pieces along each line (part), each
        with the colour and width QGIS computes for the middle of the piece
        (COLOR_FIELD, WIDTH_FIELD). The start / end values are evaluated per
        feature, as QGIS does, and interpolated by length along the part."""
        from qgis.core import (QgsExpression, QgsExpressionContext,  # pylint: disable=import-outside-toplevel
                               QgsExpressionContextUtils, QgsFeature, QgsField, QgsFields,
                               QgsGeometry, QgsInterpolatedLineColor, QgsInterpolatedLineWidth,
                               QgsProject, QgsReadWriteContext, QgsSymbolLayerUtils,
                               QgsVectorFileWriter, QgsWkbTypes)
        from qgis.PyQt.QtXml import QDomDocument  # pylint: disable=import-outside-toplevel
        layer = self._open(source, "interpolated_src")
        if not layer.isValid():
            return None

        def restore(kind, xml):
            item = kind()
            doc = QDomDocument()
            if xml and doc.setContent(xml)[0]:
                item.readXml(doc.documentElement(), QgsReadWriteContext())
            return item
        color = restore(QgsInterpolatedLineColor, recipe.param("color"))
        width = restore(QgsInterpolatedLineWidth, recipe.param("width"))
        context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))

        def prepared(pair):
            out = []
            for text in pair:
                expression = QgsExpression(text) if text else None
                if expression is not None:
                    expression.prepare(context)
                out.append(expression)
            return out
        color_exprs, width_exprs = prepared(recipe.param("color_values")), prepared(recipe.param("width_values"))
        varies_color = color.coloringMethod() == QgsInterpolatedLineColor.ColorRamp and all(color_exprs)
        varies_width = width.isVariableWidth() and all(width_exprs)
        shader = color.colorRampShader()
        color_span = abs(shader.maximumValue() - shader.minimumValue()) or 1.0
        width_span = abs(width.maximumValue() - width.minimumValue()) or 1.0
        steps = recipe.param("steps", 64)

        def number(expression):
            value = expression.evaluate(context) if expression is not None else None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        fields = QgsFields()
        for field in layer.fields():
            if field.name().lower() not in ("fid", "ogc_fid"):
                fields.append(field)
        fields.append(QgsField(mat.COLOR_FIELD, QVariant.String))
        fields.append(QgsField(mat.WIDTH_FIELD, QVariant.Double))
        out = self._temp_path("interpolated")[:-len(_TEMP_RULE_FORMAT)] + "fgb"
        with self._temp_files_lock:
            self._temp_files.add(out)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "FlatGeobuf"
        options.layerOptions = ["SPATIAL_INDEX=NO"]  # keep the drawing order
        writer = QgsVectorFileWriter.create(out, fields, QgsWkbTypes.LineString, layer.crs(),
                                            QgsProject.instance().transformContext(), options)
        written = 0
        for feature in layer.getFeatures():
            self._check_cancel()
            context.setFeature(feature)
            c1, c2 = (number(e) for e in color_exprs) if varies_color else (None, None)
            w1, w2 = (number(e) for e in width_exprs) if varies_width else (None, None)
            pieces = 1
            if c1 is not None and c2 is not None:
                pieces = max(pieces, math.ceil(steps * abs(c2 - c1) / color_span))
            if w1 is not None and w2 is not None:
                pieces = max(pieces, math.ceil(steps * abs(w2 - w1) / width_span))
            pieces = min(pieces, 4 * steps)
            geometry = feature.geometry()
            if geometry.isEmpty():
                continue
            for part in geometry.constParts():
                length = part.length()
                if length <= 0:
                    continue
                for piece in range(pieces):
                    t0, t1 = piece / pieces, (piece + 1) / pieces
                    middle = (t0 + t1) / 2
                    line = QgsGeometry(part.curveSubstring(t0 * length, t1 * length))
                    if line.isEmpty():
                        continue
                    if c1 is not None and c2 is not None:
                        rgba = color.color(c1 + (c2 - c1) * middle)
                    else:
                        rgba = color.color(c1 if c1 is not None else 0.0)
                    if w1 is not None and w2 is not None:
                        stroke = width.strokeWidth(w1 + (w2 - w1) * middle)
                    elif width.isVariableWidth():
                        stroke = 0.0
                    else:
                        stroke = width.fixedStrokeWidth()
                    if stroke <= 0 or not rgba.isValid() or rgba.alpha() == 0:
                        continue  # out of range (ignored) or nothing to draw
                    out_feature = QgsFeature(fields)
                    for field in fields:
                        if field.name() not in (mat.COLOR_FIELD, mat.WIDTH_FIELD):
                            out_feature[field.name()] = feature[field.name()]
                    out_feature[mat.COLOR_FIELD] = QgsSymbolLayerUtils.encodeColor(rgba)
                    out_feature[mat.WIDTH_FIELD] = float(stroke)
                    out_feature.setGeometry(line)
                    writer.addFeature(out_feature)
                    written += 1
        del writer
        return out if written else None

    def _arrow_polygons(self, source: str, recipe: Recipe) -> Optional[str]:
        """Worker: the polygons QGIS fills for an arrow symbol layer
        (``fidelity/arrows.py``), built in painter pixels (y down) of the
        recipe's zoom in the recipe CRS (the project CRS, like QGIS)."""
        from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,  # pylint: disable=import-outside-toplevel
                               QgsFeature, QgsFields, QgsGeometry, QgsPointXY, QgsProject,
                               QgsVectorFileWriter, QgsWkbTypes)
        from .fidelity.arrows import arrow_polygons  # pylint: disable=import-outside-toplevel
        layer = self._open(source, "arrows")
        if layer is None or not layer.isValid():
            return None
        pixel = float(recipe.param("pixel"))
        start, width, head_length, thickness, offset = (v / pixel for v in recipe.param("sizes"))
        curved, repeated = bool(recipe.param("curved")), bool(recipe.param("repeated"))
        head_type, arrow_type = int(recipe.param("head_type")), int(recipe.param("arrow_type"))
        crs = QgsCoordinateReferenceSystem(recipe.param("crs") or f"EPSG:{_EPSG_CRS}")
        to_crs = from_crs = None
        if crs.isValid() and crs != layer.crs():
            context = QgsProject.instance().transformContext()
            to_crs = QgsCoordinateTransform(layer.crs(), crs, context)
            from_crs = QgsCoordinateTransform(crs, layer.crs(), context)
        fields = QgsFields()
        for field in layer.fields():
            if field.name().lower() not in ("fid", "ogc_fid"):
                fields.append(field)
        out = self._temp_path("arrows")[:-len(_TEMP_RULE_FORMAT)] + "fgb"
        with self._temp_files_lock:
            self._temp_files.add(out)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "FlatGeobuf"
        options.layerOptions = ["SPATIAL_INDEX=NO"]  # keep the drawing order
        writer = QgsVectorFileWriter.create(out, fields, QgsWkbTypes.MultiPolygon, layer.crs(),
                                            QgsProject.instance().transformContext(), options)
        # Opaque multi-layer fills: QGIS fills every arrow with all layers
        # before the next arrow, so a later arrow's shadow covers an earlier
        # arrow. Each layer then keeps only its visible part (painter order).
        shifts = recipe.param("painter")
        target = int(recipe.param("layer", 0))
        written = 0
        for feature in layer.getFeatures():
            self._check_cancel()
            geometry = QgsGeometry(feature.geometry())
            if geometry.isEmpty():
                continue
            if to_crs is not None:
                geometry.transform(to_crs)
            shapes = []
            for part in geometry.constParts():
                line = part.curveToLine() if part.hasCurvedSegments() else part
                points = [(line.xAt(i), line.yAt(i)) for i in range(line.numPoints())]
                # QGIS draws nothing for repeated vertices (zero-length segments).
                points = [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
                if not points:
                    continue
                x0, y0 = points[0]
                pixels = [((x - x0) / pixel, (y0 - y) / pixel) for x, y in points]
                for polygon in arrow_polygons(pixels, curved, repeated, start, width, head_length,
                                              thickness, head_type, arrow_type, offset):
                    shape = QgsGeometry.fromPolygonXY([[
                        QgsPointXY(x0 + x * pixel, y0 - y * pixel) for x, y in polygon]])
                    shape = shape.makeValid()  # Qt fills self-crossings odd-even
                    shape.convertGeometryCollectionToSubclass(QgsWkbTypes.PolygonGeometry)
                    if not shape.isEmpty():
                        shapes.append(shape)
            if shifts:
                shapes = self._painter_visible(shapes, shifts, target)
            for shape in shapes:
                if from_crs is not None:
                    shape.transform(from_crs)
                shape.convertToMultiType()
                out_feature = QgsFeature(fields)
                for field in fields:
                    out_feature[field.name()] = feature[field.name()]
                out_feature.setGeometry(shape)
                writer.addFeature(out_feature)
                written += 1
        del writer
        return out if written else None

    def _direction_runs(self, source: str, recipe: Recipe, source_geometry: int) -> Optional[str]:
        """Worker: lines cut into runs of segments whose screen direction
        falls in the same bucket (mat.DIRECTION_FIELD, 0 = east, clockwise
        on screen), for inner effect strips (fidelity/line_effects.py)."""
        from qgis.core import (QgsFeature, QgsField, QgsFields, QgsGeometry,  # pylint: disable=import-outside-toplevel
                               QgsLineString, QgsProject, QgsVectorFileWriter, QgsWkbTypes)
        layer = self._open(source, "runs")
        if layer is None or not layer.isValid():
            return None
        buckets = int(recipe.param("buckets"))
        window = float(recipe.param("window", 0.0) or 0.0)
        step = 2 * math.pi / buckets
        fields = QgsFields()
        for field in layer.fields():
            if field.name().lower() not in ("fid", "ogc_fid") and \
                    field.name() not in (mat.DIRECTION_FIELD, mat.RUN_FIELD):
                fields.append(field)
        fields.append(QgsField(mat.DIRECTION_FIELD, QVariant.Int))
        fields.append(QgsField(mat.RUN_FIELD, QVariant.Int))
        out = self._temp_path("runs")[:-len(_TEMP_RULE_FORMAT)] + "fgb"
        with self._temp_files_lock:
            self._temp_files.add(out)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "FlatGeobuf"
        options.layerOptions = ["SPATIAL_INDEX=NO"]  # keep the drawing order
        writer = QgsVectorFileWriter.create(out, fields, QgsWkbTypes.LineString, layer.crs(),
                                            QgsProject.instance().transformContext(), options)
        written = 0

        def write(feature, points, bucket):
            out_feature = QgsFeature(fields)
            for field in fields:
                if field.name() not in (mat.DIRECTION_FIELD, mat.RUN_FIELD):
                    out_feature[field.name()] = feature[field.name()]
            out_feature[mat.DIRECTION_FIELD] = bucket
            out_feature[mat.RUN_FIELD] = written
            out_feature.setGeometry(QgsGeometry(QgsLineString(points)))
            writer.addFeature(out_feature)
        for feature in layer.getFeatures():
            self._check_cancel()
            geometry = feature.geometry()
            if geometry.isEmpty():
                continue
            if source_geometry == 2:
                geometry = QgsGeometry(geometry.constGet().boundary())
            for part in geometry.constParts():
                line = part.curveToLine() if part.hasCurvedSegments() else part
                points = [line.pointN(i) for i in range(line.numPoints())]
                along = [0.0]
                for a, b in zip(points, points[1:]):
                    along.append(along[-1] + math.hypot(b.x() - a.x(), b.y() - a.y()))
                run, bucket = [], None
                for index, (a, b) in enumerate(zip(points, points[1:])):
                    if along[index + 1] == along[index]:
                        continue
                    middle = (along[index] + along[index + 1]) / 2
                    start = line.interpolatePoint(max(0.0, middle - window / 2)) if window else a
                    end = line.interpolatePoint(min(along[-1], middle + window / 2)) if window else b
                    dx, dy = end.x() - start.x(), end.y() - start.y()
                    if dx == 0 and dy == 0:
                        dx, dy = b.x() - a.x(), b.y() - a.y()
                    # Screen y points down: the screen direction is (dx, -dy).
                    here = int(math.floor((math.atan2(-dy, dx) % (2 * math.pi)) / step)) % buckets
                    if here != bucket and run:
                        write(feature, run, bucket)
                        written += 1
                        run = [a]
                    elif not run:
                        run = [a]
                    bucket = here
                    run.append(b)
                if len(run) >= 2:
                    write(feature, run, bucket)
                    written += 1
        del writer
        return out if written else None

    def _label_windows(self, source: str, grp: _RuleGroupSnapshot) -> Optional[str]:
        """Worker: repeated curved line labels laid out at the recipe's zoom
        as QGIS lays them out (core/label_lines.py): one short line per
        label, along its characters and inside one tile, with the feature's
        fields; MapLibre centres the label on it ("line-center")."""
        from qgis.core import NULL, QgsFeature, QgsFields, QgsLineString, QgsPoint  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtGui import QFont, QFontMetrics, QFontMetricsF  # pylint: disable=import-outside-toplevel
        recipe = grp.recipe
        text = next((expr for _, expr, name in grp.expression_fields
                     if name == f"{_FIELD_PREFIX}_label"), None)
        layer = self._open(source, "windows")
        if not text or layer is None or not layer.isValid():
            return None
        expression = QgsExpression(text)
        context = self._worker_expression_context()
        context.appendScope(QgsExpressionContextUtils.layerScope(layer))
        expression.prepare(context)
        zoom, size = int(recipe.param("zoom")), float(recipe.param("size"))
        fit, chop = float(recipe.param("fit", 1.0)), float(recipe.param("chop", 1.0))
        # The advances MapLibre lays the label out with: the glyphs'
        # (GlyphGenerator: whole pixels of a 24 px em) and the letter spacing
        # between them.
        font = QFont()
        font.fromString(recipe.param("font"))
        font.setPixelSize(24)
        metrics = QFontMetrics(font)
        spacing = float(recipe.param("spacing", 0.0)) * size
        transform = recipe.param("transform", "none")
        # pal's candidate step: a sixth of the text height, or the engine's
        # line candidates per centimetre (at 96 dpi), whichever is longer.
        per_cm = float(recipe.param("candidates_per_cm", 5.0)) or 5.0
        step = max(QFontMetricsF(font).height() * size / 24 / 6, 10.0 * 96 / 25.4 / per_cm)
        settings = dict(repeat=float(recipe.param("repeat")), step=step,
                        max_in=float(recipe.param("max_in", 25.0)),
                        max_out=float(recipe.param("max_out", -25.0)),
                        max_angle=float(recipe.param("max_angle", 25.0)),
                        angle_window=0.6 * size * fit, anchor=float(recipe.param("anchor", 0.5)),
                        margin=max(2.0, 0.25 * size * fit))
        widths: Dict[str, List[float]] = {}

        def advances(label: str) -> List[float]:
            if label not in widths:
                shown = label.upper() if transform == "uppercase" else \
                    label.lower() if transform == "lowercase" else label
                drawn = [char for char in shown.strip() if metrics.inFontUcs4(ord(char))]
                widths[label] = [metrics.horizontalAdvance(char) * size / 24
                                 + (spacing if i < len(drawn) - 1 else 0.0)
                                 for i, char in enumerate(drawn)]
            return widths[label]

        # QGIS labels only the longest part of a feature unless every part
        # is labelled (the base layer has the parts as features).
        orig = layer.fields().indexFromName(f"{_FIELD_PREFIX}_orig_id")
        chosen = None
        if not recipe.param("per_part") and orig >= 0:
            longest: Dict[Any, Tuple[float, int]] = {}
            for feature in layer.getFeatures():
                length = feature.geometry().length() if feature.hasGeometry() else 0.0
                key = feature.attribute(orig)
                if key not in longest or length > longest[key][0]:
                    longest[key] = (length, feature.id())
            chosen = {fid for _, fid in longest.values()}
        fields = QgsFields()
        for field in layer.fields():
            if field.name().lower() not in ("fid", "ogc_fid"):
                fields.append(field)
        out = self._temp_path("windows")[:-len(_TEMP_RULE_FORMAT)] + "fgb"
        with self._temp_files_lock:
            self._temp_files.add(out)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "FlatGeobuf"
        options.layerOptions = ["SPATIAL_INDEX=NO"]  # keep the drawing order
        writer = QgsVectorFileWriter.create(out, fields, QgsWkbTypes.LineString, layer.crs(),
                                            QgsProject.instance().transformContext(), options)
        world = 2 * math.pi * 6378137.0  # EPSG:3857 metres -> CSS px of the zoom
        pixel, half = world / (512.0 * 2 ** zoom), world / 2
        written = 0
        for feature in layer.getFeatures():
            self._check_cancel()
            if (chosen is not None and feature.id() not in chosen) or not feature.hasGeometry():
                continue
            context.setFeature(feature)
            value = expression.evaluate(context)
            if expression.hasEvalError() or value is None or value == NULL:
                continue
            drawn = advances(str(value))
            if not drawn:
                continue
            for part in feature.geometry().constParts():
                line = part.curveToLine() if part.hasCurvedSegments() else part
                xs = [(line.xAt(i) + half) / pixel for i in range(line.numPoints())]
                ys = [(half - line.yAt(i)) / pixel for i in range(line.numPoints())]
                for window in label_lines.label_windows(
                        xs, ys, [width * fit for width in drawn],
                        chop_width=sum(drawn) * chop, **settings):
                    out_feature = QgsFeature(fields)
                    for field in fields:
                        out_feature[field.name()] = feature[field.name()]
                    out_feature.setGeometry(QgsGeometry(QgsLineString(
                        [QgsPoint(x * pixel - half, half - y * pixel) for x, y in window])))
                    writer.addFeature(out_feature)
                    written += 1
        del writer
        return out if written else None

    @staticmethod
    def _painter_visible(shapes, shifts, target: int):
        """The visible part of fill layer ``target`` of each shape when every
        shape is drawn with all layers (shifted by ``shifts``, map units) in
        turn, returned unshifted (the layer's own shift is drawn on screen)."""
        from qgis.core import QgsGeometry, QgsSpatialIndex, QgsWkbTypes  # pylint: disable=import-outside-toplevel
        layers = len(shifts)
        drawn = []  # (order, geometry) of every layer of every shape
        for index, shape in enumerate(shapes):
            for k, (dx, dy) in enumerate(shifts):
                moved = QgsGeometry(shape)
                moved.translate(dx, dy)
                drawn.append((index * layers + k, moved))
        spatial = QgsSpatialIndex()
        for order, moved in drawn:
            spatial.addFeature(order, moved.boundingBox())
        visible = []
        dx, dy = shifts[target]
        for index in range(len(shapes)):
            order = index * layers + target
            mine = drawn[order][1]
            later = [drawn[i][1] for i in spatial.intersects(mine.boundingBox()) if i > order]
            if later:
                mine = mine.difference(QgsGeometry.unaryUnion(later))
                mine.convertGeometryCollectionToSubclass(QgsWkbTypes.PolygonGeometry)
            if mine.isEmpty():
                continue
            mine.translate(-dx, -dy)
            visible.append(mine)
        return visible

    def _random_points(self, source: str, recipe: Recipe) -> str:
        """Worker: random marker fill points with QGIS's native (prepared
        geometry) random points in polygons: ``count`` per feature, or
        ``ceil(count * area / densityArea)`` (area in the recipe CRS)."""
        from qgis.core import QgsProperty  # pylint: disable=import-outside-toplevel
        export_crs = f"EPSG:{_EPSG_CRS}"
        crs = recipe.param("crs") or export_crs
        geom = "@geometry" if crs == export_crs else \
            f"transform(@geometry, '{export_crs}', '{crs}')"
        count, density = recipe.param("count"), recipe.param("density")
        number = f"ceil({count} * area({geom}) / {density!r})" if density else str(count)
        # Every point copies its polygon's attributes, "fid" included, which
        # a GeoPackage rejects as a duplicate key: write FlatGeobuf, then
        # drop it.
        points = self._temp_path("rnd")[:-len(_TEMP_RULE_FORMAT)] + "fgb"
        with self._temp_files_lock:
            self._temp_files.add(points)
        points = self._run_alg_safe(
            "randompointsinpolygons", "native", INPUT=source,
            POINTS_NUMBER=QgsProperty.fromExpression(number), MIN_DISTANCE=0,
            MAX_TRIES_PER_POINT=50, SEED=max(1, int(recipe.param("seed") or 1)),
            INCLUDE_POLYGON_ATTRIBUTES=True, OUTPUT=points)
        # One multipoint per polygon: the later per-feature steps (fields,
        # geometry expression, cleaning) then touch a few features instead of
        # hundreds of thousands; the final single-part split restores points.
        fields = self._open(points, "fields").fields()
        if fields.indexFromName(f"{_FIELD_PREFIX}_orig_id") >= 0:
            points = self._run_alg_safe("collect", "native", INPUT=points,
                                        FIELD=[f"{_FIELD_PREFIX}_orig_id"])
        if fields.indexFromName("fid") >= 0:
            points = self._run_alg_safe("deletecolumn", "native", INPUT=points, COLUMN=["fid"])
        return points

    def _materialize_marker_points(self, source: str, recipe: Recipe, source_geometry: int) -> str:
        """Worker: exact marker-line positions as points with ``ANGLE_FIELD``."""
        lines = source
        if source_geometry == 2 and (recipe.param("offset") or recipe.param("ring_filter")):
            # QGIS buffers each ring (positive = inwards), see polygon_offset_expression.
            lines = self._run_alg_safe(
                "geometrybyexpression", "native", INPUT=source, OUTPUT_GEOMETRY=1,
                EXPRESSION=mat.polygon_offset_expression(recipe, f"EPSG:{_EPSG_CRS}"))
            # An inward offset wider than the polygon collapses the ring:
            # QGIS draws no markers there.
            lines = self._run_alg_safe("removenullgeometries", "native", INPUT=lines,
                                       REMOVE_EMPTY=True)
            lines = self._run_alg_safe("multiparttosingleparts", "native", INPUT=lines)
        elif source_geometry == 2:  # marker line on a polygon outline
            # One line per ring: QGIS starts every ring afresh.
            lines = self._run_alg_safe("polygonstolines", "native", INPUT=source)
            lines = self._run_alg_safe("multiparttosingleparts", "native", INPUT=lines)
        elif recipe.param("offset"):
            lines = self._run_alg_safe(
                "geometrybyexpression", "native", INPUT=lines, OUTPUT_GEOMETRY=1,
                EXPRESSION=mat.offset_line_expression(recipe, f"EPSG:{_EPSG_CRS}"))
            lines = self._run_alg_safe("removenullgeometries", "native", INPUT=lines,
                                       REMOVE_EMPTY=True)
        lines = self._run_alg_safe(
            "fieldcalculator", "native", INPUT=lines, FIELD_NAME=mat.COUNT_FIELD,
            FIELD_TYPE=1, FORMULA="num_points(@geometry)")
        outputs = []
        vertex_placements = [p for p in recipe.placements if p in mat.VERTEX_PLACEMENTS]
        if vertex_placements:
            vertices = self._run_alg_safe("extractvertices", "native", INPUT=lines)
            expression = " OR ".join(f"({mat.vertex_filter(p)})" for p in vertex_placements)
            selected = self._run_alg_safe("extractbyexpression", "native",
                                          INPUT=vertices, EXPRESSION=expression)
            outputs.append(self._run_alg_safe(
                "fieldcalculator", "native", INPUT=selected, FIELD_NAME=mat.ANGLE_FIELD,
                FIELD_TYPE=0, FORMULA='"angle"'))
        if "Interval" in recipe.placements and self._memory_active:
            outputs.append(self._interval_points_direct(lines, recipe))
        elif "Interval" in recipe.placements:
            multipoints = self._run_alg_safe(
                "geometrybyexpression", "native", INPUT=lines, OUTPUT_GEOMETRY=2, WITH_Z=True,
                EXPRESSION=mat.interval_points_expression(recipe, f"EPSG:{_EPSG_CRS}"))
            points = self._run_alg_safe("multiparttosingleparts", "native", INPUT=multipoints)
            angled = self._run_alg_safe(
                "fieldcalculator", "native", INPUT=points, FIELD_NAME=mat.ANGLE_FIELD,
                FIELD_TYPE=0, FORMULA="z(@geometry)")
            outputs.append(self._run_alg_safe("dropmzvalues", "native", INPUT=angled,
                                              DROP_M_VALUES=True, DROP_Z_VALUES=True))
        for placement in ("CentralPoint", "SegmentCenter"):
            if placement not in recipe.placements:
                continue
            base = lines
            if placement == "SegmentCenter":
                base = self._run_alg_safe("explodelines", "native", INPUT=lines)
            angled = self._run_alg_safe(
                "fieldcalculator", "native", INPUT=base, FIELD_NAME=mat.ANGLE_FIELD,
                FIELD_TYPE=0, FORMULA="line_interpolate_angle(@geometry, length(@geometry) / 2)")
            # geometrybyexpression: OUTPUT_GEOMETRY 2 = point
            outputs.append(self._run_alg_safe(
                "geometrybyexpression", "native", INPUT=angled, OUTPUT_GEOMETRY=2,
                EXPRESSION="line_interpolate_point(@geometry, length(@geometry) / 2)"))
        if len(outputs) == 1:
            return outputs[0]
        return self._run_alg_safe("mergevectorlayers", "native", LAYERS=outputs)

    def _build_field_mapping(
        self, grp: _RuleGroupSnapshot, current_input: str
    ) -> List[Dict[str, Any]]:
        """Worker: assemble the FIELDS_MAPPING list for refactorfields."""
        mapping: List[Tuple[int, str, str]] = []
        mapping.append(
            (10, grp.description, f"{_FIELD_PREFIX}_description")
        )
        mapping.extend(grp.expression_fields)
        if grp.include_required_fields_only != 0:
            # Read fields fresh in this worker thread; the QgsVectorLayer is
            # locally constructed and stays local.
            tmp = self._open(current_input, "tmp")
            if tmp.isValid():
                for f in tmp.fields():
                    if 'ogc_fid' not in f.name().lower():
                        mapping.append((f.type(), f'"{f.name()}"', f.name()))


        mapping.append(
            (6, f'"{_FIELD_PREFIX}_orig_id"', f"{_FIELD_PREFIX}_orig_id")
        )
        source_fields = self._open(current_input, "fields").fields()
        if source_fields.indexFromName(ORDER_FIELD) >= 0:
            mapping.append((2, f'"{ORDER_FIELD}"', ORDER_FIELD))
        if source_fields.indexFromName(FEATURE_KEY_FIELD) >= 0:
            mapping.append((10, f'"{FEATURE_KEY_FIELD}"', FEATURE_KEY_FIELD))
        for name in self.extra_tile_fields.get(grp.layer_id, []):
            if name == LABEL_ANCHOR_MARKER:
                if grp.rule_type == 1 and grp.source_geometry == 2:
                    mapping.extend(LABEL_ANCHOR_FIELDS)
                continue
            index = source_fields.indexFromName(name)
            if index >= 0 and name not in [m[2] for m in mapping]:
                mapping.append((source_fields.at(index).type(), f'"{name}"', name))
        for anchor in (mat.ANCHOR_X_FIELD, mat.ANCHOR_Y_FIELD):
            if source_fields.indexFromName(anchor) >= 0:
                mapping.append((6, f'"{anchor}"', anchor))
        if source_fields.indexFromName(mat.BAND_FIELD) >= 0:  # colour-band draw order
            mapping.append((2, f'"{mat.BAND_FIELD}"', mat.BAND_FIELD))
        if grp.pattern_anchor:
            whole = grp.pattern_anchor == "feature" and not grp.merge and \
                source_fields.indexFromName(self._BASE_ANCHOR_X) >= 0
            for name, base, axis in ((mat.PATTERN_ANCHOR_X_FIELD, self._BASE_ANCHOR_X, "x"),
                                     (mat.PATTERN_ANCHOR_Y_FIELD, self._BASE_ANCHOR_Y, "y")):
                mapping.append((6, f'"{base}"' if whole else self._anchor_expression(
                    grp.anchor_crs, axis, grp.pattern_anchor == "feature-clip"), name))
        for name in (mat.DIRECTION_FIELD, mat.RUN_FIELD):  # inner effect strips
            if source_fields.indexFromName(name) >= 0:
                mapping.append((2, f'"{name}"', name))
        return [
            {"type": m[0], "expression": m[1], "name": m[2]} for m in mapping
        ]

    # -------------------------------------------------------------------
    # Phase 4 — result collection (caller thread)
    # -------------------------------------------------------------------
    def _collect_results(
        self,
        rule_groups: List[_RuleGroupSnapshot],
        rule_outputs: Dict[str, Optional[str]],
    ) -> Tuple[List[QgsVectorLayer], List[FlattenedRule]]:
        """The successful outputs as layers (ExportedDataset: path, name and
        feature count read with SQLite; opening thousands of GeoPackages as
        QGIS layers took a fifth of a big export); report failures."""
        successful_rules: List[FlattenedRule] = []
        for grp in rule_groups:
            out_path = rule_outputs.get(grp.output_dataset)
            on_disk = join(self.utils_dir, f"{grp.output_dataset}.{_TEMP_RULE_FORMAT}")
            if not out_path or not exists(on_disk):
                # Drop these rules from the caller's flat list.
                for rule in grp.flat_rules:
                    if rule in self.flattened_rules:
                        self.flattened_rules.remove(rule)
                continue
            info = (self._output_info.get(grp.output_dataset) or gpkg_info(on_disk)) \
                if self.light_results else None
            if info is not None:
                layer = ExportedDataset(
                    on_disk, grp.output_dataset, info.count,
                    lambda path=on_disk, name=grp.output_dataset: _open_file(
                        path, name, self._transform_context))
            else:
                layer = _open_file(on_disk, grp.output_dataset, self._transform_context)
            if layer.isValid() and layer.featureCount() > 0:
                self.processed_layers.append(layer)
                successful_rules.extend(grp.flat_rules)
            else:
                for rule in grp.flat_rules:
                    if rule in self.flattened_rules:
                        self.flattened_rules.remove(rule)
        return self.processed_layers, successful_rules

    # -------------------------------------------------------------------
    # Feature order across rules (caller thread, after the datasets)
    # -------------------------------------------------------------------
    def _keep_feature_order(self, rules: List[FlattenedRule]) -> List[FlattenedRule]:
        """``rules`` with feature-order strata (fidelity.feature_order): a
        feature QGIS draws above an overlapping feature of a later rule is
        drawn by a copy of its rule's style layers in a higher stratum. Only
        the style changes (filters on the original feature id); datasets,
        tiles and the export cache stay as they are."""
        by_layer: Dict[str, List[FlattenedRule]] = {}
        for rule in rules:
            if rule.get_attr("t") == 0:
                by_layer.setdefault(rule.layer.id(), []).append(rule)
        copies: List[FlattenedRule] = []
        for layer_rules in by_layer.values():
            if self._is_cancelled():
                break
            try:
                lifted = self._feature_strata(layer_rules)
            except Exception:  # noqa: BLE001  (the layer keeps rule order)
                self.feedback.reportError(
                    f"Feature order of '{layer_rules[0].layer.name()}' not kept:\n"
                    f"{traceback.format_exc()}")
                continue
            copies.extend(self._lift_features(layer_rules, lifted))
        return rules + copies

    def _feature_strata(self, rules: List[FlattenedRule]) -> Dict[int, Dict[Tuple[int, int], int]]:
        """{pass: {(feature id, rule): stratum > 0}} of one layer's renderer
        rules (order key: -layer, pass, stratum, rule, ...); {} when the layer
        keeps rule order."""
        layer = rules[0].layer
        renderer = layer.renderer()
        if renderer is None or _enum_value(layer.geometryType()) not in (1, 2):
            return {}  # markers overlap by their size on screen
        try:
            levels = not isinstance(renderer, QgsRuleBasedRenderer) and renderer.usingSymbolLevels()
        except (AttributeError, RuntimeError):
            levels = True
        # Symbol levels draw symbol by symbol, as the style layers do; merged,
        # grouped, heatmap and inner-effect drawings are not per feature.
        if levels or any(r.merge or r.heatmap or r.point_group or r.inner_effect or len(r.order) < 5
                         for r in rules):
            return {}
        passes: Dict[int, Dict[int, List[FlattenedRule]]] = {}
        for rule in rules:
            passes.setdefault(rule.order[1], {}).setdefault(rule.order[3], []).append(rule)
        # The datasets of what each rule draws on the map: polygons before
        # lines (an outline lies on its polygon), plain before materialized.
        footprints: Dict[int, Dict[int, List[str]]] = {}
        for draw_pass, by_seq in passes.items():
            names = {}
            for seq, components in by_seq.items():
                shapes = {r.output_dataset: r for r in components if r.get_attr("c") in (1, 2)}
                names[seq] = [r.output_dataset for r in sorted(shapes.values(), key=lambda r: (
                    -r.get_attr("c"), r.recipe is not None, bool(r.pre_generator), r.output_dataset))]
            # One rule only, or a rule drawn only with markers: rule order.
            if len(names) > 1 and all(names.values()):
                footprints[draw_pass] = names
        datasets = {name: _open_file(join(self.utils_dir, f"{name}.{_TEMP_RULE_FORMAT}"), name,
                                     self._transform_context)
                    for names in footprints.values() for group in names.values() for name in group}
        count = sum(d.featureCount() for d in datasets.values() if d.isValid())
        if count > fo.MAX_FEATURES:
            self.diagnostics.add(
                "Q2VT_FEATURE_ORDER_ACROSS_RULES",
                f"Layer '{layer.name()}': {count} features are too many to check; overlapping "
                "features of different rules are drawn in rule order.",
                severity=Severity.INFO, layer_id=layer.id())
            return {}
        rows = {name: self._feature_footprints(dataset) for name, dataset in datasets.items()}
        del datasets
        # Overlaps narrower than about a tile unit at the last zoom: slivers
        # between neighbours simplified apart (or invisible).
        margin = self._simplification_tolerance() / _DATA_SIMPLIFICATION_TOLERANCE
        drawn: Dict[int, list] = {}
        for draw_pass, names in footprints.items():
            parts: Dict[Tuple[int, int], list] = {}
            ranks: Dict[int, float] = {}
            for seq, group in names.items():
                source: Dict[int, str] = {}  # one dataset per feature (zoom splits repeat it)
                for name in group:
                    for fid, rank, geometry in rows[name]:
                        if source.setdefault(fid, name) == name:
                            ranks.setdefault(fid, rank)
                            parts.setdefault((fid, seq), []).append(geometry)
            # QGIS's drawing order: feature by feature (request order), each
            # with its rules in order.
            keys = sorted(parts, key=lambda key: (ranks[key[0]], key))
            drawn[draw_pass] = [(fid, seq, parts[(fid, seq)][0] if len(parts[(fid, seq)]) == 1
                                 else QgsGeometry.collectGeometry(parts[(fid, seq)]))
                                for fid, seq in keys]

        def lift(touching_lines: bool):
            result = {}
            for draw_pass, items in drawn.items():
                lifted = fo.strata(items, margin, touching_lines)
                if lifted:
                    result[draw_pass] = lifted
            total = sum(len(lifted) for lifted in result.values())
            top = max((s for lifted in result.values() for s in lifted.values()), default=0)
            return result, total, top, total <= fo.MAX_LIFTED and top <= fo.MAX_STRATA

        result, total, top, fits = lift(True)
        lines = any(_enum_value(geometry.type()) == 1
                    for items in drawn.values() for _, _, geometry in items)
        if not fits and lines:
            # Lines that only touch (ways ending at a junction) lift far more
            # features than crossings: keep at least the crossings' order.
            crossing, _, _, crossing_fits = lift(False)
            if crossing_fits:
                self.diagnostics.add(
                    "Q2VT_FEATURE_ORDER_ACROSS_RULES",
                    f"Layer '{layer.name()}': too many lines of different rules meet ({total} "
                    f"feature(s) in {top} strata; the export keeps up to {fo.MAX_LIFTED} features "
                    f"in {fo.MAX_STRATA} strata): lines that cross keep QGIS's drawing order, "
                    "lines that only touch (at junctions) are drawn in rule order.",
                    severity=Severity.INFO, layer_id=layer.id())
                return crossing
        if not fits:
            self.diagnostics.add(
                "Q2VT_FEATURE_ORDER_ACROSS_RULES",
                f"Layer '{layer.name()}': {total} feature(s) that QGIS draws above overlapping "
                f"features of later rules stay below them ({top} strata; the export keeps up to "
                f"{fo.MAX_LIFTED} features in {fo.MAX_STRATA} strata).",
                layer_id=layer.id())
            return {}
        return result

    @staticmethod
    def _feature_footprints(dataset) -> List[tuple]:
        """(original feature id, drawing rank, geometry) of a dataset's rows."""
        if not dataset.isValid():
            return []
        fields = dataset.fields()
        id_index = fields.indexFromName(fo.ID_FIELD)
        rank_index = fields.indexFromName(ORDER_FIELD)
        if id_index < 0:
            return []
        request = QgsFeatureRequest().setSubsetOfAttributes(
            [i for i in (id_index, rank_index) if i >= 0])
        rows = []
        for feature in dataset.getFeatures(request):
            geometry = feature.geometry()
            try:
                fid = int(feature.attribute(id_index))
            except (TypeError, ValueError):
                continue
            if geometry.isEmpty():
                continue
            try:  # the renderer's order-by rank, else the source order
                rank = float(feature.attribute(rank_index)) if rank_index >= 0 else fid
            except (TypeError, ValueError):
                rank = fid
            rows.append((fid, rank, geometry))
        return rows

    @staticmethod
    def _lift_features(rules: List[FlattenedRule],
                       lifted: Dict[int, Dict[Tuple[int, int], int]]) -> List[FlattenedRule]:
        """Copies of the components of each rule with lifted features, one
        per stratum ("_kNN" style names, owned by the rule in publishing),
        drawing only those features; the rule itself draws the others."""
        copies = []
        for draw_pass, pairs in sorted(lifted.items()):
            by_seq: Dict[int, Dict[int, List[int]]] = {}
            for (fid, seq), stratum in pairs.items():
                by_seq.setdefault(seq, {}).setdefault(stratum, []).append(fid)
            for rule in rules:
                strata = by_seq.get(rule.order[3]) if rule.order[1] == draw_pass else None
                if not strata:
                    continue
                for stratum, ids in sorted(strata.items()):
                    copy = rule.derive()
                    copy.rule.setDescription(fo.copy_name(rule.rule.description(), stratum))
                    copy.order = rule.order[:2] + (stratum,) + rule.order[3:]
                    copy.feature_filter = fo.only(ids)
                    copies.append(copy)
                rule.feature_filter = fo.without(fid for ids in strata.values() for fid in ids)
        return copies

    # -------------------------------------------------------------------
    # Worker-safe processing runner
    # -------------------------------------------------------------------
    def _run_alg_safe(
        self,
        algorithm: str,
        algorithm_type: str = "native",
        **params,
    ) -> str:
        """Run a processing algorithm with NO main-thread state access.

        * Fresh QgsProcessingContext per call.
        * Expression context: copies of the global and project scopes taken
          on the main thread (see __init__) — never QgsProject.instance().
        * Per-call QgsProcessingFeedback.
        * Returns an output path (string), never a live layer reference.
        """
        self._check_cancel()
        context = self._processing_context()
        feedback = QgsProcessingFeedback()
        full_name = f"{algorithm_type}:{algorithm}"
        key = _EXPRESSION_PARAMETERS.get(algorithm) if algorithm_type == "native" else None
        if key and isinstance(params.get(key), str) and params[key].strip():
            # An expression that fails on one feature (text in a numeric
            # field...): QGIS skips that value - the property keeps its
            # default, the rule does not match, no label or generated
            # geometry - while the algorithm would abort the whole step. (The
            # line break keeps a trailing "--" comment from eating the ")".)
            # A value a numeric field cannot hold (text in a rotation field)
            # is NULL too: the writer dropped the whole feature (its label).
            cast = {0: "to_real", 1: "to_int"}.get(params.get("FIELD_TYPE")) \
                if algorithm == "fieldcalculator" else None
            params[key] = f"try({cast}({params[key]}\n))" if cast else f"try({params[key]}\n)"
        if algorithm == "refactorfields" and algorithm_type == "native":
            def safe(field):
                expression = str(field.get("expression") or "")
                if not expression.strip():
                    return field
                cast = {6: "to_real", 2: "to_int", 4: "to_int"}.get(field.get("type"))
                wrapped = f"try({cast}({expression}\n))" if cast else f"try({expression}\n)"
                return {**field, "expression": wrapped}
            params["FIELDS_MAPPING"] = [safe(field) for field in params.get("FIELDS_MAPPING") or []]
        if self._memory_active:
            return self._run_in_memory(full_name, params, context, feedback)

        if params.get("OUTPUT") in (None, "TEMPORARY_OUTPUT"):
            params["OUTPUT"] = self._temp_path("temp")

        crash_log.note(f"  {full_name} " + ", ".join(
            f"{k}={str(v)[:300]}" for k, v in params.items() if k not in ("INPUT", "OUTPUT")))
        # pylint: disable=E1111
        result = run_processing(
            full_name, params, context=context, feedback=feedback
        )
        output = result.get("OUTPUT")
        # If processing returned a layer, surface its source path. We never
        # let a live QgsVectorLayer escape into our pipeline data flow.
        if isinstance(output, QgsVectorLayer):
            return output.source()
        return output

    def _processing_context(self) -> QgsProcessingContext:
        context = QgsProcessingContext()
        context.setExpressionContext(self._worker_expression_context())
        if not self._planar:
            context.setEllipsoid(self._ellipsoid)
        context.setDistanceUnit(self._distance_unit)
        context.setAreaUnit(self._area_unit)
        context.setInvalidGeometryCheck(QgsFeatureRequest.InvalidGeometryCheck.GeometryNoCheck)
        return context

    # --- in-memory chains ---------------------------------------------------
    def _open(self, ref, name: str = "layer") -> QgsVectorLayer:
        """The layer of a step result: a memory layer (token) or a file."""
        if isinstance(ref, QgsVectorLayer):
            return ref
        if isinstance(ref, str) and ref.startswith(_MEM_PREFIX):
            return self._mem[ref]
        return _open_file(ref, name, self._transform_context)

    def _resolve(self, value):
        if isinstance(value, str) and value.startswith(_MEM_PREFIX):
            return self._mem[value]
        if isinstance(value, list):
            return [self._resolve(v) for v in value]
        return value

    def _input_features(self, params: dict) -> int:
        total = 0
        for key in ("INPUT", "POLYGONS", "LAYERS"):
            for value in (params.get(key) if isinstance(params.get(key), list) else [params.get(key)]):
                if isinstance(value, str) and value.startswith(_MEM_PREFIX):
                    total += max(0, self._mem[value].featureCount())
                elif isinstance(value, str) and value:
                    if value not in self._file_counts:  # files do not change during the export
                        layer = _open_file(value, "count", self._transform_context)
                        self._file_counts[value] = max(0, layer.featureCount()) if layer.isValid() else 0
                    total += self._file_counts[value]
        return total

    def _step_key(self, full_name: str, params: dict) -> Optional[str]:
        """Identity of a step for sharing; None when it cannot be shared."""
        if full_name not in _SHAREABLE_ALGORITHMS:
            return None
        plain = {}
        for key, value in params.items():
            if key == "OUTPUT":
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                plain[key] = value
            elif isinstance(value, list) and all(isinstance(v, (str, int, float, bool, dict)) for v in value):
                plain[key] = value
            else:
                return None  # a QgsProperty or a layer object: not shared
            if _NONDETERMINISTIC.search(json.dumps(plain[key], default=str)):
                return None
        return json.dumps([full_name, plain], sort_keys=True, default=str)

    def _run_in_memory(self, full_name: str, params: dict, context, feedback) -> str:
        """A step of a rule group's chain: memory output (a token) unless it
        writes the group's final dataset or its input is large."""
        explicit = params.get("OUTPUT")
        temporary = explicit in (None, "TEMPORARY_OUTPUT") or explicit in self._temp_files
        if temporary and self._input_features(params) > _MEMORY_MAX_FEATURES:
            temporary = False  # a large step writes a file, as before
            if explicit in (None, "TEMPORARY_OUTPUT"):
                params["OUTPUT"] = self._temp_path("temp")
            self.step_stats["file"] += 1
        key = self._step_key(full_name, params) if temporary else None
        if key is not None and key in self._shared:
            self.step_stats["shared"] += 1
            return self._shared[key]
        crash_log.note(f"  {full_name} (memory) " + ", ".join(
            f"{k}={str(v)[:300]}" for k, v in params.items() if k not in ("INPUT", "OUTPUT")))
        resolved = {k: self._resolve(v) for k, v in params.items()}
        if temporary:
            resolved["OUTPUT"] = "TEMPORARY_OUTPUT"
        algorithm = QgsApplication.processingRegistry().createAlgorithmById(full_name)
        if algorithm is None:
            raise QgsProcessingException(f"Algorithm {full_name} not found")
        # The parameters are ours and valid: no checkParameterValues (it opens
        # every input once more).
        results, ok = algorithm.run(resolved, context, feedback, {}, False)
        if not ok:
            raise QgsProcessingException(f"{full_name} failed")
        self.step_stats["run"] += 1
        output = results.get("OUTPUT")
        if not temporary:
            return output.source() if isinstance(output, QgsVectorLayer) else output
        layer = context.takeResultLayer(output) if isinstance(output, str) else output
        if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            raise QgsProcessingException(f"{full_name}: no result layer")
        self._mem_counter += 1
        token = f"{_MEM_PREFIX}{self._mem_counter}"
        self._mem[token] = layer
        count = max(0, layer.featureCount())
        if key is not None and self._shared_features + count <= _SHARED_MAX_FEATURES:
            self._shared[key] = token
            self._shared_tokens.add(token)
            self._shared_features += count
        else:
            self._group_tokens.append(token)
        return token

    def _interval_points_direct(self, lines: str, recipe: Recipe) -> str:
        """The interval markers of ``lines`` computed in Python
        (core.marker_points): the same points and angles as the expression
        chain of the file mode, without walking every line from its start
        for every marker. Shared like a Processing step; a file for a large
        input, as _run_in_memory does."""
        self._check_cancel()
        expression = mat.interval_points_expression(recipe, f"EPSG:{_EPSG_CRS}")
        key = json.dumps(["q2vt:interval_points", lines, expression])
        if key in self._shared:
            self.step_stats["shared"] += 1
            return self._shared[key]
        crash_log.note(f"  interval markers (direct) {dict(recipe.params)}")
        source = self._open(lines, "lines")
        fields, batches = marker_points.interval_points(
            source, recipe, f"EPSG:{_EPSG_CRS}", self._worker_expression_context(), expression)
        self.step_stats["direct"] += 1
        if self._input_features({"INPUT": lines}) > _MEMORY_MAX_FEATURES:
            path = self._temp_path("temp")
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = "GPKG"
            writer = QgsVectorFileWriter.create(path, fields, QgsWkbTypes.Point, source.crs(),
                                                self._transform_context, options)
            for batch in batches:
                self._check_cancel()
                writer.addFeatures(batch)
            del writer
            self.step_stats["file"] += 1
            return path
        layer = QgsMemoryProviderUtils.createMemoryLayer("points", fields, QgsWkbTypes.Point,
                                                         source.crs())
        for batch in batches:
            self._check_cancel()
            layer.dataProvider().addFeatures(batch)
        self._mem_counter += 1
        token = f"{_MEM_PREFIX}{self._mem_counter}"
        self._mem[token] = layer
        count = max(0, layer.featureCount())
        if self._shared_features + count <= _SHARED_MAX_FEATURES:
            self._shared[key] = token
            self._shared_tokens.add(token)
            self._shared_features += count
        else:
            self._group_tokens.append(token)
        return token

    def _release_group_layers(self) -> None:
        """Memory layers of the finished group (shared ones stay)."""
        for token in self._group_tokens:
            if token not in self._shared_tokens:
                self._mem.pop(token, None)
        self._group_tokens = []

    def _release_all_layers(self) -> None:
        self._mem.clear()
        self._shared.clear()
        self._shared_tokens.clear()
        self._group_tokens = []
        self._shared_features = 0

    def _worker_expression_context(self) -> QgsExpressionContext:
        """Global + project scope for a worker: copies of the main-thread
        snapshots (project variables stay available to expressions)."""
        with self._scope_lock:
            scopes = [QgsExpressionContextScope(self._global_scope),
                      QgsExpressionContextScope(self._project_scope)]
        context = QgsExpressionContext()
        for scope in scopes:
            context.appendScope(scope)
        return context

    # -------------------------------------------------------------------
    # Snapshot helpers — caller-thread only
    # -------------------------------------------------------------------
    def _resolve_map_scale_in_rules(self, flat_rules: list) -> None:
        """Replace @map_scale references with each rule's zoom scale.

        Runs on caller thread because it mutates QObject state (rule symbols
        and labeling settings).
        """
        for flat_rule in flat_rules:
            rule_type = flat_rule.get_attr("t")
            zoom_scale = ZoomLevels.zoom_to_scale(flat_rule.get_attr("o"))
            if rule_type == 1 and flat_rule.rule.settings():
                settings = flat_rule.rule.settings()
                label_exp = settings.getLabelExpression().expression()
                if label_exp and "map_scale" in label_exp:
                    settings.fieldName = with_map_scale(label_exp, zoom_scale)
                    settings.isExpression = True
                if settings.geometryGeneratorEnabled:
                    settings.geometryGenerator = with_map_scale(
                        settings.geometryGenerator, zoom_scale
                    )
            else:
                symbol = flat_rule.rule.symbol()
                if not symbol:
                    continue
                for layer in symbol.symbolLayers():
                    if layer.layerType() == "GeometryGenerator":
                        layer.setGeometryExpression(
                            with_map_scale(layer.geometryExpression(), zoom_scale)
                        )

    def _create_expression_fields(
        self, flat_rules: list
    ) -> List[Tuple[int, str, str]]:
        """Build calculated-field entries from data-driven properties."""
        fields: List[Tuple[int, str, str]] = []
        for flat_rule in flat_rules:
            rule_type = flat_rule.get_attr("t")
            suffix = flat_rule.get_attr("s") if rule_type == 0 else flat_rule.get_attr("f")
            min_scale = ZoomLevels.zoom_to_scale(flat_rule.get_attr("o"))
            rule_fields = DataDefinedPropertiesFetcher(
                flat_rule.rule, min_scale, suffix, diagnostics=self.diagnostics
            ).fetch()
            if rule_fields:
                # Normalise to tuples of primitives so the snapshot is
                # guaranteed-immutable.
                fields.extend(tuple(f) for f in rule_fields)
        return fields

    def _add_label_expression_field(
        self,
        flat_rule: FlattenedRule,
        fields: List[Tuple[int, str, str]],
    ) -> List[Tuple[int, str, str]]:
        if not flat_rule.rule.settings():
            return fields
        label_exp = flat_rule.rule.settings().getLabelExpression().expression()
        if not label_exp:
            return fields
        field_name = f"{_FIELD_PREFIX}_label"
        settings = flat_rule.rule.settings()
        filter_exp = (
            f'"{label_exp}"'
            if not settings.isExpression
            else f"({label_exp})"
        )
        if settings.useSubstitutions:
            filter_exp = self._substituted(filter_exp, settings.substitutions)
        wrap = settings.wrapChar
        if wrap:  # QGIS breaks the line at every wrap character
            quoted = wrap.replace("'", "''")
            filter_exp = f"replace({filter_exp}, '{quoted}', '\n')"
        fields.append((10, filter_exp, field_name))
        if settings.format().allowHtmlFormatting():
            # MapLibre has no markup: text sections with their own scale.
            html_labels.register_expression_functions()
            for index in range(html_labels.MAX_SECTIONS):
                fields.append((10, f"q2vt_html_text({filter_exp}, {index})",
                               html_labels.TEXT_FIELD.format(index)))
                fields.append((6, f"q2vt_html_scale({filter_exp}, {index})",
                               html_labels.SCALE_FIELD.format(index)))
        settings.isExpression = False
        settings.fieldName = field_name
        return fields

    @staticmethod
    def _with_label_text(filter_expression: str, text: str) -> str:
        """QGIS draws nothing for a label whose text is NULL or empty, not
        even its background shape; MapLibre would draw the shape (or an
        icon) alone. Such features are left out of the label dataset."""
        condition = f"coalesce(to_string({text}), '') <> ''"
        return f"({filter_expression}) AND {condition}" if filter_expression else condition

    @staticmethod
    def _substituted(expression: str, substitutions) -> str:
        """The label text with QGIS's text replacements applied, in order
        (``QgsStringReplacement::process``): plain replacements honour the
        case option; whole-word ones are regular expressions with QGIS's
        (ASCII) word boundaries."""
        def literal(text: str) -> str:
            return "'" + text.replace("\\", "\\\\").replace("'", "''") + "'"

        def regex_escape(text: str) -> str:  # QRegularExpression::escape
            return "".join(c if c.isalnum() or c == "_" else "\\" + c for c in text)

        for replacement in substitutions.replacements():
            match, target = replacement.match(), replacement.replacement()
            if not match:
                continue
            if not replacement.wholeWordOnly() and replacement.caseSensitive():
                expression = f"replace({expression}, {literal(match)}, {literal(target)})"
                continue
            if replacement.wholeWordOnly():
                # QGIS builds QRegularExpression("\\b%1\\b") from the match
                # unescaped (it is a pattern), and its \\b knows only ASCII word
                # characters ("Zöld" has a boundary after the Z). regexp_replace's
                # \\b is Unicode-aware, so QGIS's boundary is spelt out.
                word = "[A-Za-z0-9_]"
                boundary = (f"(?:(?<={word})(?!{word})|(?<!{word})(?={word}))")
                pattern = f"{boundary}(?:{match}){boundary}"
            else:
                pattern = regex_escape(match)
            if not replacement.caseSensitive():
                pattern = "(?i)" + pattern
            # A regex replacement reads \1 as a group: QString::replace does too.
            expression = f"regexp_replace({expression}, {literal(pattern)}, {literal(target)})"
        return expression

    def _get_geometry_transformation(
        self, flat_rule: FlattenedRule
    ) -> Optional[Tuple[int, str]]:
        rule_type = flat_rule.get_attr("t")
        if rule_type == 0 and flat_rule.rule.symbol():
            transformation = self._get_renderer_transformation(flat_rule)
        elif rule_type == 1:
            transformation = self._get_labeling_transformation(flat_rule)
        else:
            return None
        if not transformation:
            return None
        transformation[1] = self._clip_to_extent(transformation[1])
        return tuple(transformation)

    def _clip_to_extent(self, expression: str) -> str:
        """``expression`` cut to the export extent. Geometries inside it are
        kept as they are: intersection() nodes a self-crossing line into
        pieces (a label then sits on a fragment) and can move a ring's start
        vertex."""
        extent_wkt = self.extent.asWktPolygon()
        return (
            f"with_variable('q2vt_t', {expression}, "
            f"with_variable('q2vt_ext', geom_from_wkt('{extent_wkt}'), "
            f"with_variable('clip', if(within(@q2vt_t, @q2vt_ext), @q2vt_t, "
            f"intersection(@q2vt_t, @q2vt_ext)), "
            f"if(not is_empty_or_null(@clip), @clip, NULL))))"
        )

    @staticmethod
    def _layer_point_expression(x: str, y: str, layer_crs: str) -> str:
        point = f"make_point(to_real({x}), to_real({y}))"
        export_crs = f"EPSG:{_EPSG_CRS}"
        if not layer_crs or layer_crs == export_crs:
            return point
        return f"transform({point}, '{layer_crs}', '{export_crs}')"

    # Longest part of a (multi)line: where QGIS puts a single line label.
    _LONGEST_PART = (
        "if(coalesce(num_geometries(@geometry), 1) > 1, geometry_n(@geometry, "
        "array_find(array_foreach(generate_series(1, num_geometries(@geometry)), "
        "length(geometry_n(@geometry, @element))), array_max(array_foreach("
        "generate_series(1, num_geometries(@geometry)), length(geometry_n(@geometry, "
        "@element))))) + 1), @geometry)")

    def _single_line_label_as_point(self, flat_rule: FlattenedRule) -> None:
        """A line label drawn once per line (no repeat distance), as QGIS
        draws it: at the middle of the line, along it.

        MapLibre places line labels per tile piece of the line ("line-center"
        centres the label on every piece), so the label is exported as the
        line's midpoint instead: an over-point label rotated to the line
        (kept upright), above/below/on the point as the placement flags say.
        """
        settings = flat_rule.rule.settings()
        if settings is None or flat_rule.get_attr("g") not in (1, 2) or \
                settings.geometryGeneratorEnabled or self._pinned_position(settings):
            return
        if not self._labels_along_line(flat_rule):
            return
        if float(settings.repeatDistance or 0) > 0:
            return
        try:
            flags = int(settings.lineSettings().placementFlags())
        except (AttributeError, TypeError):
            flags = 1
        from .maplibre_converter import TextPropertyExtractor  # pylint: disable=import-outside-toplevel
        side = TextPropertyExtractor.side_of(
            flags, TextPropertyExtractor.placement_name(settings) in ("Curved", "PerimeterCurved"))
        quadrant = {"on": Qgis.LabelQuadrantPosition.Over,
                    "above": Qgis.LabelQuadrantPosition.Above,
                    "below": Qgis.LabelQuadrantPosition.Below}[side]
        rotation = (
            f"with_variable('q2vt_l', {self._label_line(flat_rule)}, with_variable('q2vt_a', "
            f"line_interpolate_angle(@q2vt_l, length(@q2vt_l) / 2) - 90, "
            f"if(@q2vt_a > 90, @q2vt_a - 180, if(@q2vt_a <= -90, @q2vt_a + 180, @q2vt_a))))")
        settings.placement = Qgis.LabelPlacement.OverPoint
        settings.quadOffset = quadrant
        settings.xOffset = 0.0
        settings.yOffset = {"on": 0.0, "above": -1.0, "below": 1.0}[side] * float(settings.dist or 0)
        settings.offsetUnits = settings.distUnits
        settings.dataDefinedProperties().setProperty(
            QgsPalLayerSettings.Property.LabelRotation, QgsProperty.fromExpression(rotation))
        flat_rule.line_label_midpoint = True

    # Exterior ring of the largest part of a (multi)polygon: the outline a
    # QGIS perimeter label follows (pal labels the largest part).
    _LARGEST_RING = (
        "exterior_ring(if(is_multipart(@geometry), geometry_n(@geometry, "
        "array_find(array_foreach(generate_series(1, num_geometries(@geometry)), "
        "area(geometry_n(@geometry, @element))), array_max(array_foreach("
        "generate_series(1, num_geometries(@geometry)), area(geometry_n(@geometry, "
        "@element))))) + 1), @geometry))")
    # Exterior rings of every part ("Label every part of multi-part features").
    _EXTERIOR_RINGS = (
        "if(is_multipart(@geometry), collect_geometries(array_foreach(generate_series(1, "
        "num_geometries(@geometry)), exterior_ring(geometry_n(@geometry, @element)))), "
        "exterior_ring(@geometry))")

    @staticmethod
    def _labels_along_line(flat_rule: FlattenedRule) -> bool:
        """A label that follows its line: line and curved line labels, and
        polygon labels "Using perimeter" (Line) or "Using perimeter (curved)"."""
        settings = flat_rule.rule.settings()
        if settings is None:
            return False
        placement = int(getattr(settings.placement, "value", settings.placement))
        if flat_rule.get_attr("g") == 2:
            return placement in (int(Qgis.LabelPlacement.Line),
                                 int(Qgis.LabelPlacement.PerimeterCurved))
        return placement in (int(Qgis.LabelPlacement.Line), int(Qgis.LabelPlacement.Curved))

    def _label_line(self, flat_rule: FlattenedRule) -> str:
        """The line a label drawn once per feature is placed on: the longest
        part of a line, the outline of a polygon's largest part."""
        return self._LARGEST_RING if flat_rule.get_attr("g") == 2 else self._LONGEST_PART

    def _get_labeling_transformation(self, flat_rule: FlattenedRule):
        if flat_rule.recipe is not None and flat_rule.recipe.kind == "label_windows":
            return [1, "@geometry"]  # windows built by _label_windows
        settings = flat_rule.rule.settings()
        target_geom = flat_rule.get_attr("g")
        transform_expr = "@geometry"
        if getattr(flat_rule, "line_label_midpoint", False):
            flat_rule.set_attr("c", 0)
            return [0, f"with_variable('q2vt_l', {self._label_line(flat_rule)}, "
                       f"line_interpolate_point(@q2vt_l, length(@q2vt_l) / 2))"]
        pinned = self._pinned_position(settings)
        if pinned is not None:
            # Data-defined label position (layer CRS), see
            # RulesFlattener._split_pinned_labels.
            flat_rule.set_attr("c", 0)
            return [0, self._layer_point_expression(*pinned, flat_rule.layer.crs().authid())]
        if settings and settings.geometryGeneratorEnabled:
            target_geom = settings.geometryGeneratorType
           
            transform_expr = self._generator_in_layer_crs(
                settings.geometryGenerator, flat_rule)
            settings.geometryGeneratorEnabled = False
            flat_rule.set_attr("c", target_geom)
        elif target_geom == 2 and self._labels_along_line(flat_rule):
            # A perimeter label repeated along the outline: the rings, as
            # lines (a centroid has no line for MapLibre's line placement).
            flat_rule.set_attr("c", 1)
            target_geom = 1
            transform_expr = self._EXTERIOR_RINGS if settings.labelPerPart else self._LARGEST_RING
        elif target_geom == 2:
            # A label on the visible part: the static point (for clients
            # without the viewer's visible-polygon labels) lies in the part
            # inside the export extent. A whole-polygon centroid outside the
            # extent emptied the dataset, and the style layer was dropped
            # with the viewer's labels (large zoning polygons).
            visible = self._labels_visible_polygon(flat_rule, 0)
            flat_rule.set_attr("c", 0)
            target_geom = 0
            transform_expr = self._get_polygon_centroids_expression(
                clip=visible, roomiest=self._roomiest_placement(settings))
        return [target_geom, transform_expr]

    @staticmethod
    def _pinned_position(settings) -> Optional[Tuple[str, str]]:
        if settings is None:
            return None
        props = settings.dataDefinedProperties()
        P = QgsPalLayerSettings.Property
        x_prop, y_prop = props.property(P.PositionX), props.property(P.PositionY)
        if x_prop and y_prop and x_prop.isActive() and y_prop.isActive():
            return x_prop.asExpression(), y_prop.asExpression()
        return None

    def _get_renderer_transformation(self, flat_rule: FlattenedRule):
        symbol = flat_rule.rule.symbol()
        if not symbol:
            return None
        symbol_layer = symbol.symbolLayers()[0]
        target_geom = flat_rule.get_attr("g")
        transform_expr = "@geometry"
        recipe = flat_rule.recipe
        if recipe is not None and recipe.kind == "marker_points":
            return [0, "@geometry"]  # points already materialized
        if recipe is not None and recipe.kind == "hatch_lines":
            return [1, mat.hatch_expression(recipe, f"EPSG:{_EPSG_CRS}")]
        if recipe is not None and recipe.kind == "color_bands":
            return [2, "@geometry"]  # bands already materialized (_color_bands)
        if recipe is not None and recipe.kind == "interpolated_segments":
            return [1, "@geometry"]  # pieces already materialized
        if recipe is not None and recipe.kind == "grid_points":
            # Stroke-only markers are exported as their (clipped) line work.
            kind = 2 if recipe.param("fill") or recipe.param("stroke") else \
                1 if recipe.param("segments") or recipe.param("paths") else 0
            if mat.grid_splittable(recipe) and flat_rule.get_attr("g") == 2:
                # Computed per piece of the polygon (_pattern_pieces).
                recipe = mat.Recipe(recipe.kind, recipe.placements,
                                    recipe.params + (("anchor_fields", True),))
            return [kind, mat.grid_expression(recipe, f"EPSG:{_EPSG_CRS}")]
        if recipe is not None and recipe.kind == "glyph":
            return [2, mat.glyph_expression(recipe, f"EPSG:{_EPSG_CRS}")]
        if recipe is not None and recipe.kind == "dash_segments":
            crs = f"EPSG:{_EPSG_CRS}"
            if flat_rule.get_attr("g") == 2:
                lines = mat.polygon_offset_expression(recipe, crs)
            elif recipe.param("offset"):
                lines = mat.offset_line_expression(recipe, crs)
            else:
                lines = "@geometry"
            return [1, mat.dash_expression(recipe, lines, crs)]
        if recipe is not None and recipe.kind == "random_points":
            return [0, "@geometry"]  # points already materialized (_random_points)
        if recipe is not None and recipe.kind == "polygon_offset":
            return [1, mat.polygon_offset_expression(recipe, f"EPSG:{_EPSG_CRS}")]
        if recipe is not None and recipe.kind == "line_offset":
            return [1, mat.offset_line_expression(recipe, f"EPSG:{_EPSG_CRS}")]
        if recipe is not None and recipe.kind == "arrow_polygons":
            # Polygons built by _arrow_polygons; the arrow's outline
            # (SymbolMaterializer._arrow, a line rule) is drawn on their rings.
            if flat_rule.get_attr("c") == 1:
                return [1, "boundary(@geometry)"]
            return [2, "@geometry"]
        if recipe is not None and recipe.kind == "direction_runs":
            return [1, "@geometry"]  # runs built by _direction_runs
        if recipe is not None and recipe.kind == "simplified":
            lines = "boundary(@geometry)" if flat_rule.get_attr("g") == 2 else "@geometry"
            return [1, f"simplify({lines}, {float(recipe.param('tolerance'))!r})"]
        if recipe is not None and recipe.kind == "callout":
            label = self._layer_point_expression(
                f'"{CALLOUT_X_FIELD}"', f'"{CALLOUT_Y_FIELD}"', recipe.param("crs"))
            return [1, callout_leader_expression(label, flat_rule.get_attr("g"),
                                                 recipe.param("anchor", 0))]
        if symbol_layer.layerType() == "CentroidFill":
            return [0, mat.centroid_fill_expression(bool(symbol_layer.pointOnSurface()))]
        if symbol_layer.layerType() == "GeometryGenerator":
            target_geom = _enum_value(symbol_layer.subSymbol().type())
            transform_expr = self._generator_in_layer_crs(
                symbol_layer.geometryExpression(), flat_rule)
            # As drawn by the sub-symbol (e.g. a "$geometry" generator with a
            # line sub-symbol on a polygon layer draws the rings).
            transform_expr = mat.coerce_to_symbol_type(transform_expr, target_geom)
        else:
            target_geom = flat_rule.get_attr("c")
            source_geom = flat_rule.get_attr("g")
            if source_geom != target_geom:
                if target_geom == 0:
                    transform_expr = self._get_polygon_centroids_expression()
                elif target_geom == 1:
                    transform_expr = "boundary(@geometry)"
        return [target_geom, transform_expr]

    @staticmethod
    def _generator_in_layer_crs(generator_exp: str, flat_rule: FlattenedRule) -> str:
        """Evaluate a geometry generator in the source layer CRS.

        Base layers are already in EPSG:3857 when rules are exported, but
        generator distances (buffers, offsets, ...) are written for the layer
        CRS. ``@geometry`` and ``$geometry`` are both rebound to the geometry
        transformed back to the layer CRS, and the result is transformed to
        the export CRS again.
        """
        layer_crs = flat_rule.layer.crs().authid()
        if not layer_crs or layer_crs == f"EPSG:{_EPSG_CRS}":
            return generator_exp
        bound = bind_geometry(
            generator_exp, f"transform(@geometry, 'EPSG:{_EPSG_CRS}', '{layer_crs}')"
        )
        return f"transform({bound}, '{layer_crs}', 'EPSG:{_EPSG_CRS}')"

    # Suffix of the dataset holding a polygon label's polygons.
    VISIBLE_POLYGONS_SUFFIX = "_vp"
    VISIBLE_LINES_SUFFIX = "_vl"

    def _labels_visible_polygon(self, flat_rule: FlattenedRule, geom_target: int) -> bool:
        """A polygon label placed on the *visible part* of its polygon, as
        QGIS's "Centroid: visible polygon" (QgsPalLayerSettings.centroidWhole
        False, the QGIS default): the viewer recomputes the position on every
        move. Only labels exported as polygon centroids (not pinned, generated
        or line-placed ones)."""
        if flat_rule.get_attr("t") != 1 or flat_rule.get_attr("g") != 2 or geom_target != 0:
            return False
        settings = flat_rule.rule.settings()
        if settings is None or self._pinned_position(settings) is not None \
                or getattr(flat_rule, "line_label_midpoint", False):
            return False
        if self.cent_source == 0:
            return False
        if self.cent_source == 1:
            return True
        # Free (angled): the angle depends on whether the label fits the
        # polygon at the current scale, so the viewer places it. QGIS places
        # Free (and Horizontal) labels on the polygon clipped to the map
        # extent; "Centroid: whole polygon" only moves centroid placements.
        if _enum_value(settings.placement) == _enum_value(Qgis.LabelPlacement.Free):
            return True
        return not settings.centroidWhole

    def _visible_polygon_group(self, label: "_RuleGroupSnapshot",
                               flat_rule: FlattenedRule) -> "_RuleGroupSnapshot":
        """The label's polygons with the label's fields: the viewer clips them
        to the screen and places one label per feature (see viewer.html)."""
        name = label.output_dataset + self.VISIBLE_POLYGONS_SUFFIX
        for rule in label.flat_rules:
            rule.visible_polygons = name
            rule.label_per_part = bool(flat_rule.rule.settings().labelPerPart)
        return dataclasses.replace(
            label, output_dataset=name, geometry_target=2,
            geometry_expression=self._clip_to_extent("@geometry"),
            description=label.description, flat_rules=[], visible_polygons=True)

    def _visible_line_group(self, label: "_RuleGroupSnapshot",
                            flat_rule: FlattenedRule) -> "_RuleGroupSnapshot":
        """The lines of a line label drawn once per line (exported at the
        line's middle, _single_line_label_as_point), with the label's fields:
        the viewer puts the label at the middle of the line's *visible* part,
        along it, as QGIS places line labels inside the map extent."""
        name = label.output_dataset + self.VISIBLE_LINES_SUFFIX
        for rule in label.flat_rules:
            rule.visible_polygons = name
            rule.label_per_part = bool(flat_rule.rule.settings().labelPerPart)
            rule.visible_kind = "line"
        lines = self._EXTERIOR_RINGS if flat_rule.get_attr("g") == 2 else "@geometry"
        return dataclasses.replace(
            label, output_dataset=name, geometry_target=1,
            geometry_expression=self._clip_to_extent(lines),
            description=label.description, flat_rules=[], visible_polygons=True)

    @staticmethod
    def _roomiest_placement(settings) -> bool:
        """Horizontal / Free polygon labels: QGIS ranks the candidates by their
        distance from the polygon's pole of inaccessibility (the point with the
        most room), not by the centroid."""
        if settings is None:
            return False
        placement = _enum_value(settings.placement)
        return placement in (_enum_value(Qgis.LabelPlacement.Horizontal),
                             _enum_value(Qgis.LabelPlacement.Free))

    def _get_polygon_centroids_expression(self, clip: bool = False, roomiest: bool = False) -> str:
        if clip or self.cent_source == 1:
            polygons = (
                f"intersection(@geometry, "
                f"geom_from_wkt('{self.extent.asWktPolygon()}'))"
            )
        else:
            polygons = "@geometry"
        if roomiest:  # 0.5 m in EPSG:3857 units (the base layers' CRS)
            return (f"with_variable('source', {polygons}, "
                    f"coalesce(pole_of_inaccessibility(@source, 0.5), point_on_surface(@source)))")
        return (
            f"with_variable('source', {polygons}, "
            f"if(intersects(centroid(@source), @source), "
            f"centroid(@source), point_on_surface(@source)))"
        )

    # -------------------------------------------------------------------
    # Pool sizing, future iteration, temp tracking
    # -------------------------------------------------------------------
    def _executor(self, max_workers: int, prefix: str):
        if max_workers <= 1:
            return _InlineExecutor()
        return ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix=prefix)

    def _compute_pool_size(self, num_jobs: int) -> int:
        if num_jobs <= 0 or not self.parallel:
            return 1
        cpu_n = os.cpu_count() or 1
        from_user = max(1, int(cpu_n * self.cpu_percent / 100))
        return min(from_user, _MAX_WORKERS_HARD_CAP, num_jobs)

    # -------------------------------------------------------------------
    # Progress and messages
    # -------------------------------------------------------------------
    def _post(self, kind: str, message: str) -> None:
        """pushInfo / pushWarning from any thread: workers queue the message
        and the main thread writes it (see _flush_messages)."""
        crash_log.note(message)
        if main_thread.on_main_thread() or not main_thread.app_running():
            getattr(self.feedback, kind)(message)
        else:
            self._messages.put((kind, message))

    def _flush_messages(self) -> None:
        while True:
            try:
                kind, message = self._messages.get_nowait()
            except queue.Empty:
                return
            getattr(self.feedback, kind)(message)

    def _progress(self, phase_start: float, phase_end: float, done: int, total: int) -> None:
        """Progress bar: ``done/total`` of a phase spanning phase_start..end
        (fractions of this export's share)."""
        low, high = self._progress_range
        fraction = phase_start + (phase_end - phase_start) * (done / max(1, total))
        self.feedback.setProgress(low + (high - low) * fraction)

    def _iter_completed(
        self, futures: Dict[Future, Any]
    ) -> Iterator[Future]:
        """Yield futures as they complete, polling cancellation each second.

        Unlike concurrent.futures.as_completed, this checks our cancel flag
        between waits so an external cancellation request is responsive even
        when current futures are still running.
        """
        if all(isinstance(fut, _InlineFuture) for fut in futures):
            for fut in futures:  # serial: run each job now, in order
                if self._is_cancelled():
                    return
                fut.execute()
                self._flush_messages()
                main_thread.keep_responsive()
                yield fut
            return
        pending = set(futures.keys())
        while pending:
            done, pending = wait(
                pending, timeout=0.2, return_when=FIRST_COMPLETED
            )
            self._flush_messages()
            main_thread.keep_responsive()
            for fut in done:
                yield fut
            if self._is_cancelled():
                # Best-effort: cancel anything not yet started. Already-running
                # futures will exit at their next _check_cancel().
                for fut in pending:
                    fut.cancel()
                return

    def _temp_path(self, prefix: str = "temp") -> str:
        """Allocate a tracked temp path inside utils_dir."""
        p = join(self.utils_dir, f"{prefix}_{uuid4().hex}.{_TEMP_RULE_FORMAT}")
        with self._temp_files_lock:
            self._temp_files.add(p)
        return p

    def _cleanup_temp_files(self) -> None:
        with self._temp_files_lock:
            paths = list(self._temp_files)
            self._temp_files.clear()
        for p in paths:
            try:
                if exists(p):
                    os.remove(p)
            except OSError:
                pass