"""
tiles_generator.py

GDALTilesGenerator — builds a multi-layer OGR VRT from the exported
datasets and calls ogr2ogr to produce MVT (Mapbox Vector Tiles) in MBTiles
format.

* Per-layer zoom ranges come from explicit metadata (the flattened rules),
  with the legacy filename parsing kept only as a fallback.
* There is no hidden per-layer zoom cap: the archive covers every requested
  zoom (the legacy VRT silently capped each layer at zoom 16).
* The VRT is written with an XML library, so paths containing ``&``, ``<``
  or non-ASCII characters are escaped correctly.
* Generated ``q2vt_property_*`` fields are pruned from the style's actual
  per-source-layer dependencies, not by searching ``str(style)``.
* ogr2ogr runs as an argument list (no shell) and is terminated when the
  user cancels.
"""

import heapq
import itertools
import json
import math
import os
import re
import sqlite3
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
import zlib
from concurrent.futures import ThreadPoolExecutor
from os import cpu_count
from os.path import join, basename
from typing import Dict, List, Optional, Tuple
from osgeo import ogr
from qgis.core import QgsVectorLayer, QgsProcessingFeedback, QgsProcessingUtils

from ..utils import main_thread
from ..utils.config import _EPSG_CRS, _SIMPLIFICATION, _SIMPLIFICATION_MAX_ZOOM
from .fidelity.dependencies import prunable_fields, style_field_dependencies
from . import export_cache
from .datasets import drop_fields, gpkg_info

_MVT_MAX_ZOOM = 22


class TilesGenerationCancelled(RuntimeError):
    """Raised when the user cancels while ogr2ogr is running."""


# Seconds per unit of TileProgress cost until a job has finished (measured
# 6e-8 .. 1.1e-7 with 4 jobs on 4 cores; the finished jobs correct it).
_SECONDS_PER_COST = 1e-7
# Cost of one output tile, in source bytes.
_TILE_COST = 2000


class TileProgress:
    """Estimated progress of ogr2ogr jobs, which report none themselves.

    A job's cost is the size of its datasets times their zoom levels plus
    the tiles of the extent at those zooms. A running job advances with its
    elapsed time against the time its cost should take at the rate of the
    jobs already finished (a fixed guess before the first one); past that
    time it still creeps on, never reaching its end before it finishes.
    """

    def __init__(self, costs: List[float]):
        self.costs = [max(1.0, float(c)) for c in costs]
        self.total = sum(self.costs)
        self.started: Dict[int, float] = {}
        self.finished: Dict[int, float] = {}
        self._last = 0.0

    def start(self, job: int, now: float) -> None:
        self.started[job] = now

    def finish(self, job: int, now: float) -> None:
        self.finished[job] = now - self.started.get(job, now)

    def rate(self) -> float:
        cost = sum(self.costs[j] for j in self.finished)
        seconds = sum(self.finished.values())
        return seconds / cost if cost > 0 and seconds > 0 else _SECONDS_PER_COST

    def fraction(self, now: float) -> float:
        rate = self.rate()
        done = sum(self.costs[j] for j in self.finished)
        for job, began in self.started.items():
            if job in self.finished:
                continue
            x = (now - began) / max(1e-9, self.costs[job] * rate)
            part = 0.9 * x if x <= 1 else 0.9 + 0.09 * (1 - math.exp(-(x - 1)))
            done += self.costs[job] * part
        self._last = max(self._last, min(1.0, done / self.total if self.total else 1.0))
        return self._last


class GDALTilesGenerator:
    """Generate MBTiles vector tiles using GDAL CLI with an OGR VRT intermediary."""

    def __init__(
        self,
        layers: List[QgsVectorLayer],
        style: dict,
        output_dir: str,
        extent,
        cpu_percent: int,
        feedback: QgsProcessingFeedback,
        layer_zooms: Optional[Dict[str, Tuple[int, int]]] = None,
        cache: Optional["export_cache.ExportCache"] = None,
        dataset_keys: Optional[Dict[str, str]] = None,
        layer_groups: Optional[Dict[str, List[str]]] = None,
        progress_range: Tuple[float, float] = (0.0, 100.0),
    ):
        # With a cache: one tile set per QGIS layer (its datasets), reused
        # while the layer's datasets are unchanged, then merged.
        self.cache = cache
        self.dataset_keys = dataset_keys or {}
        self.layer_groups = layer_groups or {}
        self.layers = layers
        self.style = style
        self.output_dir = output_dir
        self.extent = extent
        self.cpu_percent = cpu_percent
        self.feedback = feedback
        self.layer_zooms = layer_zooms or {}
        self.progress_range = progress_range
        # (table, fields) per dataset file, read once (remove_unused_fields).
        self._datasets: Dict[str, Tuple[Optional[str], List[str]]] = {}
        # ogr2ogr seconds per QGIS layer id (layer_groups), for the export log.
        self.layer_seconds: Dict[str, float] = {}

    def _report(self, fraction: float) -> None:
        if self.feedback is not None:
            low, high = self.progress_range
            self.feedback.setProgress(low + (high - low) * fraction)

    def _tiles_at(self, zoom: int) -> int:
        """Tiles of the export extent at ``zoom`` (whole world without one)."""
        n = 2 ** zoom
        extent = self.extent
        if extent is None or extent.isEmpty():
            return n * n
        half = 20037508.342789244
        size = 2 * half / n

        def span(low, high):
            return int(math.floor((high + half) / size)) - int(math.floor((low + half) / size)) + 1
        return max(1, min(n, span(extent.xMinimum(), extent.xMaximum()))
                   * min(n, span(extent.yMinimum(), extent.yMaximum())))

    def _job_cost(self, members: List[QgsVectorLayer]) -> float:
        """TileProgress cost of tiling ``members``: every feature is cut at
        every zoom, and every tile is written."""
        cost = 0.0
        for layer in members:
            try:
                size = os.path.getsize(layer.source().split("|")[0])
            except OSError:
                size = 0
            low, high = self._layer_zoom_range(layer)
            cost += sum(size + _TILE_COST * self._tiles_at(z) for z in range(low, high + 1))
        return cost

    def generate(self) -> Tuple[str, int]:
        """Build VRT, run ogr2ogr, return (mbtiles URI, min_zoom).

        The tiles of each QGIS layer are made by their own ogr2ogr, several
        at a time, and merged (one ogr2ogr for everything used one core for
        most of its time); Q2VT_SINGLE_OGR2OGR=1 makes them in one run."""
        self.remove_unused_fields()
        output, uri = self._prepare_output_paths()
        vrt_path = join(QgsProcessingUtils.tempFolder(), "layers.vrt")

        min_zoom = self._get_global_min_zoom()
        max_zoom = self._get_global_max_zoom()

        if self.cache is not None or os.environ.get("Q2VT_SINGLE_OGR2OGR") != "1":
            self._generate_per_layer(output, min_zoom, max_zoom)
        else:
            self._build_vrt(vrt_path)
            conf_path = join(QgsProcessingUtils.tempFolder(), "layers_conf.json")
            self._write_layer_conf(conf_path)
            self._run_ogr2ogr(vrt_path, output, min_zoom, max_zoom, conf_path)
        self._prune_tiles_outside_extent(output)

        return uri, min_zoom

    @staticmethod
    def _layer_name(layer: QgsVectorLayer) -> str:
        return basename(layer.source().split("|layername=")[0]).rsplit(".", 1)[0]

    def remove_unused_fields(self):
        """Drop generated property fields that no style layer of *that* source layer reads."""
        dependencies = style_field_dependencies(self.style)
        for layer in self.layers:
            gpkg = layer.source().split("|layername=")[0]
            name = self._layer_name(layer)
            required = set(dependencies.get(name, set()))
            for suffix in ("_vp", "_vl"):  # the polygons / lines of a label: its fields
                if name.endswith(suffix):
                    required |= dependencies.get(name[:-len(suffix)], set())
            info = gpkg_info(gpkg)  # SQLite: a fraction of an OGR open
            if info is not None:
                unused = prunable_fields(info.fields, required)
                if drop_fields(gpkg, info.table, [n for n in info.fields if n in unused]):
                    self._datasets[gpkg] = (info.table, [n for n in info.fields if n not in unused])
                    continue
            ds = ogr.Open(gpkg, update=1)
            if ds is None:
                continue
            ogr_layer = ds.GetLayer(0)
            layer_defn = ogr_layer.GetLayerDefn()
            names = [layer_defn.GetFieldDefn(i).GetName() for i in range(layer_defn.GetFieldCount())]
            unused = prunable_fields(names, required)
            # Iterate backwards because field indices change after deletion.
            for i in range(len(names) - 1, -1, -1):
                if names[i] in unused:
                    ogr_layer.DeleteField(i)
            self._datasets[gpkg] = (ogr_layer.GetName(), [n for n in names if n not in unused])
            ds = None  # Flush changes and close the dataset

    # --- VRT construction ---

    def _build_vrt(self, vrt_path: str, layers: Optional[List[QgsVectorLayer]] = None):
        """Write an OGR VRT containing one entry per layer with per-zoom configuration."""
        root = ET.Element("OGRVRTDataSource")
        for layer in self.layers if layers is None else layers:
            name = self._layer_name(layer)
            min_zoom, max_zoom = self._layer_zoom_range(layer)
            node = ET.SubElement(root, "OGRVRTLayer", name=name)
            source = layer.source().split("|layername=")[0]
            ET.SubElement(node, "SrcDataSource").text = source
            # The table inside the file: a dataset reused from the export cache
            # keeps the name it was first written under (another rule's name
            # when only the rule's zoom range changed); without SrcLayer OGR
            # looks for a table named like the tile layer and writes nothing.
            table = self._table_of(source)
            if table and table != name:
                ET.SubElement(node, "SrcLayer").text = table
            ET.SubElement(node, "LayerSRS").text = f"EPSG:{_EPSG_CRS}"
            ET.SubElement(node, "GeometryType").text = "wkbUnknown"
        ET.ElementTree(root).write(vrt_path, encoding="utf-8", xml_declaration=True)

    def _write_layer_conf(self, conf_path: str, layers: Optional[List[QgsVectorLayer]] = None,
                          band: Optional[Tuple[int, int]] = None):
        """Per-layer zoom ranges for the MVT writer (``-dsco CONF``), cut to
        ``band`` for a piece of zoom levels (the writer makes every zoom of
        CONF, whatever the dataset's MINZOOM / MAXZOOM).

        OGR VRT has no layer-creation options, so zoom ranges put there are
        ignored and every dataset would be written at every zoom level.
        """
        conf = {}
        for layer in self.layers if layers is None else layers:
            min_zoom, max_zoom = self._layer_zoom_range(layer)
            if band is not None:
                min_zoom, max_zoom = max(min_zoom, band[0]), min(max_zoom, band[1])
            conf[self._layer_name(layer)] = {"minzoom": int(min_zoom), "maxzoom": int(max_zoom)}
        with open(conf_path, "w", encoding="utf-8") as handle:
            json.dump(conf, handle)

    # --- ogr2ogr execution ---

    @staticmethod
    def _ogr2ogr_executable() -> str:
        """ogr2ogr from the QGIS/GDAL environment (PATH as set up by QGIS)."""
        return shutil.which("ogr2ogr") or "ogr2ogr"

    def _cpu_num(self) -> int:
        return max(1, int((cpu_count() or 1) * self.cpu_percent / 100))

    def _ogr2ogr_command(self, vrt_path: str, output: str, min_zoom: int, max_zoom: int,
                         conf_path: Optional[str] = None, top_zoom: Optional[int] = None) -> List[str]:
        """``top_zoom``: the export's highest zoom when ``max_zoom`` is that of
        a piece below it (the writer simplifies its MAXZOOM as the highest)."""
        top_simplification = _SIMPLIFICATION if top_zoom is not None and max_zoom < top_zoom \
            else _SIMPLIFICATION_MAX_ZOOM
        cmd = [
            self._ogr2ogr_executable(), "-f", "MBTiles", output, vrt_path,
            "-dsco", f"MINZOOM={min_zoom}",
            "-dsco", f"MAXZOOM={max_zoom}",
            "-t_srs", f"EPSG:{_EPSG_CRS}",
            "-dsco", "MAX_SIZE=5000000",
            "-dsco", "MAX_FEATURES=2000000",
            "-dsco", f"SIMPLIFICATION={_SIMPLIFICATION}",
            "-dsco", f"SIMPLIFICATION_MAX_ZOOM={top_simplification}",
        ]
        if conf_path:
            cmd += ["-dsco", f"CONF={conf_path}"]
        return cmd

    @staticmethod
    def _popen_options() -> dict:
        if os.name != "nt":
            return {}
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        return {"startupinfo": startupinfo, "creationflags": 0x08000000}  # CREATE_NO_WINDOW

    def _run_ogr2ogr(self, vrt_path: str, output: str, min_zoom: int, max_zoom: int,
                     conf_path: Optional[str] = None):
        """Execute ogr2ogr to convert the VRT to MBTiles."""
        env = os.environ.copy()
        env["GDAL_NUM_THREADS"] = str(self._cpu_num())
        cmd = self._ogr2ogr_command(vrt_path, output, min_zoom, max_zoom, conf_path)

        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            creationflags = 0x08000000  # CREATE_NO_WINDOW
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        tracker = TileProgress([self._job_cost(self.layers)])
        with subprocess.Popen(
            cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            startupinfo=startupinfo, creationflags=creationflags,
        ) as proc:
            started = last_message = last_report = time.monotonic()
            tracker.start(0, started)
            while proc.poll() is None:
                now = time.monotonic()
                if now - last_report > 1:
                    last_report = now
                    self._report(tracker.fraction(now))
                if self.feedback is not None and now - last_message > 15:
                    last_message = now
                    self.feedback.pushInfo(
                        f"   Still generating tiles ({(now - started) / 60:.1f} minutes, "
                        f"about {100 * tracker.fraction(now):.0f}% done)...")
                if self.feedback is not None and self.feedback.isCanceled():
                    proc.terminate()
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    if os.path.exists(output):
                        os.remove(output)  # partial archive in the new output dir only
                    raise TilesGenerationCancelled("Tile generation cancelled")
                time.sleep(0.1)
                main_thread.keep_responsive()
            _, stderr = proc.communicate()
            if proc.returncode != 0:
                error_msg = f"ogr2ogr failed.\nError: {stderr}"
                if self.feedback:
                    self.feedback.reportError(error_msg)
                raise RuntimeError(error_msg)

    # --- per-layer tiles (export cache) ---

    @staticmethod
    def _dataset_table(path: str) -> Optional[str]:
        ds = ogr.Open(path)
        if ds is None or ds.GetLayerCount() == 0:
            return None
        name = ds.GetLayer(0).GetName()
        ds = None
        return name

    def _table_of(self, path: str) -> Optional[str]:
        known = self._datasets.get(path)
        return known[0] if known else self._dataset_table(path)

    def _dataset_fields(self, layer: QgsVectorLayer) -> List[str]:
        path = layer.source().split("|layername=")[0]
        if path in self._datasets:
            return list(self._datasets[path][1])
        ds = ogr.Open(path)
        if ds is None:
            return []
        defn = ds.GetLayer(0).GetLayerDefn()
        names = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
        ds = None
        return names

    def _tile_groups(self) -> List[Tuple[str, Optional[str], List[QgsVectorLayer]]]:
        """[(QGIS layer id, cache key or None, datasets)] — one group per QGIS
        layer, in the order of the datasets (the order of one combined export)."""
        by_name = {self._layer_name(layer): layer for layer in self.layers}
        owner = {name: lid for lid, names in self.layer_groups.items() for name in names}
        groups: Dict[str, List[QgsVectorLayer]] = {}
        for name, layer in by_name.items():
            groups.setdefault(owner.get(name, f"dataset:{name}"), []).append(layer)
        out = []
        for owner_id, members in groups.items():
            if self.cache is None:
                out.append((owner_id, None, members))
                continue
            parts = []
            for layer in members:
                name = self._layer_name(layer)
                key = self.dataset_keys.get(name)
                if key is None:
                    parts = None
                    break
                parts.append([name, key, list(self._layer_zoom_range(layer)),
                              self._dataset_fields(layer)])
            out.append((owner_id, export_cache.make_key("tiles", self._zooms, parts,
                                                        _SIMPLIFICATION, _SIMPLIFICATION_MAX_ZOOM)
                        if parts is not None else None, members))
        return out

    def _generate_per_layer(self, output: str, min_zoom: int, max_zoom: int):
        """Tiles of each QGIS layer's datasets (from the cache when unchanged),
        merged into ``output``."""
        self._zooms = [min_zoom, max_zoom]
        work = join(self.output_dir, "layer_tiles")
        os.makedirs(work, exist_ok=True)
        parts, groups = [], []
        for number, (owner, key, members) in enumerate(self._tile_groups()):
            cached = self.cache.get_tiles(key) if key and self.cache is not None else None
            if cached:
                parts.append(cached)
                continue
            target = join(work, f"group_{number:04d}.mbtiles")
            vrt = join(work, f"group_{number:04d}.vrt")
            conf = join(work, f"group_{number:04d}.json")
            self._build_vrt(vrt, members)
            self._write_layer_conf(conf, members)
            groups.append((key, target, vrt, conf, members, owner))
            parts.append(target)
        if self.feedback is not None and self.cache is not None:
            self.feedback.pushInfo(f"   Tiles: {len(parts) - len(groups)} of {len(parts)} layers "
                                   "reused from earlier exports, " f"{len(groups)} to generate")
        # A layer's tiles in several pieces (bands of zoom levels) when it alone
        # would keep one core busy after the others are done.
        cpu = self._cpu_num()
        target_cost = sum(self._job_cost(g[4]) for g in groups) / max(1, cpu) / 2
        jobs = []  # (group index, piece file, command, cost, owner)
        for index, (key, target, vrt, conf, members, owner) in enumerate(groups):
            pieces = self._pieces(members, target_cost) if cpu > 1 else [(min_zoom, max_zoom, 0.0)]
            for number, (low, high, cost) in enumerate(pieces):
                piece = target if len(pieces) == 1 else f"{target[:-8]}_{number:02d}.mbtiles"
                piece_vrt, piece_conf = vrt, conf
                if len(pieces) > 1:  # the datasets of the band, their zooms cut to it
                    inside = [m for m in members if self._layer_zoom_range(m)[0] <= high
                              and self._layer_zoom_range(m)[1] >= low]
                    piece_vrt, piece_conf = piece[:-8] + ".vrt", piece[:-8] + ".json"
                    self._build_vrt(piece_vrt, inside)
                    self._write_layer_conf(piece_conf, inside, (low, high))
                command = self._ogr2ogr_command(piece_vrt, piece, low, high, piece_conf, top_zoom=max_zoom)
                jobs.append((index, piece, command, cost or self._job_cost(members), owner))
        self._run_parallel([job[2] for job in jobs], [job[3] for job in jobs], [job[4] for job in jobs])
        for index, (key, target, *_rest) in enumerate(groups):
            pieces = [job for job in jobs if job[0] == index]
            if len(pieces) > 1:  # no tile in two pieces: their tiles are copied
                merge_mbtiles([job[1] for job in pieces], target, min_zoom, max_zoom, workers=cpu)
        stored = {target: self.cache.put_tiles(key, target)
                  for key, target, *_ in groups if key and self.cache is not None}
        merge_mbtiles([stored.get(path, path) for path in parts], output, min_zoom, max_zoom,
                      workers=cpu)
        shutil.rmtree(work, ignore_errors=True)  # kept in the cache; not needed here

    def _zoom_costs(self, members: List[QgsVectorLayer]) -> Dict[int, float]:
        """TileProgress cost of each zoom level of a layer's datasets."""
        costs: Dict[int, float] = {}
        for layer in members:
            try:
                size = os.path.getsize(layer.source().split("|")[0])
            except OSError:
                size = 0
            low, high = self._layer_zoom_range(layer)
            for zoom in range(low, high + 1):
                costs[zoom] = costs.get(zoom, 0.0) + size + _TILE_COST * self._tiles_at(zoom)
        return costs

    def _pieces(self, members: List[QgsVectorLayer], target: float) -> List[Tuple[int, int, float]]:
        """[(min zoom, max zoom, cost)]: one layer's tile job in bands of zoom
        levels of about ``target`` cost, the highest zooms (most tiles)
        first. The MVT writer makes each zoom level on its own: a band's
        tiles are those of one run (its CONF cut to the band, its highest
        zoom simplified as below the top: _ogr2ogr_command). Not cut into
        areas: a spatial filter reads the features in another order."""
        costs = self._zoom_costs(members)
        whole = sum(costs.values())
        if not costs or whole <= 1.2 * target:
            return [(self._zooms[0], self._zooms[1], whole)]
        bands, band = [], []
        for zoom in sorted(costs, reverse=True):
            if band and sum(costs[z] for z in band) + costs[zoom] > target:
                bands.append(band)
                band = []
            band.append(zoom)
        bands.append(band)
        return [(min(band), max(band), sum(costs[z] for z in band)) for band in bands]

    def _run_parallel(self, commands: List[List[str]], costs: Optional[List[float]] = None,
                      labels: Optional[List[str]] = None):
        """Run ogr2ogr commands, several at a time, polled from this (main)
        thread so QGIS stays responsive and Cancel works. The biggest jobs
        (``costs``) start first, so none of them is left running alone at
        the end; the progress bar follows TileProgress."""
        if not commands:
            self._report(1.0)
            return
        costs = list(costs) if costs and len(costs) == len(commands) else [1.0] * len(commands)
        tracker = TileProgress(costs)
        cpu = self._cpu_num()
        slots = max(1, min(len(commands), cpu))
        env = os.environ.copy()
        env["GDAL_NUM_THREADS"] = str(max(1, cpu // slots))
        waiting = sorted(range(len(commands)), key=lambda j: -costs[j])
        running: Dict[int, subprocess.Popen] = {}
        started = last_message = last_report = time.monotonic()
        try:
            while waiting or running:
                while waiting and len(running) < slots:
                    job = waiting.pop(0)
                    running[job] = subprocess.Popen(
                        commands[job], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        text=True, **self._popen_options())
                    tracker.start(job, time.monotonic())
                for job, proc in [(j, p) for j, p in running.items() if p.poll() is not None]:
                    del running[job]
                    tracker.finish(job, time.monotonic())
                    if labels and job < len(labels):
                        self.layer_seconds[labels[job]] = \
                            self.layer_seconds.get(labels[job], 0.0) + tracker.finished[job]
                    _, stderr = proc.communicate()
                    if proc.returncode != 0:
                        error_msg = f"ogr2ogr failed.\nError: {stderr}"
                        if self.feedback:
                            self.feedback.reportError(error_msg)
                        raise RuntimeError(error_msg)
                if self.feedback is not None and self.feedback.isCanceled():
                    raise TilesGenerationCancelled("Tile generation cancelled")
                now = time.monotonic()
                if now - last_report > 1:
                    last_report = now
                    self._report(tracker.fraction(now))
                if self.feedback is not None and now - last_message > 15:
                    last_message = now
                    self.feedback.pushInfo(
                        f"   Still generating tiles ({(now - started) / 60:.1f} minutes, "
                        f"{len(waiting) + len(running)} layers left, "
                        f"about {100 * tracker.fraction(now):.0f}% done)...")
                time.sleep(0.05)
                main_thread.keep_responsive()
            self._report(1.0)
        finally:
            for proc in running.values():
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()

    def _prune_tiles_outside_extent(self, output: str):
        """Drop tiles that do not intersect the requested extent.

        Source features are selected with a symbol-reach buffer so symbols
        of features just outside the extent still reach into it; the tiles
        created only for that buffer ring are removed again.
        """
        extent = self.extent
        if extent is None or extent.isEmpty() or not os.path.exists(output):
            return
        half = 20037508.342789244
        with sqlite3.connect(output) as conn:
            zooms = [row[0] for row in conn.execute("SELECT DISTINCT zoom_level FROM tiles")]
            for zoom in zooms:
                n = 2 ** zoom
                size = 2 * half / n

                def column(x):
                    return min(n - 1, max(0, int(math.floor((x + half) / size))))
                x0, x1 = column(extent.xMinimum()), column(extent.xMaximum())
                # XYZ rows grow southwards; MBTiles stores TMS rows (from the south).
                tms = sorted((n - 1 - column(-extent.yMinimum()),
                              n - 1 - column(-extent.yMaximum())))
                conn.execute(
                    "DELETE FROM tiles WHERE zoom_level = ? AND (tile_column < ? OR "
                    "tile_column > ? OR tile_row < ? OR tile_row > ?)",
                    (zoom, x0, x1, tms[0], tms[1]))
            conn.commit()
        with sqlite3.connect(output) as conn:
            conn.execute("VACUUM")

    # --- Helpers ---

    def _prepare_output_paths(self) -> Tuple[str, str]:
        output = join(self.output_dir, "tiles.mbtiles")
        return output, f"type=mbtiles&url={output}"

    def _layer_zoom_range(self, layer: QgsVectorLayer) -> Tuple[int, int]:
        name = self._layer_name(layer)
        if name in self.layer_zooms:
            low, high = self.layer_zooms[name]
        else:
            low, high = self._parse_layer_zoom(layer, "o"), self._parse_layer_zoom(layer, "i")
        low = max(0, min(_MVT_MAX_ZOOM, int(low)))
        return low, max(low, min(_MVT_MAX_ZOOM, int(high)))

    def _parse_layer_zoom(self, layer: QgsVectorLayer, marker: str) -> int:
        """Legacy fallback: zoom level encoded in the dataset filename."""
        match = re.search(f"{marker}(\\d+)", self._layer_name(layer))
        return int(match.group(1)) if match else 0

    def _get_global_min_zoom(self) -> int:
        return min((self._layer_zoom_range(layer)[0] for layer in self.layers), default=0)

    def _get_global_max_zoom(self) -> int:
        return max((self._layer_zoom_range(layer)[1] for layer in self.layers), default=14)


def _tile_layers(data: bytes) -> bytes:
    """An MVT tile's bytes (gzip or raw) without compression."""
    return zlib.decompress(data, 47) if data[:2] == b"\x1f\x8b" else data


def _tile_rows(path: str):
    """A part's tiles in (zoom, column, row) order (its UNIQUE index)."""
    part = sqlite3.connect(path)
    try:
        yield from part.execute("SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles "
                                "ORDER BY zoom_level, tile_column, tile_row")
    finally:
        part.close()


def _merged_tile(chunks: List[bytes]) -> bytes:
    """One tile of the parts' tiles at the same address (their layers, in
    the parts' order); a tile of one part as it is when already gzipped."""
    if len(chunks) == 1 and chunks[0][:2] == b"\x1f\x8b":
        return chunks[0]
    return _gzip(b"".join(_tile_layers(data) for data in chunks))


def merge_mbtiles(parts: List[str], output: str, min_zoom: int, max_zoom: int,
                  workers: int = 1) -> None:
    """One MBTiles of several MVT MBTiles with distinct layers: a tile's
    layers are concatenated (an MVT tile is a list of layer messages, so the
    concatenation of tiles is a tile with all their layers) and gzipped.

    The parts are read side by side in tile order (a few tiles in memory at
    a time, not all of them), and the tiles of several parts are gzipped
    ``workers`` at a time (zlib runs outside the GIL)."""
    if os.path.exists(output):
        os.remove(output)
    vector_layers, stats, bounds = [], [], None
    with sqlite3.connect(output) as out:
        out.execute("CREATE TABLE metadata (name text, value text)")
        out.execute("CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, "
                    "tile_data blob, UNIQUE (zoom_level, tile_column, tile_row))")
        if len(parts) == 1:  # nothing to merge: copy as is
            out.execute("ATTACH DATABASE ? AS part", (parts[0],))
            out.execute("INSERT INTO tiles SELECT zoom_level, tile_column, tile_row, tile_data FROM part.tiles")
            out.execute("INSERT INTO metadata SELECT name, value FROM part.metadata")
            out.commit()
            out.execute("DETACH DATABASE part")
            return
        for path in parts:
            with sqlite3.connect(path) as part:
                meta = dict(part.execute("SELECT name, value FROM metadata"))
            try:
                info = json.loads(meta.get("json") or "{}")
            except ValueError:
                info = {}
            vector_layers += info.get("vector_layers") or []
            stats += (info.get("tilestats") or {}).get("layers") or []
            try:
                west, south, east, north = (float(v) for v in meta["bounds"].split(","))
                bounds = [west, south, east, north] if bounds is None else [
                    min(bounds[0], west), min(bounds[1], south), max(bounds[2], east), max(bounds[3], north)]
            except (KeyError, ValueError):
                pass
        # heapq.merge is stable: tiles at the same address come in the parts' order.
        rows = heapq.merge(*(_tile_rows(path) for path in parts), key=lambda row: row[:3])
        tiles = ((address, [row[3] for row in group])
                 for address, group in itertools.groupby(rows, key=lambda row: row[:3]))
        with ThreadPoolExecutor(max(1, workers)) as pool:
            while True:
                batch = list(itertools.islice(tiles, 256))
                if not batch:
                    break
                data = pool.map(_merged_tile, [chunks for _, chunks in batch])
                out.executemany("INSERT INTO tiles VALUES (?, ?, ?, ?)",
                                ((z, x, y, blob) for ((z, x, y), _), blob in zip(batch, data)))
        vector_layers, stats = _unique_layers(vector_layers, "id"), _unique_layers(stats, "layer")
        bounds = bounds or [-180.0, -85.0511, 180.0, 85.0511]
        center = [(bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2, min_zoom]
        metadata = {
            "name": "tiles", "description": "", "version": "2", "minzoom": str(min_zoom),
            "maxzoom": str(max_zoom), "center": ",".join(f"{v:.7g}" if i < 2 else str(v)
                                                          for i, v in enumerate(center)),
            "bounds": ",".join(f"{v:.7f}" for v in bounds), "type": "overlay", "format": "pbf",
            "scheme": "tms",
            "json": json.dumps({"vector_layers": vector_layers,
                                "tilestats": {"layerCount": len(stats), "layers": stats}},
                               ensure_ascii=False),
        }
        out.executemany("INSERT INTO metadata VALUES (?, ?)", metadata.items())
        out.commit()


def _unique_layers(entries: List[dict], key: str) -> List[dict]:
    """Metadata entries of the same layer from several pieces (zoom bands,
    columns) as one: the widest zoom range, every field."""
    out: Dict[str, dict] = {}
    for entry in entries:
        name = entry.get(key) if isinstance(entry, dict) else None
        if name is None or name not in out:
            out[name if name is not None else f"#{len(out)}"] = dict(entry) if isinstance(entry, dict) else entry
            continue
        merged = out[name]
        for bound, pick in (("minzoom", min), ("maxzoom", max)):
            if bound in entry and bound in merged:
                merged[bound] = pick(merged[bound], entry[bound])
        if isinstance(entry.get("fields"), dict) and isinstance(merged.get("fields"), dict):
            merged["fields"] = dict(entry["fields"], **merged["fields"])
    return list(out.values())


def _gzip(data: bytes) -> bytes:
    compressor = zlib.compressobj(6, zlib.DEFLATED, 31)
    return compressor.compress(data) + compressor.flush()
