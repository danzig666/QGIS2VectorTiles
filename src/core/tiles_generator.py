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
from os import cpu_count
from os.path import join, basename
from typing import Dict, List, Optional, Tuple
from osgeo import ogr
from qgis.core import QgsVectorLayer, QgsProcessingFeedback, QgsProcessingUtils

from ..utils import main_thread
from ..utils.config import _EPSG_CRS, _SIMPLIFICATION, _SIMPLIFICATION_MAX_ZOOM
from .fidelity.dependencies import prunable_fields, style_field_dependencies
from . import export_cache

_MVT_MAX_ZOOM = 22


class TilesGenerationCancelled(RuntimeError):
    """Raised when the user cancels while ogr2ogr is running."""


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

    def generate(self) -> Tuple[str, int]:
        """Build VRT, run ogr2ogr, return (mbtiles URI, min_zoom)."""
        self.remove_unused_fields()
        output, uri = self._prepare_output_paths()
        vrt_path = join(QgsProcessingUtils.tempFolder(), "layers.vrt")

        min_zoom = self._get_global_min_zoom()
        max_zoom = self._get_global_max_zoom()

        if self.cache is not None:
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
            required = dependencies.get(self._layer_name(layer), set())
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
            ds = None  # Flush changes and close the dataset

    # --- VRT construction ---

    def _build_vrt(self, vrt_path: str, layers: Optional[List[QgsVectorLayer]] = None):
        """Write an OGR VRT containing one entry per layer with per-zoom configuration."""
        root = ET.Element("OGRVRTDataSource")
        for layer in self.layers if layers is None else layers:
            name = self._layer_name(layer)
            min_zoom, max_zoom = self._layer_zoom_range(layer)
            node = ET.SubElement(root, "OGRVRTLayer", name=name)
            ET.SubElement(node, "SrcDataSource").text = layer.source().split("|layername=")[0]
            ET.SubElement(node, "LayerSRS").text = f"EPSG:{_EPSG_CRS}"
            ET.SubElement(node, "GeometryType").text = "wkbUnknown"
        ET.ElementTree(root).write(vrt_path, encoding="utf-8", xml_declaration=True)

    def _write_layer_conf(self, conf_path: str, layers: Optional[List[QgsVectorLayer]] = None):
        """Per-layer zoom ranges for the MVT writer (``-dsco CONF``).

        OGR VRT has no layer-creation options, so zoom ranges put there are
        ignored and every dataset would be written at every zoom level.
        """
        conf = {}
        for layer in self.layers if layers is None else layers:
            min_zoom, max_zoom = self._layer_zoom_range(layer)
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
                         conf_path: Optional[str] = None) -> List[str]:
        cmd = [
            self._ogr2ogr_executable(), "-f", "MBTiles", output, vrt_path,
            "-dsco", f"MINZOOM={min_zoom}",
            "-dsco", f"MAXZOOM={max_zoom}",
            "-t_srs", f"EPSG:{_EPSG_CRS}",
            "-dsco", "MAX_SIZE=5000000",
            "-dsco", "MAX_FEATURES=2000000",
            "-dsco", f"SIMPLIFICATION={_SIMPLIFICATION}",
            "-dsco", f"SIMPLIFICATION_MAX_ZOOM={_SIMPLIFICATION_MAX_ZOOM}",
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

        with subprocess.Popen(
            cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            startupinfo=startupinfo, creationflags=creationflags,
        ) as proc:
            started = last_message = time.monotonic()
            while proc.poll() is None:
                if self.feedback is not None and time.monotonic() - last_message > 15:
                    last_message = time.monotonic()
                    self.feedback.pushInfo(
                        f"   Still generating tiles ({(last_message - started) / 60:.1f} minutes)...")
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

    def _dataset_fields(self, layer: QgsVectorLayer) -> List[str]:
        ds = ogr.Open(layer.source().split("|layername=")[0])
        if ds is None:
            return []
        defn = ds.GetLayer(0).GetLayerDefn()
        names = [defn.GetFieldDefn(i).GetName() for i in range(defn.GetFieldCount())]
        ds = None
        return names

    def _tile_groups(self) -> List[Tuple[Optional[str], List[QgsVectorLayer]]]:
        """[(cache key or None, datasets)] — one group per QGIS layer, in the
        order of the datasets (the order of one combined export)."""
        by_name = {self._layer_name(layer): layer for layer in self.layers}
        owner = {name: lid for lid, names in self.layer_groups.items() for name in names}
        groups: Dict[str, List[QgsVectorLayer]] = {}
        for name, layer in by_name.items():
            groups.setdefault(owner.get(name, f"dataset:{name}"), []).append(layer)
        out = []
        for members in groups.values():
            parts = []
            for layer in members:
                name = self._layer_name(layer)
                key = self.dataset_keys.get(name)
                if key is None:
                    parts = None
                    break
                parts.append([name, key, list(self._layer_zoom_range(layer)),
                              self._dataset_fields(layer)])
            out.append((export_cache.make_key("tiles", self._zooms, parts,
                                              _SIMPLIFICATION, _SIMPLIFICATION_MAX_ZOOM)
                        if parts is not None else None, members))
        return out

    def _generate_per_layer(self, output: str, min_zoom: int, max_zoom: int):
        """Tiles of each QGIS layer's datasets (from the cache when unchanged),
        merged into ``output``."""
        self._zooms = [min_zoom, max_zoom]
        work = join(self.output_dir, "layer_tiles")
        os.makedirs(work, exist_ok=True)
        parts, jobs = [], []
        for number, (key, members) in enumerate(self._tile_groups()):
            cached = self.cache.get_tiles(key) if key else None
            if cached:
                parts.append(cached)
                continue
            target = join(work, f"group_{number:04d}.mbtiles")
            vrt = join(work, f"group_{number:04d}.vrt")
            conf = join(work, f"group_{number:04d}.json")
            self._build_vrt(vrt, members)
            self._write_layer_conf(conf, members)
            jobs.append((key, target, self._ogr2ogr_command(vrt, target, min_zoom, max_zoom, conf)))
            parts.append(target)
        if self.feedback is not None:
            self.feedback.pushInfo(f"   Tiles: {len(parts) - len(jobs)} of {len(parts)} layers "
                                   "reused from earlier exports, " f"{len(jobs)} to generate")
        self._run_parallel([cmd for _, _, cmd in jobs])
        stored = {target: self.cache.put_tiles(key, target) for key, target, _ in jobs if key}
        merge_mbtiles([stored.get(path, path) for path in parts], output, min_zoom, max_zoom)
        shutil.rmtree(work, ignore_errors=True)  # kept in the cache; not needed here

    def _run_parallel(self, commands: List[List[str]]):
        """Run ogr2ogr commands, several at a time, polled from this (main)
        thread so QGIS stays responsive and Cancel works."""
        if not commands:
            return
        cpu = self._cpu_num()
        slots = max(1, min(len(commands), cpu))
        env = os.environ.copy()
        env["GDAL_NUM_THREADS"] = str(max(1, cpu // slots))
        waiting, running = list(commands), []
        started = last_message = time.monotonic()
        try:
            while waiting or running:
                while waiting and len(running) < slots:
                    running.append(subprocess.Popen(
                        waiting.pop(0), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                        text=True, **self._popen_options()))
                for proc in [p for p in running if p.poll() is not None]:
                    running.remove(proc)
                    _, stderr = proc.communicate()
                    if proc.returncode != 0:
                        error_msg = f"ogr2ogr failed.\nError: {stderr}"
                        if self.feedback:
                            self.feedback.reportError(error_msg)
                        raise RuntimeError(error_msg)
                if self.feedback is not None and self.feedback.isCanceled():
                    raise TilesGenerationCancelled("Tile generation cancelled")
                if self.feedback is not None and time.monotonic() - last_message > 15:
                    last_message = time.monotonic()
                    self.feedback.pushInfo(
                        f"   Still generating tiles ({(last_message - started) / 60:.1f} minutes, "
                        f"{len(waiting) + len(running)} layers left)...")
                time.sleep(0.05)
                main_thread.keep_responsive()
        finally:
            for proc in running:
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


def merge_mbtiles(parts: List[str], output: str, min_zoom: int, max_zoom: int) -> None:
    """One MBTiles of several MVT MBTiles with distinct layers: a tile's
    layers are concatenated (an MVT tile is a list of layer messages, so the
    concatenation of tiles is a tile with all their layers) and gzipped."""
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
        merged: Dict[Tuple[int, int, int], List[bytes]] = {}
        for path in parts:
            with sqlite3.connect(path) as part:
                for z, x, y, data in part.execute(
                        "SELECT zoom_level, tile_column, tile_row, tile_data FROM tiles"):
                    merged.setdefault((z, x, y), []).append(_tile_layers(data))
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
        out.executemany("INSERT INTO tiles VALUES (?, ?, ?, ?)", (
            (z, x, y, _gzip(b"".join(chunks))) for (z, x, y), chunks in sorted(merged.items())))
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


def _gzip(data: bytes) -> bytes:
    compressor = zlib.compressobj(6, zlib.DEFLATED, 31)
    return compressor.compress(data) + compressor.flush()
