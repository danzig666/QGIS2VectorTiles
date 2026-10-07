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
    QgsCoordinateReferenceSystem,
    QgsExpressionContext,
    QgsExpression,
    QgsExpressionContextScope,
    QgsExpressionContextUtils,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsFeatureRequest,
    QgsField,
    QgsPalLayerSettings,
    QgsProperty,
    QgsRectangle,
    QgsCoordinateTransform,
    QgsVectorLayer,
    QgsProject,
)

from ..utils import crash_log, main_thread
from ..utils.config import _DATA_SIMPLIFICATION_TOLERANCE, _EPSG_CRS, _FIELD_PREFIX
from ..utils.flattened_rule import FlattenedRule
from ..utils.zoom_levels import ZoomLevels
from .ddp_fetcher import DataDefinedPropertiesFetcher
from . import export_cache
from .fidelity.diagnostics import DiagnosticCollector
from .fidelity.qgis_expr import bind_geometry, in_layer_crs, substitute_geometry, with_map_scale
from .fidelity import html_labels
from .fidelity import materialize as mat
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

# Temp files prefered to be parquet but in linux which not support parquet they are became gpkg.
_TEMP_LAYER_FORMAT = 'sqlite'
_TEMP_RULE_FORMAT = 'gpkg'
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

    @property
    def needs_serial_read(self) -> bool:
        return self.provider in _SERIAL_READ_PROVIDERS


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


_RING_FIELD = f"{_FIELD_PREFIX}_ring_cw"
# 1 when the (first) exterior ring is clockwise; is_polygon_clockwise() is
# not available before QGIS 3.36.
_RING_CLOCKWISE_EXPRESSION = (
    "with_variable('q2vt_p', if(is_multipart(@geometry), geometry_n(@geometry, 1), @geometry), "
    "if(geom_to_wkt(exterior_ring(force_polygon_cw(@q2vt_p))) = "
    "geom_to_wkt(exterior_ring(@q2vt_p)), 1, 0))"
)


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
    ):
        self.flattened_rules = flattened_rules
        # Datasets of unchanged layers reused from earlier exports (None: off).
        self.cache = cache
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

        # Single lock used to serialise reads from "needs_serial_read"
        # providers, regardless of how many workers exist. Conservative but
        # absolutely safe — Postgres/WFS/etc. are read one at a time, full stop.
        self._serial_read_lock = threading.Lock()

        # Cancellation flag. Workers check this between processing.run calls.
        self._cancelled = threading.Event()

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------
    def export(self) -> Tuple[List[QgsVectorLayer], List[FlattenedRule]]:
        """Run the full export pipeline. Synchronous. Always returns."""
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

            materialized = self._materialize_sources_serial(
                {lid: src for lid, src in sources.items() if lid in needed})

            # Phase 2 — parallel base-layer pipeline (file → file).
            if self._is_cancelled():
                return [], []

            base_layers = self._build_base_layers_parallel(materialized)

            # Phase 3 — parallel rule export (file → file).
            if self._is_cancelled():
                return [], []
            
            rule_outputs = self._export_rules_parallel(pending, base_layers)
            self._store_cached(pending, rule_outputs)
            rule_outputs.update(cached_outputs)
            # Phase 4 — collect results on caller thread.
            return self._collect_results(rule_groups, rule_outputs)
        finally:
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
            sources[lid] = _SourceSnapshot(
                layer_id=lid,
                name=r.layer.name(),
                source_uri=r.layer.source(),
                provider=r.layer.providerType(),
                order_by=self._order_by(r.layer),
                crs_wkt=r.layer.crs().toWkt(),
                feature_key=self.feature_keys.get(lid, ""),
                data_fingerprint=(export_cache.source_fingerprint(
                    r.layer.providerType(), r.layer.source()) if self.cache else None),
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
            if primary.get_attr("t") == 1:
                expr_fields = self._add_label_expression_field(
                    primary, expr_fields
                )

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
            out_path = join(self.utils_dir, f"materialized_{src.layer_id}.{_TEMP_LAYER_FORMAT}")

            if exists(out_path):
                # Idempotent restart support.
                materialized[src.layer_id] = out_path
                continue

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
                    continue

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
                materialized[src.layer_id] = out_path
            except _Cancelled:
                raise
            except Exception:  # noqa: BLE001  (we want to swallow per-source)
                self.feedback.reportError(
                    f"Failed to export source '{src.name}':\n"
                    f"{traceback.format_exc()}"
                )
        return materialized

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

        with self._executor(max_workers, "rules-base") as pool:
            futures: Dict[Future, str] = {
                pool.submit(
                    self._build_one_base_layer, src_path, target_paths[lid]
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
    
    def _build_one_base_layer(self, src_path: str, dst_path: str) -> None:
        """Worker: run the cleanup chain on a local Parquet file."""
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
        self._check_cancel()
        singleparted = self._run_alg_safe(
            "multiparttosingleparts", "native", INPUT=orig_id
        )
        self._check_cancel()
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

    def _export_one_rule_group_recorded(self, grp: _RuleGroupSnapshot, source_path: str):
        """_export_one_rule_group, keeping the diagnostics it adds (stored
        with the dataset in the export cache and replayed on reuse)."""
        before = len(self.diagnostics.items)
        try:
            return self._export_one_rule_group(grp, source_path)
        finally:
            added = self.diagnostics.items[before:]
            if self.parallel:  # other groups add concurrently: only this group's
                added = [d for d in added if d.component == grp.output_dataset]
            self._group_diagnostics[grp.output_dataset] = added

    # -------------------------------------------------------------------
    # Export cache
    # -------------------------------------------------------------------
    def _cache_context(self) -> dict:
        """Settings every dataset depends on (besides its rule and source)."""
        extent = self.extent
        project_scope = QgsExpressionContextUtils.projectScope(QgsProject.instance())
        global_scope = QgsExpressionContextUtils.globalScope()
        variables = {}
        for scope in (global_scope, project_scope):
            for name in scope.variableNames():
                if name.startswith(("project_last_saved", "project_path", "project_home",
                                    "project_basename", "project_filename", "user_", "_")):
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
        source = {f.name: getattr(src, f.name) for f in dataclasses.fields(src)
                  if f.name not in ("layer_id", "name", "feature_key")}
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
        context = self._cache_context()
        last = self.cache.last_components()
        last_context = last.get("_context") or {}
        components = {"_context": context}
        code, context_hash = export_cache.code_fingerprint()[:16], export_cache.part_hash(context)
        redone: Dict[str, set] = {}
        for grp in rule_groups:
            src = sources.get(grp.layer_id)
            if src is None or not src.data_fingerprint:
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
            check = QgsVectorLayer(filt, "check", "ogr")
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
            check = QgsVectorLayer(current_input, "check", "ogr")
            if not check.isValid() or check.featureCount() <= 0:
                return None

        # Patterns on large or detailed polygons: pieces, or native points.
        if grp.recipe is not None and grp.recipe.kind == "grid_points" and \
                grp.source_geometry == 2 and mat.grid_splittable(grp.recipe):
            current_input = self._pattern_pieces(current_input, grp.recipe)
        elif grp.recipe is not None and grp.recipe.kind == "random_points":
            current_input = self._random_points(current_input, grp.recipe)
            check = QgsVectorLayer(current_input, "check", "ogr")
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
        elif grp.recipe is not None and grp.recipe.kind == "arrow_body" and grp.recipe.param("taper"):
            current_input = self._tapered_arrows(current_input, grp.recipe)
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
        keep_biggest_part = False
        if grp.rule_type == 1 and not grp.visible_polygons:
            settings = grp.flat_rules[0].rule.settings()
            if settings and not settings.labelPerPart:
                keep_biggest_part = True
        if grp.rule_type == 0:
            symbol_layer = grp.flat_rules[0].rule.symbol().symbolLayers()[0]
            if symbol_layer.layerType() == 'CentroidFill' and not symbol_layer.pointOnAllParts():
                keep_biggest_part = True
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
        transformed = self._run_alg_safe(
            "geometrybyexpression", "native",
            INPUT=transbase,
            OUTPUT_GEOMETRY=geom_target,
            EXPRESSION=grp.geometry_expression,
        )
        check = QgsVectorLayer(transformed, "check", "ogr")
        if not check.isValid() or check.featureCount() <= 0:
            self._report_empty_output(grp, transbase)
            return None
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
        check = QgsVectorLayer(cleaned, "check", "ogr")
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
        check = QgsVectorLayer(out, "check", "ogr")
        if not check.isValid() or check.featureCount() <= 0:
            return None
        return out

    def _report_empty_output(self, grp: _RuleGroupSnapshot, source: str) -> None:
        matched = QgsVectorLayer(source, "matched", "ogr")
        count = matched.featureCount() if matched.isValid() else 0
        if count > 0:
            self.diagnostics.add(
                "Q2VT_RULE_OUTPUT_EMPTY",
                f"{grp.description}: {count} matching feature(s) but the geometry "
                f"expression produced no geometry.",
                layer_id=grp.layer_id, component=grp.output_dataset,
                detail=grp.geometry_expression)

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
                                     FIELD_TYPE=0, FIELD_LENGTH=24, FIELD_PRECISION=9,
                                     FORMULA=formula)
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
        layer = QgsVectorLayer(source, "bands_src", "ogr")
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
            layer = QgsVectorLayer(source, "point_groups", "ogr")
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
        layer = QgsVectorLayer(source, "merge_src", "ogr")
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
        layer = QgsVectorLayer(source, "interpolated_src", "ogr")
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

    # Pieces of a tapered arrow body (width steps of 1/24 of the change).
    ARROW_TAPER_PIECES = 24

    def _tapered_arrows(self, source: str, recipe: Recipe) -> Optional[str]:
        """Worker: arrow bodies (``mat.arrow_body_for``, cut at the heads)
        whose width runs from the start to the end width, as QGIS's arrow
        polygon does: short pieces, each with the width of its middle
        (WIDTH_FIELD)."""
        from qgis.core import (QgsFeature, QgsField, QgsFields, QgsGeometry,  # pylint: disable=import-outside-toplevel
                               QgsProject, QgsVectorFileWriter, QgsWkbTypes)
        bodies = self._run_alg_safe("geometrybyexpression", "native", INPUT=source, OUTPUT_GEOMETRY=1,
                                    EXPRESSION=mat.arrow_body_for(recipe, f"EPSG:{_EPSG_CRS}"))
        bodies = self._run_alg_safe("removenullgeometries", "native", INPUT=bodies, REMOVE_EMPTY=True)
        bodies = self._run_alg_safe("multiparttosingleparts", "native", INPUT=bodies)
        layer = QgsVectorLayer(bodies, "arrow_bodies", "ogr") if isinstance(bodies, str) else bodies
        if layer is None or not layer.isValid():
            return None
        start, end = recipe.param("taper")
        nested = bool(recipe.param("taper_nested", False))
        fields = QgsFields()
        for field in layer.fields():
            if field.name().lower() not in ("fid", "ogc_fid") and field.name() != mat.WIDTH_FIELD:
                fields.append(field)
        fields.append(QgsField(mat.WIDTH_FIELD, QVariant.Double))
        out = self._temp_path("arrows")[:-len(_TEMP_RULE_FORMAT)] + "fgb"
        with self._temp_files_lock:
            self._temp_files.add(out)
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = "FlatGeobuf"
        options.layerOptions = ["SPATIAL_INDEX=NO"]  # keep the drawing order
        writer = QgsVectorFileWriter.create(out, fields, QgsWkbTypes.LineString, layer.crs(),
                                            QgsProject.instance().transformContext(), options)
        written, pieces = 0, self.ARROW_TAPER_PIECES
        for feature in layer.getFeatures():
            self._check_cancel()
            geometry = feature.geometry()
            if geometry.isEmpty():
                continue
            for part in geometry.constParts():
                length = part.length()
                if length <= 0:
                    continue
                for piece in range(pieces):
                    t0, t1 = piece / pieces, (piece + 1) / pieces
                    if nested:
                        # Opaque: each piece reaches to the wide end of the body,
                        # so a width step lies on a continuous line (pieces
                        # meeting end to end show hairline cracks).
                        t0, t1 = (t0, 1.0) if end >= start else (0.0, t1)
                    line = QgsGeometry(part.curveSubstring(t0 * length, t1 * length))
                    if line.isEmpty():
                        continue
                    out_feature = QgsFeature(fields)
                    for field in fields:
                        if field.name() != mat.WIDTH_FIELD:
                            out_feature[field.name()] = feature[field.name()]
                    middle = (piece + 0.5) / pieces
                    out_feature[mat.WIDTH_FIELD] = float(start + (end - start) * middle)
                    out_feature.setGeometry(line)
                    writer.addFeature(out_feature)
                    written += 1
        del writer
        return out if written else None

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
        fields = QgsVectorLayer(points, "fields", "ogr").fields()
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
        if recipe.param("arrow_curved") is not None:
            # Arrow heads sit at the ends of every (curved / per-segment) arrow.
            lines = self._run_alg_safe(
                "geometrybyexpression", "native", INPUT=lines, OUTPUT_GEOMETRY=1,
                EXPRESSION=mat.arrow_body_for(recipe, f"EPSG:{_EPSG_CRS}", cuts=False))
            lines = self._run_alg_safe("multiparttosingleparts", "native", INPUT=lines)
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
        if "Interval" in recipe.placements:
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
            tmp = QgsVectorLayer(current_input, "tmp", "ogr")
            if tmp.isValid():
                for f in tmp.fields():
                    if 'ogc_fid' not in f.name().lower():
                        mapping.append((f.type(), f'"{f.name()}"', f.name()))


        mapping.append(
            (6, f'"{_FIELD_PREFIX}_orig_id"', f"{_FIELD_PREFIX}_orig_id")
        )
        source_fields = QgsVectorLayer(current_input, "fields", "ogr").fields()
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
        """Wrap successful outputs in QgsVectorLayer; report failures."""
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
            layer = QgsVectorLayer(on_disk, grp.output_dataset, "ogr")
            if layer.isValid() and layer.featureCount() > 0:
                self.processed_layers.append(layer)
                successful_rules.extend(grp.flat_rules)
            else:
                for rule in grp.flat_rules:
                    if rule in self.flattened_rules:
                        self.flattened_rules.remove(rule)
        return self.processed_layers, successful_rules

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
        context = QgsProcessingContext()
        context.setExpressionContext(self._worker_expression_context())
        if not self._planar:
            context.setEllipsoid(self._ellipsoid)
        context.setDistanceUnit(self._distance_unit)
        context.setAreaUnit(self._area_unit)
        context.setInvalidGeometryCheck(QgsFeatureRequest.InvalidGeometryCheck.GeometryNoCheck)
        feedback = QgsProcessingFeedback()

        if params.get("OUTPUT") in (None, "TEMPORARY_OUTPUT"):
            params["OUTPUT"] = self._temp_path("temp")

        full_name = f"{algorithm_type}:{algorithm}"
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
        point = f"make_point({x}, {y})"
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
        if settings is None or flat_rule.get_attr("g") != 1 or \
                settings.geometryGeneratorEnabled or self._pinned_position(settings):
            return
        placement = getattr(settings.placement, "value", settings.placement)
        if int(placement) not in (int(Qgis.LabelPlacement.Line),
                                  int(Qgis.LabelPlacement.Curved)):
            return
        if float(settings.repeatDistance or 0) > 0:
            return
        try:
            flags = int(settings.lineSettings().placementFlags())
        except (AttributeError, TypeError):
            flags = 1
        side = "on" if flags & 1 or not flags & 6 else ("above" if flags & 2 else "below")
        quadrant = {"on": Qgis.LabelQuadrantPosition.Over,
                    "above": Qgis.LabelQuadrantPosition.Above,
                    "below": Qgis.LabelQuadrantPosition.Below}[side]
        rotation = (
            f"with_variable('q2vt_l', {self._LONGEST_PART}, with_variable('q2vt_a', "
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

    def _get_labeling_transformation(self, flat_rule: FlattenedRule):
        settings = flat_rule.rule.settings()
        target_geom = flat_rule.get_attr("g")
        transform_expr = "@geometry"
        if getattr(flat_rule, "line_label_midpoint", False):
            flat_rule.set_attr("c", 0)
            return [0, f"with_variable('q2vt_l', {self._LONGEST_PART}, "
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
        if recipe is not None and recipe.kind == "arrow_body":
            if recipe.param("taper"):
                return [1, "@geometry"]  # bodies built by _tapered_arrows
            return [1, mat.arrow_body_for(recipe, f"EPSG:{_EPSG_CRS}")]
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
        return dataclasses.replace(
            label, output_dataset=name, geometry_target=1,
            geometry_expression=self._clip_to_extent("@geometry"),
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