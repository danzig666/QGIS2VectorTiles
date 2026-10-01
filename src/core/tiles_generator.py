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
from os import cpu_count
from os.path import join, basename
from typing import Dict, List, Optional, Tuple
from osgeo import ogr
from qgis.core import QgsVectorLayer, QgsProcessingFeedback, QgsProcessingUtils

from ..utils import main_thread
from ..utils.config import _EPSG_CRS, _SIMPLIFICATION, _SIMPLIFICATION_MAX_ZOOM
from .fidelity.dependencies import prunable_fields, style_field_dependencies

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
    ):
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

    def _build_vrt(self, vrt_path: str):
        """Write an OGR VRT containing one entry per layer with per-zoom configuration."""
        root = ET.Element("OGRVRTDataSource")
        for layer in self.layers:
            name = self._layer_name(layer)
            min_zoom, max_zoom = self._layer_zoom_range(layer)
            node = ET.SubElement(root, "OGRVRTLayer", name=name)
            ET.SubElement(node, "SrcDataSource").text = layer.source().split("|layername=")[0]
            ET.SubElement(node, "LayerSRS").text = f"EPSG:{_EPSG_CRS}"
            ET.SubElement(node, "GeometryType").text = "wkbUnknown"
        ET.ElementTree(root).write(vrt_path, encoding="utf-8", xml_declaration=True)

    def _write_layer_conf(self, conf_path: str):
        """Per-layer zoom ranges for the MVT writer (``-dsco CONF``).

        OGR VRT has no layer-creation options, so zoom ranges put there are
        ignored and every dataset would be written at every zoom level.
        """
        conf = {}
        for layer in self.layers:
            min_zoom, max_zoom = self._layer_zoom_range(layer)
            conf[self._layer_name(layer)] = {"minzoom": int(min_zoom), "maxzoom": int(max_zoom)}
        with open(conf_path, "w", encoding="utf-8") as handle:
            json.dump(conf, handle)

    # --- ogr2ogr execution ---

    @staticmethod
    def _ogr2ogr_executable() -> str:
        """ogr2ogr from the QGIS/GDAL environment (PATH as set up by QGIS)."""
        return shutil.which("ogr2ogr") or "ogr2ogr"

    def _run_ogr2ogr(self, vrt_path: str, output: str, min_zoom: int, max_zoom: int,
                     conf_path: Optional[str] = None):
        """Execute ogr2ogr to convert the VRT to MBTiles."""
        cpu_num = str(max(1, int((cpu_count() or 1) * self.cpu_percent / 100)))
        env = os.environ.copy()
        env["GDAL_NUM_THREADS"] = cpu_num

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
