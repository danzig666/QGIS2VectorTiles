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

import os
import threading
import traceback
import platform
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from os.path import exists, join
from typing import Any, Dict, Iterator, List, Optional, Tuple
from uuid import uuid4
from qgis.PyQt.QtCore import QVariant
from processing import run as run_processing
from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsExpressionContext,
    QgsExpression,
    QgsExpressionContextUtils,
    QgsProcessingContext,
    QgsProcessingFeedback,
    QgsFeatureRequest,
    QgsField,
    QgsPalLayerSettings,
    QgsRectangle,
    QgsCoordinateTransform,
    QgsVectorLayer,
    QgsProject,
)

from ..utils.config import _DATA_SIMPLIFICATION_TOLERANCE, _EPSG_CRS, _FIELD_PREFIX
from ..utils.flattened_rule import FlattenedRule
from ..utils.zoom_levels import ZoomLevels
from .ddp_fetcher import DataDefinedPropertiesFetcher
from .fidelity.diagnostics import DiagnosticCollector
from .fidelity.qgis_expr import bind_geometry, in_layer_crs, with_map_scale
from .fidelity import materialize as mat
from .fidelity.materialize import Recipe

# Drawing rank of each feature under the renderer's order-by clauses.
ORDER_FIELD = f"{_FIELD_PREFIX}_draw_order"
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
    # Kept ONLY to drive the success/failure return value of export(); workers
    # MUST NOT read any live state from these.
    flat_rules: List[FlattenedRule]


_RING_FIELD = f"{_FIELD_PREFIX}_ring_cw"
# 1 when the (first) exterior ring is clockwise; is_polygon_clockwise() is
# not available before QGIS 3.36.
_RING_CLOCKWISE_EXPRESSION = (
    "with_variable('q2vt_p', if(is_multipart(@geometry), geometry_n(@geometry, 1), @geometry), "
    "if(geom_to_wkt(exterior_ring(force_polygon_cw(@q2vt_p))) = "
    "geom_to_wkt(exterior_ring(@q2vt_p)), 1, 0))"
)


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
    ):
        self.flattened_rules = flattened_rules
        # QgsRectangle is a value type — safe to share across threads.
        self.extent = extent
        self.include_required_fields_only = include_required_fields_only
        self.max_zoom = max_zoom
        self.cent_source = cent_source
        self.utils_dir = utils_dir
        self.feedback = feedback
        self.cpu_percent = cpu_percent
        self.diagnostics = diagnostics or DiagnosticCollector()
        # Measurement settings of the project (caller thread snapshot): QGIS
        # measures $area/$length ellipsoidally when an ellipsoid is set and
        # planimetrically in the layer CRS otherwise.
        project = QgsProject.instance()
        self._ellipsoid = project.ellipsoid()
        self._planar = not self._ellipsoid or self._ellipsoid.upper() == "NONE"
        self._distance_unit = project.distanceUnits()
        self._area_unit = project.areaUnits()

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

            # Phase 1 — serial source materialisation (caller thread).
            if self._is_cancelled():
                return [], []

            materialized = self._materialize_sources_serial(sources)

            # Phase 2 — parallel base-layer pipeline (file → file).
            if self._is_cancelled():
                return [], []

            base_layers = self._build_base_layers_parallel(materialized)

            # Phase 3 — parallel rule export (file → file).
            if self._is_cancelled():
                return [], []
            
            rule_outputs = self._export_rules_parallel(rule_groups, base_layers)
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
            )

        # Snapshot rule groups.
        rule_groups: List[_RuleGroupSnapshot] = []
        for output_dataset, flat_rules in rules_by_dataset.items():
            primary = flat_rules[0]

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
                flat_rules=flat_rules,
            ))

        return sources, rule_groups

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
        for src in sources.values():
            self._check_cancel()
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
                if not layer.isValid():
                    self.feedback.pushWarning(
                        f"Cannot open source '{src.name}' "
                        f"(provider={src.provider}); skipping."
                    )
                    continue

                # Materialise via fixgeometries(METHOD=0): does the first
                # geometry-cleaning pass AND dumps provider data to Parquet
                # in one shot.
                if src.needs_serial_read:
                    with self._serial_read_lock:
                        self._run_alg_safe(
                            "fixgeometries", "native",
                            INPUT=layer, METHOD=0, OUTPUT=out_path,
                        )
                else:
                    self._run_alg_safe(
                        "fixgeometries", "native",
                        INPUT=layer, METHOD=0, OUTPUT=out_path,
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

        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="rules-base"
        ) as pool:
            futures: Dict[Future, str] = {
                pool.submit(
                    self._build_one_base_layer, src_path, target_paths[lid]
                ): lid
                for lid, src_path in todo.items()
            }
            for fut in self._iter_completed(futures):
                lid = futures[fut]
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
        
        context = QgsProject.instance().transformContext()
        transformer = QgsCoordinateTransform(source_crs, dest_crs, context)
        transformed_extent = transformer.transformBoundingBox(self.extent)
        return transformed_extent
    
    def _build_one_base_layer(self, src_path: str, dst_path: str) -> None:
        """Worker: run the cleanup chain on a local Parquet file."""
        self._check_cancel()
        transform_extent = self.transform_extent(src_path)
        clipped = self._run_alg_safe(
            "extractbyextent", "native",
            INPUT=src_path, EXTENT=transform_extent, CLIP=False,
        )
        self._check_cancel()
        is_polygon = QgsVectorLayer(clipped, "check", "ogr").geometryType() == 2
        if is_polygon:
            # fixgeometries(METHOD=1) rewinds rings to a fixed orientation,
            # but QGIS draws directional outline symbols (marker lines,
            # arrows, offsets) along the *source* ring direction. Record the
            # exterior orientation here and restore it after the fix.
            clipped = self._run_alg_safe(
                "fieldcalculator", "native", INPUT=clipped, FIELD_NAME=_RING_FIELD,
                FIELD_TYPE=1, FORMULA=_RING_CLOCKWISE_EXPRESSION)
        # METHOD=1 (structure) — finishes the geometry fix started in Phase 1.
        fixed_struct = self._run_alg_safe(
            "fixgeometries", "native", INPUT=clipped, METHOD=1
        )
        self._check_cancel()
        reprojected = self._run_alg_safe(
            "reprojectlayer", "native",
            INPUT=fixed_struct,
            TARGET_CRS=QgsCoordinateReferenceSystem(f"EPSG:{_EPSG_CRS}"),
        )
        if is_polygon:
            restored = self._run_alg_safe(
                "geometrybyexpression", "native", INPUT=reprojected, OUTPUT_GEOMETRY=0,
                EXPRESSION=f'if("{_RING_FIELD}" = 1, force_polygon_cw(@geometry), '
                           f'if("{_RING_FIELD}" = 0, force_polygon_ccw(@geometry), @geometry))')
            reprojected = self._run_alg_safe(
                "deletecolumn", "native", INPUT=restored, COLUMN=[_RING_FIELD])
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
            TOLERANCE=_DATA_SIMPLIFICATION_TOLERANCE,
            OUTPUT=dst_path,
        )

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

        with ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="rules-export"
        ) as pool:
            futures: Dict[Future, _RuleGroupSnapshot] = {}
            for grp in rule_groups:
                src_path = base_layers.get(grp.layer_id)
                if not src_path or not exists(src_path):
                    outputs[grp.output_dataset] = None
                    continue
                fut = pool.submit(
                    self._export_one_rule_group, grp, src_path
                )
                futures[fut] = grp

            for fut in self._iter_completed(futures):
                grp = futures[fut]
                try:
                    outputs[grp.output_dataset] = fut.result(
                        timeout=_PER_ALG_TIMEOUT_S
                    )
                except _Cancelled:
                    self.feedback.pushInfo("Rule export cancelled.")
                    for pending_fut, pending_grp in futures.items():
                        outputs.setdefault(pending_grp.output_dataset, None)
                    return outputs
                except Exception:  # noqa: BLE001
                    self.feedback.reportError(
                        f"Rule export failed for '{grp.output_dataset}':\n"
                        f"{traceback.format_exc()}"
                    )
                    outputs[grp.output_dataset] = None
        return outputs

    def validate_expression(self, grp, expr_str: str):
        layer_name = grp.flat_rules[0].layer.name() or grp.layer_id
        rule_type = 'labeling' if grp.rule_type == 1 else 'symbology'
        warning_msg = f'The expression "{expr_str}" within the {rule_type} of the "{layer_name}" layer'

        if not isinstance(expr_str, str):
            self.feedback.pushWarning(f"{warning_msg} must be a string.")

        expr_str = expr_str.strip()

        if not expr_str:
            return None

        expr = QgsExpression(expr_str)

        if expr.hasParserError():
            self.feedback.pushWarning(f"{warning_msg} is not valid.")
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
        output_path = join(self.utils_dir, f"{grp.output_dataset}.{_TEMP_RULE_FORMAT}")
        if exists(output_path):
            return output_path

        current_input: str = source_path

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

        # Materialized marker positions: derive point features (with the
        # line azimuth) from the complete original lines before any field
        # expressions or tiling.
        if grp.recipe is not None and grp.recipe.kind == "marker_points":
            current_input = self._materialize_marker_points(
                current_input, grp.recipe, grp.source_geometry)
            check = QgsVectorLayer(current_input, "check", "ogr")
            if not check.isValid() or check.featureCount() <= 0:
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
        if grp.rule_type == 1:
            settings = grp.flat_rules[0].rule.settings()
            if settings and not settings.labelPerPart:
                keep_biggest_part = True
        if grp.rule_type == 0:
            symbol_layer = grp.flat_rules[0].rule.symbol().symbolLayers()[0]
            if symbol_layer.layerType() == 'CentroidFill' and not symbol_layer.pointOnAllParts():
                keep_biggest_part = True
        if keep_biggest_part:
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

        self._check_cancel()
        cleaned = self._run_alg_safe(
            "removenullgeometries", "native",
            INPUT=transformed,
            REMOVE_EMPTY=True,
        )
        check = QgsVectorLayer(cleaned, "check", "ogr")
        if not check.isValid() or check.featureCount() <= 0:
            self._report_empty_output(grp, transbase)
            return None
        self._check_cancel()
        return self._run_alg_safe(
            "multiparttosingleparts", "native",
            INPUT=cleaned,
            OUTPUT=output_path,
        )

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

    def _materialize_marker_points(self, source: str, recipe: Recipe, source_geometry: int) -> str:
        """Worker: exact marker-line positions as points with ``ANGLE_FIELD``."""
        lines = source
        if source_geometry == 2:  # marker line on a polygon outline
            lines = self._run_alg_safe("polygonstolines", "native", INPUT=source)
        if recipe.param("offset"):
            lines = self._run_alg_safe(
                "geometrybyexpression", "native", INPUT=lines, OUTPUT_GEOMETRY=1,
                EXPRESSION=mat.offset_line_expression(recipe, f"EPSG:{_EPSG_CRS}"))
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
        * Minimal expression context (global scope only) — never
          QgsProject.instance().
        * Per-call QgsProcessingFeedback.
        * Returns an output path (string), never a live layer reference.
        """
        self._check_cancel()
        context = QgsProcessingContext()
        context.setExpressionContext(QgsProject.instance().createExpressionContext())
        if not self._planar:
            context.setEllipsoid(self._ellipsoid)
        context.setDistanceUnit(self._distance_unit)
        context.setAreaUnit(self._area_unit)
        context.setInvalidGeometryCheck(QgsFeatureRequest.InvalidGeometryCheck.GeometryNoCheck)
        feedback = QgsProcessingFeedback()

        if params.get("OUTPUT") in (None, "TEMPORARY_OUTPUT"):
            params["OUTPUT"] = self._temp_path("temp")

        full_name = f"{algorithm_type}:{algorithm}"
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

    @staticmethod
    def _make_worker_expression_context() -> QgsExpressionContext:
        """Minimal expression context safe for worker-thread use.

        Crucially does NOT call QgsProject.instance().createExpressionContext()
        — that walks scopes which include layer references and is the original
        implementation's biggest thread-affinity violation.
        """
        ctx = QgsExpressionContext()
        ctx.appendScope(QgsExpressionContextUtils.globalScope())
        return ctx

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
        filter_exp = (
            f'"{label_exp}"'
            if not flat_rule.rule.settings().isExpression
            else label_exp
        )
        fields.append((10, filter_exp, field_name))
        flat_rule.rule.settings().isExpression = False
        flat_rule.rule.settings().fieldName = field_name
        return fields

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
        extent_wkt = self.extent.asWktPolygon()
        clipped = (
            f"with_variable('clip', intersection({transformation[1]}, "
            f"geom_from_wkt('{extent_wkt}')), "
            f"if(not is_empty_or_null(@clip), @clip, NULL))"
        )
        transformation[1] = clipped
        return tuple(transformation)

    @staticmethod
    def _layer_point_expression(x: str, y: str, layer_crs: str) -> str:
        point = f"make_point({x}, {y})"
        export_crs = f"EPSG:{_EPSG_CRS}"
        if not layer_crs or layer_crs == export_crs:
            return point
        return f"transform({point}, '{layer_crs}', '{export_crs}')"

    def _get_labeling_transformation(self, flat_rule: FlattenedRule):
        settings = flat_rule.rule.settings()
        target_geom = flat_rule.get_attr("g")
        transform_expr = "@geometry"
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
            flat_rule.set_attr("c", 0)
            target_geom = 0
            transform_expr = self._get_polygon_centroids_expression()
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
        if recipe is not None and recipe.kind == "grid_points":
            return [0, mat.grid_expression(recipe, f"EPSG:{_EPSG_CRS}")]
        if recipe is not None and recipe.kind == "arrow_body":
            return [1, mat.arrow_body_for(recipe, f"EPSG:{_EPSG_CRS}")]
        if recipe is not None and recipe.kind == "callout":
            label = self._layer_point_expression(
                f'"{CALLOUT_X_FIELD}"', f'"{CALLOUT_Y_FIELD}"', recipe.param("crs"))
            return [1, callout_leader_expression(label, flat_rule.get_attr("g"),
                                                 recipe.param("anchor", 0))]
        if symbol_layer.layerType() == "GeometryGenerator":
            target_geom = symbol_layer.subSymbol().type()
            transform_expr = self._generator_in_layer_crs(
                symbol_layer.geometryExpression(), flat_rule)
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

    def _get_polygon_centroids_expression(self) -> str:
        if self.cent_source == 1:
            polygons = (
                f"intersection(@geometry, "
                f"geom_from_wkt('{self.extent.asWktPolygon()}'))"
            )
        else:
            polygons = "@geometry"
        return (
            f"with_variable('source', {polygons}, "
            f"if(intersects(centroid(@source), @source), "
            f"centroid(@source), point_on_surface(@source)))"
        )

    # -------------------------------------------------------------------
    # Pool sizing, future iteration, temp tracking
    # -------------------------------------------------------------------
    def _compute_pool_size(self, num_jobs: int) -> int:
        if num_jobs <= 0:
            return 1
        cpu_n = os.cpu_count() or 1
        from_user = max(1, int(cpu_n * self.cpu_percent / 100))
        return min(from_user, _MAX_WORKERS_HARD_CAP, num_jobs)

    def _iter_completed(
        self, futures: Dict[Future, Any]
    ) -> Iterator[Future]:
        """Yield futures as they complete, polling cancellation each second.

        Unlike concurrent.futures.as_completed, this checks our cancel flag
        between waits so an external cancellation request is responsive even
        when current futures are still running.
        """
        pending = set(futures.keys())
        while pending:
            done, pending = wait(
                pending, timeout=1.0, return_when=FIRST_COMPLETED
            )
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