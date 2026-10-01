"""
QGIS raster layers -> their own raster PMTiles archive (PNG / JPEG / WebP).

Vector layers are never rasterized: the map data stays MVT. A *raster*
layer of the project (an orthophoto, a scanned plan, a DEM rendered with its
QGIS colour ramp, a WMS/XYZ layer) is rendered by QGIS itself, exactly as on
the canvas (its renderer, resampling, layer opacity and scale range), into
Web Mercator tiles packed into ``data/raster-<id>.pmtiles``. The viewer's
opacity slider multiplies that.

* Rendering runs on QGIS's main thread in metatiles (``RenderMapTile``
  flag, no seams), one layer at a time; cancellation is checked per
  metatile.
* Fully transparent tiles are not stored (the viewer shows nothing there).
* Identical tiles are stored once; the archive is validated (structure,
  image signatures) before it is renamed into place.
"""

import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsMapRendererCustomPainterJob,
                       QgsMapSettings, QgsRasterLayer, QgsRectangle)
from qgis.PyQt.QtCore import QBuffer, QByteArray, QIODevice, QRect, QSize
from qgis.PyQt.QtGui import QColor, QImage, QImageWriter, QPainter

from .errors import PublishingError
from .models import LayerConfig, PublicationProfile
from .pmtiles_builder import IMAGE_TILE_TYPES, ArchiveDescriptor, TileSink
from .progress import Progress
from .vendor.pmtiles.tile import Compression

ORIGIN = 20037508.342789244          # half the Web Mercator world width (m)
WEB_MERCATOR = "EPSG:3857"
MAX_TILES_PER_LAYER = 400_000        # refuse runaway exports (estimate before rendering)
ONLINE_PROVIDERS = {"wms", "arcgismapserver", "arcgisfeatureserver", "xyz", "wcs"}


@dataclass
class RasterPlan:
    layer_id: str
    min_zoom: int
    max_zoom: int
    tiles: int                        # upper bound (tiles touching the area)
    extent_3857: Tuple[float, float, float, float]
    online: bool = False
    warnings: List[str] = field(default_factory=list)


def is_raster(layer) -> bool:
    return isinstance(layer, QgsRasterLayer)


def zoom_range(config: LayerConfig, profile: PublicationProfile) -> Tuple[int, int]:
    low = profile.view.min_zoom if config.raster_min_zoom is None else config.raster_min_zoom
    high = profile.view.max_zoom if config.raster_max_zoom is None else config.raster_max_zoom
    return int(low), int(max(low, high))


def tile_span(extent: Tuple[float, float, float, float], z: int) -> Tuple[int, int, int, int]:
    """Inclusive XYZ tile range (x0, y0, x1, y1) covering a 3857 extent."""
    size = 2 * ORIGIN / (1 << z)
    n = (1 << z) - 1
    x0 = int(math.floor((extent[0] + ORIGIN) / size))
    x1 = int(math.floor((extent[2] + ORIGIN) / size - 1e-9))
    y0 = int(math.floor((ORIGIN - extent[3]) / size))
    y1 = int(math.floor((ORIGIN - extent[1]) / size - 1e-9))
    return max(0, x0), max(0, y0), min(n, x1), min(n, y1)


def count_tiles(extent, min_zoom: int, max_zoom: int) -> int:
    total = 0
    for z in range(min_zoom, max_zoom + 1):
        x0, y0, x1, y1 = tile_span(extent, z)
        total += max(0, x1 - x0 + 1) * max(0, y1 - y0 + 1)
    return total


def plan_layer(project, layer, config: LayerConfig, profile: PublicationProfile,
               extent_3857: QgsRectangle) -> RasterPlan:
    """Zooms, area (export extent ∩ layer extent) and an upper bound of tiles."""
    low, high = zoom_range(config, profile)
    web = QgsCoordinateReferenceSystem(WEB_MERCATOR)
    area = QgsRectangle(extent_3857)
    warnings = []
    try:
        to_web = QgsCoordinateTransform(layer.crs(), web, project.transformContext())
        layer_extent = to_web.transformBoundingBox(layer.extent())
        if layer_extent.isFinite() and not layer_extent.isEmpty():
            area = area.intersect(layer_extent)
    except Exception:  # noqa: BLE001 - provider without a usable extent (e.g. XYZ): the export extent
        pass
    online = (layer.providerType() or "").lower() in ONLINE_PROVIDERS
    if online:
        warnings.append(f'"{layer.name()}" is an online map service: check that its licence allows '
                        "republishing its images.")
    box = (area.xMinimum(), area.yMinimum(), area.xMaximum(), area.yMaximum())
    tiles = 0 if area.isEmpty() else count_tiles(box, low, high)
    return RasterPlan(layer.id(), low, high, tiles, box, online, warnings)


def _encoder(fmt: str) -> str:
    supported = {bytes(f).decode().lower() for f in QImageWriter.supportedImageFormats()}
    if fmt == "webp" and "webp" not in supported:
        raise PublishingError("Q2VT_PUB_DEPENDENCY",
                              "This QGIS cannot write WebP images; choose PNG or JPEG.")
    return {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}[fmt]


def _encode(image: QImage, writer_format: str, quality: int) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, writer_format, -1 if writer_format == "PNG" else int(quality)):
        raise PublishingError("Q2VT_PUB_BUNDLE_INVALID", f"Could not encode a {writer_format} tile.")
    buffer.close()
    return bytes(data)


def _transparent(image: QImage) -> bool:
    """True when every pixel's alpha is 0 (nothing to show)."""
    import numpy as np  # pylint: disable=import-outside-toplevel
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    ptr = image.constBits()
    size = image.sizeInBytes() if hasattr(image, "sizeInBytes") else image.byteCount()
    ptr.setsize(size)
    pixels = np.frombuffer(ptr, dtype=np.uint8).reshape(image.height(), image.bytesPerLine())
    alpha = pixels[:, 3:image.width() * 4:4]  # ARGB32 little endian: B G R A
    return not alpha.any()


def render_layer(project, layer, config: LayerConfig, plan: RasterPlan, output: str,
                 progress: Optional[Progress] = None, metatile: int = 8,
                 title: str = "") -> Optional[ArchiveDescriptor]:
    """Render ``layer`` into ``output`` (PMTiles). Returns None when the layer
    draws nothing in the export extent."""
    progress = progress or Progress()
    if plan.tiles > MAX_TILES_PER_LAYER:
        raise PublishingError(
            "Q2VT_PUB_PROFILE_INVALID",
            f'Raster layer "{layer.name()}": about {plan.tiles} tiles at zooms '
            f"{plan.min_zoom}-{plan.max_zoom}; lower its maximum zoom or the extent "
            f"(limit {MAX_TILES_PER_LAYER}).")
    if plan.tiles == 0:
        return None
    writer_format = _encoder(config.raster_format)
    tile_px = 512 if config.raster_hidpi else 256
    settings = QgsMapSettings()
    settings.setLayers([layer])
    settings.setDestinationCrs(QgsCoordinateReferenceSystem(WEB_MERCATOR))
    settings.setTransformContext(project.transformContext())
    settings.setBackgroundColor(QColor(0, 0, 0, 0))
    settings.setOutputDpi(96.0 * tile_px / 256)
    settings.setFlag(QgsMapSettings.Flag.Antialiasing, True)
    settings.setFlag(QgsMapSettings.Flag.RenderMapTile, True)
    settings.setFlag(QgsMapSettings.Flag.DrawLabeling, False)
    jpeg = config.raster_format == "jpeg"
    done = 0
    with TileSink(output) as sink:
        for z in range(plan.min_zoom, plan.max_zoom + 1):
            x0, y0, x1, y1 = tile_span(plan.extent_3857, z)
            size = 2 * ORIGIN / (1 << z)
            for mx in range(x0, x1 + 1, metatile):
                for my in range(y0, y1 + 1, metatile):
                    nx, ny = min(metatile, x1 - mx + 1), min(metatile, y1 - my + 1)
                    progress.check()
                    rect = QgsRectangle(-ORIGIN + mx * size, ORIGIN - (my + ny) * size,
                                        -ORIGIN + (mx + nx) * size, ORIGIN - my * size)
                    settings.setExtent(rect)
                    settings.setOutputSize(QSize(nx * tile_px, ny * tile_px))
                    image = QImage(nx * tile_px, ny * tile_px, QImage.Format.Format_ARGB32_Premultiplied)
                    image.fill(QColor(0, 0, 0, 0))
                    painter = QPainter(image)
                    job = QgsMapRendererCustomPainterJob(settings, painter)
                    job.renderSynchronously()
                    painter.end()
                    for ix in range(nx):
                        for iy in range(ny):
                            tile = image.copy(QRect(ix * tile_px, iy * tile_px, tile_px, tile_px))
                            if _transparent(tile):
                                continue
                            if jpeg:  # no alpha: composite on white
                                flat = QImage(tile.size(), QImage.Format.Format_RGB32)
                                flat.fill(QColor(255, 255, 255))
                                p = QPainter(flat)
                                p.drawImage(0, 0, tile)
                                p.end()
                                tile = flat
                            sink.add(z, mx + ix, my + iy, _encode(tile, writer_format, config.raster_quality))
                    done += nx * ny
                    progress.update(0.9 * done / max(1, plan.tiles),
                                    f'Raster "{layer.name()}": {done}/{plan.tiles} tiles')
        if not sink.count:
            return None
        metadata = {"name": title or layer.name(), "format": config.raster_format,
                    "type": "overlay", "description": "QGIS raster layer rendered by QGIS2VectorTiles"}
        return sink.write(IMAGE_TILE_TYPES[config.raster_format], Compression.NONE, metadata,
                          progress=progress.sub(0.9, 1.0))


def raster_source_id(logical_id: str) -> str:
    return f"q2vt_raster_{logical_id.replace('-', '_')}"


def raster_style_layer_id(logical_id: str) -> str:
    return f"q2vt-raster-{logical_id}"
