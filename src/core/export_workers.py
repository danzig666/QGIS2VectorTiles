"""
export_workers.py

The rule export in worker processes. QGIS Processing is not safe in parallel
threads of one QGIS (on QGIS 3.44 / Windows it corrupted the heap), so the
export ran one dataset after the other; worker processes are separate QGIS
instances (started headless from QGIS's own Python), each exporting base
layers and rule groups from the files of the main export, with the main
export's settings: expression variables, transform context, measurement
units, zoom scale. They write the same files the main process would; it only
hands out the work, collects the results and diagnostics, and does what a
worker cannot (a group whose expressions need the project's layers, a
function only the main QGIS knows, or whatever a worker failed at).

Protocol: pickled messages, each preceded by its length (8 bytes), on the
worker's stdin / stdout; the worker's own output goes to a log file.
"""

import dataclasses
import json
import os
import pickle
import queue
import re
import shutil
import struct
import subprocess
import sys
import threading
import time
import traceback
from typing import Dict, List, Optional, Tuple

# Expressions a worker cannot evaluate as the main QGIS does: they read other
# layers of the project, its colours, or evaluate text as an expression.
_PARENT_FUNCTIONS = re.compile(
    r"\b(aggregate|relation_aggregate|get_feature|get_feature_by_id|overlay_\w+|project_color\w*|"
    r"layer_property|represent_value|represent_attributes|sqlite_fetch_and_increment|load_layer|"
    r"eval|eval_template|decode_uri|raster_value|raster_attributes|attribute)\s*\(", re.IGNORECASE)
_VARIABLE = re.compile(r"@(\w+)")
_READY_TIMEOUT_S = 60

_HEADER = struct.Struct("<Q")


def send(stream, message) -> None:
    data = pickle.dumps(message, protocol=4)
    stream.write(_HEADER.pack(len(data)))
    stream.write(data)
    stream.flush()


def receive(stream):
    header = stream.read(_HEADER.size)
    if len(header) < _HEADER.size:
        raise EOFError("worker stream closed")
    (size,) = _HEADER.unpack(header)
    data = b""
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            raise EOFError("worker stream closed")
        data += chunk
    return pickle.loads(data)


# ---------------------------------------------------------------------------
# Main process
# ---------------------------------------------------------------------------

def python_executable() -> Optional[str]:
    """QGIS's own Python interpreter (sys.executable is QGIS itself there)."""
    version = f"python{sys.version_info[0]}.{sys.version_info[1]}"
    candidates = []
    if os.path.basename(sys.executable or "").lower().startswith("python"):
        candidates.append(sys.executable)
    for prefix in (sys.exec_prefix, sys.base_exec_prefix):
        if os.name == "nt":
            candidates += [os.path.join(prefix, "python.exe"), os.path.join(prefix, "pythonw.exe")]
        else:
            candidates += [os.path.join(prefix, "bin", version), os.path.join(prefix, "bin", "python3")]
    candidates += [shutil.which(version), shutil.which("python3")]
    return next((c for c in candidates if c and os.path.isfile(c)), None)


def _scope_variables(scope) -> Tuple[Dict[str, tuple], List[str]]:
    """{name: (value, static)} of the variables that can be sent, and the
    names of those that cannot."""
    values, missing = {}, []
    for name in scope.variableNames():
        if name == "_project_transform_context":
            continue  # rebuilt in the worker from the transform context
        value = scope.variable(name)
        try:
            pickle.dumps(value, protocol=4)
        except Exception:  # noqa: BLE001 - layers and other live objects
            missing.append(name)
            continue
        values[name] = (value, scope.isStatic(name))
    return values, missing


def worker_state(exporter) -> dict:
    """What a worker needs to export as ``exporter`` (a RulesExporter)."""
    # pylint: disable=import-outside-toplevel,protected-access
    from qgis.core import QgsApplication, QgsReadWriteContext, QgsUnitTypes
    from qgis.PyQt.QtXml import QDomDocument
    from .fidelity import zoom as fidelity_zoom
    document = QDomDocument()
    root = document.createElement("transformContext")
    document.appendChild(root)
    exporter._transform_context.writeXml(root, QgsReadWriteContext())
    global_values, global_missing = _scope_variables(exporter._global_scope)
    project_values, project_missing = _scope_variables(exporter._project_scope)
    extent = exporter.extent
    return {
        "prefix": QgsApplication.prefixPath(),
        "extent": (extent.xMinimum(), extent.yMinimum(), extent.xMaximum(), extent.yMaximum()),
        "include_required_fields_only": exporter.include_required_fields_only,
        "max_zoom": exporter.max_zoom, "utils_dir": exporter.utils_dir,
        "cent_source": exporter.cent_source, "cpu_percent": exporter.cpu_percent,
        "feature_keys": exporter.feature_keys, "extra_tile_fields": exporter.extra_tile_fields,
        "map_crs": exporter._map_crs, "ellipsoid": exporter._ellipsoid, "planar": exporter._planar,
        "distance_unit": QgsUnitTypes.encodeUnit(exporter._distance_unit),
        "area_unit": QgsUnitTypes.encodeUnit(exporter._area_unit),
        "transform_context": document.toString(),
        "global_scope": global_values, "project_scope": project_values,
        "missing_variables": sorted(set(global_missing) | set(project_missing)),
        "layer_names": dict(exporter._layer_names),
        "top_scale": fidelity_zoom.TOP_SCALE,
    }


def needs_main_process(grp, missing_variables) -> bool:
    """Whether a rule group's expressions need the main QGIS (its project's
    layers, colours or variables a worker does not have)."""
    text = repr(dataclasses.replace(grp, flat_rules=[]))
    if _PARENT_FUNCTIONS.search(text):
        return True
    missing = set(missing_variables)
    return any(name in missing for name in _VARIABLE.findall(text))


class WorkerPool:
    """Worker processes of one rule export (main process side)."""

    def __init__(self, count: int, state: dict, log_dir: str):
        self.workers: List[subprocess.Popen] = []
        self.results: "queue.Queue" = queue.Queue()
        self.alive: List[bool] = []
        self.error = ""
        python = python_executable()
        if python is None:
            self.error = "no Python interpreter found next to QGIS"
            return
        package = __name__.rsplit(".src.core.", 1)[0]
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        env = dict(os.environ)
        env.update({"QT_QPA_PLATFORM": "offscreen", "Q2VT_WORKER_PACKAGE": package,
                    "Q2VT_WORKER_PREFIX": state["prefix"],
                    "Q2VT_WORKER_ROOT": root, "Q2VT_WORKER_PATH": json.dumps(sys.path),
                    "PYTHONIOENCODING": "utf-8"})
        env.pop("PYTHONSTARTUP", None)
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "export_worker_main.py")
        options = {}
        if os.name == "nt":
            options["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        for number in range(count):
            log = open(os.path.join(log_dir, f"worker_{number}.log"), "wb")  # pylint: disable=consider-using-with
            try:
                process = subprocess.Popen(  # pylint: disable=consider-using-with
                    [python, "-u", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=log, env=env, **options)
            except OSError as error:
                self.error = f"cannot start {python}: {error}"
                log.close()
                break
            process.log = log  # type: ignore[attr-defined]
            self.workers.append(process)
            self.alive.append(True)
            threading.Thread(target=self._read, args=(number, process), daemon=True,
                             name=f"q2vt-worker-{number}").start()
            try:
                send(process.stdin, ("init", state))
            except OSError as error:
                self.error = f"worker {number}: {error}"

    def _read(self, number: int, process) -> None:
        while True:
            try:
                message = receive(process.stdout)
            except (EOFError, OSError, pickle.UnpicklingError, ValueError):
                self.results.put((number, ("exit", process.poll())))
                return
            self.results.put((number, message))

    def wait_ready(self, version: str, cancelled) -> List[int]:
        """The workers that started and run this QGIS version."""
        ready, waiting = [], set(range(len(self.workers)))
        deadline = time.monotonic() + _READY_TIMEOUT_S
        while waiting and time.monotonic() < deadline and not cancelled():
            try:
                number, message = self.results.get(timeout=0.2)
            except queue.Empty:
                continue
            waiting.discard(number)
            if message[0] == "ready" and message[1] == version:
                ready.append(number)
            else:
                self.alive[number] = False
                detail = message[1] if len(message) > 1 else ""
                self.error = self.error or f"worker {number}: {message[0]} {str(detail)[-500:]}"
        for number in waiting:
            self.alive[number] = False
            self.error = self.error or f"worker {number} did not start in {_READY_TIMEOUT_S} s"
        return ready

    def submit(self, number: int, message) -> bool:
        try:
            send(self.workers[number].stdin, message)
            return True
        except (OSError, ValueError):
            self.alive[number] = False
            return False

    def close(self, kill: bool = False) -> None:
        """Stop the workers: they finish in the background (cleaning up their
        files takes them a moment); ``kill``: at once (cancelled export)."""
        for number, process in enumerate(self.workers):
            if not kill and self.alive[number]:
                try:
                    send(process.stdin, ("stop",))
                except (OSError, ValueError):
                    pass
        if kill:
            self._reap(True)
        else:
            threading.Thread(target=self._reap, args=(False,), daemon=True, name="q2vt-workers-stop").start()

    def _reap(self, kill: bool) -> None:
        for process in self.workers:
            try:
                if kill:
                    process.kill()
                process.wait(timeout=5 if kill else 30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            except OSError:
                pass
            for stream in (process.stdin, process.stdout):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            process.log.close()


# ---------------------------------------------------------------------------
# Worker process
# ---------------------------------------------------------------------------

def _scope(name: str, variables: Dict[str, tuple]):
    from qgis.core import QgsExpressionContextScope  # pylint: disable=import-outside-toplevel
    scope = QgsExpressionContextScope(name)
    for key, (value, static) in variables.items():
        scope.setVariable(key, value, static)
    return scope


def _collecting_feedback():
    """The worker exporter's feedback: its messages go to the main process."""
    from qgis.core import QgsProcessingFeedback  # pylint: disable=import-outside-toplevel

    class Collecting(QgsProcessingFeedback):
        def __init__(self):
            super().__init__()
            self.messages = []

        def pushInfo(self, info):  # noqa: N802
            self.messages.append(("pushInfo", info))

        def pushWarning(self, warning):  # noqa: N802
            self.messages.append(("pushWarning", warning))

        def reportError(self, error, fatalError=False):  # noqa: N802,N803
            self.messages.append(("reportError", error))
    return Collecting()


def _build_exporter(state: dict):
    """A RulesExporter of this worker with the main export's settings."""
    # pylint: disable=import-outside-toplevel,protected-access
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransformContext, QgsProject,
                           QgsReadWriteContext, QgsRectangle, QgsUnitTypes)
    from qgis.PyQt.QtXml import QDomDocument
    from ..utils.zoom_levels import ZoomLevels
    from .fidelity.diagnostics import DiagnosticCollector
    from .rules_exporter import RulesExporter
    ZoomLevels.configure(state["top_scale"])
    document = QDomDocument()
    document.setContent(state["transform_context"])
    transform_context = QgsCoordinateTransformContext()
    transform_context.readXml(document.documentElement(), QgsReadWriteContext())
    project = QgsProject.instance()
    project.setCrs(QgsCoordinateReferenceSystem(state["map_crs"]))
    project.setTransformContext(transform_context)
    project.setEllipsoid(state["ellipsoid"] or "NONE")
    distance, _ = QgsUnitTypes.decodeDistanceUnit(state["distance_unit"])
    area, _ = QgsUnitTypes.decodeAreaUnit(state["area_unit"])
    project.setDistanceUnits(distance)
    project.setAreaUnits(area)
    exporter = RulesExporter(
        [], QgsRectangle(*state["extent"]), state["include_required_fields_only"], state["max_zoom"],
        state["utils_dir"], state["cent_source"], _collecting_feedback(),
        cpu_percent=state["cpu_percent"], diagnostics=DiagnosticCollector(),
        feature_keys=state["feature_keys"], extra_tile_fields=state["extra_tile_fields"])
    exporter._map_crs = state["map_crs"]
    exporter._ellipsoid = state["ellipsoid"]
    exporter._planar = state["planar"]
    exporter._distance_unit, exporter._area_unit = distance, area
    exporter._transform_context = transform_context
    exporter._global_scope = _scope("Global", state["global_scope"])
    project_scope = _scope("Project", state["project_scope"])
    project_scope.setVariable("_project_transform_context", transform_context, True)
    exporter._project_scope = project_scope
    exporter._layer_names = dict(state["layer_names"])
    return exporter


def _parses(grp) -> bool:
    """Every expression of the group parses here (a function the main QGIS
    knows from a plugin does not: the main process exports that group)."""
    from qgis.core import QgsExpression  # pylint: disable=import-outside-toplevel
    expressions = [grp.filter_expression, grp.geometry_expression, grp.pre_generator]
    for fields in (grp.expression_fields, grp.part_fields, grp.generated_fields):
        expressions += [expression for _, expression, _ in fields]
    return all(not QgsExpression(e).hasParserError() for e in expressions if e)


def _messages(exporter) -> list:
    out = list(exporter.feedback.messages)
    exporter.feedback.messages.clear()
    while True:
        try:
            out.append(exporter._messages.get_nowait())  # pylint: disable=protected-access
        except queue.Empty:
            return out


def serve(stdin, stdout) -> None:
    """The worker's loop: init, then base layers and rule groups until stop."""
    # pylint: disable=protected-access
    from qgis.core import Qgis  # pylint: disable=import-outside-toplevel
    from . import export_cache  # pylint: disable=import-outside-toplevel
    from .datasets import gpkg_info  # pylint: disable=import-outside-toplevel
    exporter = None
    while True:
        try:
            message = receive(stdin)
        except EOFError:
            break
        kind = message[0]
        if kind == "stop":
            break
        if kind == "init":
            try:
                exporter = _build_exporter(message[1])
                exporter._memory_active = False
                send(stdout, ("ready", Qgis.version()))
            except Exception:  # noqa: BLE001 - reported to the main process
                send(stdout, ("init-failed", traceback.format_exc()))
                break
            continue
        if kind == "source":  # read a source (unless read already), build its base layer
            _, layer_id, source, materialized, target, keep, anchored = message
            began = time.perf_counter()
            try:
                path = materialized if source is None else exporter._materialize_one(source)
                if not path:
                    raise RuntimeError(f"source {layer_id} not read")
                exporter._build_base_layer(path, target, keep, anchored)
                send(stdout, ("base-done", layer_id, None, time.perf_counter() - began,
                              _messages(exporter)))
            except Exception:  # noqa: BLE001
                send(stdout, ("base-done", layer_id, traceback.format_exc(), time.perf_counter() - began,
                              _messages(exporter)))
            continue
        if kind == "groups":
            for grp, source in message[1]:
                if not _parses(grp):
                    send(stdout, ("group-main", grp.output_dataset))
                    continue
                began = time.perf_counter()
                error = output = None
                try:
                    output = exporter._export_one_rule_group_recorded(grp, source)
                except Exception:  # noqa: BLE001
                    error = traceback.format_exc()
                diagnostics = export_cache.diagnostics_to_dicts(
                    exporter._group_diagnostics.pop(grp.output_dataset, []))
                info = gpkg_info(output) if output and not error else None
                send(stdout, ("group-done", grp.output_dataset, output, error, diagnostics,
                              time.perf_counter() - began, tuple(info) if info else None,
                              _messages(exporter)))
            send(stdout, ("task-done",))
            continue
    if exporter is not None:
        exporter._release_all_layers()
        exporter._cleanup_temp_files()
