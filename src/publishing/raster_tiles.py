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
* Layer blend modes (the browser has none): a *multiply* layer becomes black
  with transparency 1 - brightness, a *screen* layer white with transparency
  = brightness - exact for grey rasters such as a hillshade, whatever is
  drawn below. Colour pixels use their luminance (reported as approximate).
"""

import math
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsMapRendererParallelJob,
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
    # Not rendered where the layer is hidden on the web (its scale range).
    from .qgis_model import _zoom_of_scale, layer_scale_range  # pylint: disable=import-outside-toplevel
    out_scale, in_scale = layer_scale_range(layer, config)
    if out_scale and _zoom_of_scale(out_scale) is not None:
        low = min(high, max(low, int(math.floor(_zoom_of_scale(out_scale)))))
    if in_scale and _zoom_of_scale(in_scale) is not None:
        high = max(low, min(high, int(math.ceil(_zoom_of_scale(in_scale)))))
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
    return {"png": "PNG", "jpeg": "JPEG", "webp": "WEBP"}[fmt]


def tile_format(fmt: str) -> str:
    """The image format the tiles are written in: WebP (the default) falls
    back to PNG, also transparent, where this QGIS cannot write WebP."""
    if fmt == "webp":
        supported = {bytes(f).decode().lower() for f in QImageWriter.supportedImageFormats()}
        if "webp" not in supported:
            return "png"
    return fmt


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
                 title: str = "", blend: str = "normal", threads: int = 1) -> Optional[ArchiveDescriptor]:
    """Render ``layer`` into ``output`` (PMTiles). Returns None when the layer
    draws nothing in the export extent. ``blend`` "multiply" / "screen":
    the tiles are converted to the equivalent normal image (blend_to_alpha);
    a colour (not grey) layer is then approximate (added to plan.warnings).

    ``threads``: metatiles rendered at once (QGIS map render jobs, as QGIS's
    own XYZ tile tool runs them, started and collected on this thread) and
    tiles cut and encoded at once (Qt releases the GIL while encoding).
    Results are written in a fixed order: the archive is the same for any
    number of threads."""
    progress = progress or Progress()
    if plan.tiles > MAX_TILES_PER_LAYER:
        raise PublishingError(
            "Q2VT_PUB_PROFILE_INVALID",
            f'Raster layer "{layer.name()}": about {plan.tiles} tiles at zooms '
            f"{plan.min_zoom}-{plan.max_zoom}; lower its maximum zoom or the extent "
            f"(limit {MAX_TILES_PER_LAYER}).")
    if plan.tiles == 0:
        return None
    from collections import deque  # pylint: disable=import-outside-toplevel
    from concurrent.futures import ThreadPoolExecutor  # pylint: disable=import-outside-toplevel
    fmt = tile_format(config.raster_format)
    if fmt != config.raster_format:
        plan.warnings.append(f'Raster layer "{layer.name()}": this QGIS cannot write WebP images, '
                             "so its tiles are PNG (larger).")
    writer_format = _encoder(fmt)
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
    jpeg = fmt == "jpeg"
    if blend in ("multiply", "screen") and jpeg:
        plan.warnings.append(f'Raster layer "{layer.name()}": its {blend} blend mode needs transparency; '
                             "JPEG has none, so it is drawn as normal. Choose PNG or WebP.")
        blend = "normal"
    threads = max(1, int(threads))

    def cut(image: QImage, z: int, mx: int, my: int, nx: int, ny: int):
        """Worker: the metatile's non-empty tiles, encoded."""
        grey = True
        if blend in ("multiply", "screen"):
            image, grey = blend_to_alpha(image, blend)
        tiles = []
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
                tiles.append((z, mx + ix, my + iy, _encode(tile, writer_format, config.raster_quality)))
        return tiles, grey, nx * ny

    def metatiles():
        for z in range(plan.min_zoom, plan.max_zoom + 1):
            x0, y0, x1, y1 = tile_span(plan.extent_3857, z)
            size = 2 * ORIGIN / (1 << z)
            for mx in range(x0, x1 + 1, metatile):
                for my in range(y0, y1 + 1, metatile):
                    nx, ny = min(metatile, x1 - mx + 1), min(metatile, y1 - my + 1)
                    rect = QgsRectangle(-ORIGIN + mx * size, ORIGIN - (my + ny) * size,
                                        -ORIGIN + (mx + nx) * size, ORIGIN - my * size)
                    yield z, mx, my, nx, ny, rect

    colour_blend = False
    done = 0
    rendering = deque()  # (job, z, mx, my, nx, ny), in start order
    cutting = deque()    # futures of cut(), in start order
    with TileSink(output) as sink, ThreadPoolExecutor(max_workers=threads,
                                                      thread_name_prefix="q2vt-raster") as pool:
        def write_ready(block: bool) -> None:
            nonlocal colour_blend, done
            while cutting and (block or cutting[0].done()):
                tiles, grey, count = cutting.popleft().result()
                for tile in tiles:
                    sink.add(*tile)
                colour_blend = colour_blend or not grey
                done += count
                progress.update(0.9 * done / max(1, plan.tiles),
                                f'Raster "{layer.name()}": {done}/{plan.tiles} tiles')

        def finish_oldest() -> None:
            job, z, mx, my, nx, ny = rendering.popleft()
            job.waitForFinished()
            cutting.append(pool.submit(cut, job.renderedImage(), z, mx, my, nx, ny))
            write_ready(len(cutting) > 2 * threads)

        try:
            for z, mx, my, nx, ny, rect in metatiles():
                progress.check()
                job_settings = QgsMapSettings(settings)
                job_settings.setExtent(rect)
                job_settings.setOutputSize(QSize(nx * tile_px, ny * tile_px))
                job = QgsMapRendererParallelJob(job_settings)
                job.start()
                rendering.append((job, z, mx, my, nx, ny))
                while len(rendering) >= threads:
                    finish_oldest()
            while rendering:
                finish_oldest()
            write_ready(True)
        finally:
            for job, *_ in rendering:  # cancelled or failed: stop the renders still running
                job.cancel()
        if colour_blend:
            plan.warnings.append(f'Raster layer "{layer.name()}": {blend} blend mode with colours is '
                                 "approximated in the web map (by brightness); grey layers such as a "
                                 "hillshade are exact.")
        if not sink.count:
            return None
        metadata = {"name": title or layer.name(), "format": fmt,
                    "type": "overlay", "description": "QGIS raster layer rendered by QWebMap"}
        return sink.write(IMAGE_TILE_TYPES[fmt], Compression.NONE, metadata,
                          progress=progress.sub(0.9, 1.0))


# QPainter composition modes (QgsMapLayer.blendMode) the browser can reproduce.
BLEND_NAMES = {0: "normal", 13: "multiply", 14: "screen", 15: "overlay", 16: "darken", 17: "lighten",
               18: "dodge", 19: "burn", 20: "hard light", 21: "soft light", 22: "difference",
               23: "exclusion", 12: "addition"}


def blend_name(layer) -> str:
    """The layer's blend mode name ("normal", "multiply", ...)."""
    try:
        mode = int(getattr(layer.blendMode(), "value", layer.blendMode()))
    except (AttributeError, TypeError, ValueError):
        return "normal"
    return BLEND_NAMES.get(mode, f"mode {mode}")


def blend_to_alpha(image: QImage, blend: str) -> Tuple[QImage, bool]:
    """A rendered multiply/screen layer as a normal (alpha) image with the
    same result: multiply by grey g = black with alpha 1 - g; screen by g =
    white with alpha g (both times the pixel's own alpha). Returns the image
    and whether every visible pixel was grey (else luminance: approximate)."""
    import numpy as np  # pylint: disable=import-outside-toplevel
    argb = image.convertToFormat(QImage.Format.Format_ARGB32)
    width, height = argb.width(), argb.height()
    ptr = argb.constBits()
    ptr.setsize(argb.sizeInBytes() if hasattr(argb, "sizeInBytes") else argb.byteCount())
    rows = np.frombuffer(ptr, dtype=np.uint8).reshape(height, argb.bytesPerLine())
    pixels = rows[:, :width * 4].reshape(height, width, 4).astype(np.float32)  # B G R A
    b, g, r, a = pixels[..., 0], pixels[..., 1], pixels[..., 2], pixels[..., 3]
    visible = a > 0
    grey = not visible.any() or float(np.max(np.maximum(np.abs(r - g), np.abs(g - b))[visible])) <= 2.0
    level = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    alpha = a * ((1.0 - level) if blend == "multiply" else level)
    out = np.empty((height, width, 4), dtype=np.uint8)
    out[..., :3] = 0 if blend == "multiply" else 255
    out[..., 3] = np.clip(np.rint(alpha), 0, 255).astype(np.uint8)
    result = QImage(out.tobytes(), width, height, width * 4, QImage.Format.Format_ARGB32).copy()
    return result, grey


def raster_source_id(logical_id: str) -> str:
    return f"q2vt_raster_{logical_id.replace('-', '_')}"


def raster_style_layer_id(logical_id: str) -> str:
    return f"q2vt-raster-{logical_id}"


# -- export cache ---------------------------------------------------------------
# Files next to a raster that change how GDAL / QGIS read or draw it.
_RASTER_SIDECARS = (".aux.xml", ".ovr", ".msk", ".tfw", ".tifw", ".wld", ".jgw", ".pgw", ".prj")


def _raster_files(layer) -> Optional[List[str]]:
    """The local files of a GDAL raster layer (with its sidecars), or None
    when it is not a plain local file (web, database, virtual sources)."""
    if layer.providerType() != "gdal":
        return None
    path = layer.source().split("|")[0]
    if not path or not os.path.isfile(path):
        return None
    files = [path]
    stem = os.path.splitext(path)[0]
    for side in _RASTER_SIDECARS:
        for candidate in (path + side, stem + side):
            if os.path.isfile(candidate):
                files.append(candidate)
    return sorted(set(files))


def raster_cache_key(project, layer, config: LayerConfig, plan: RasterPlan, blend: str) -> Optional[str]:
    """Content key of a rendered raster archive: the source files (size and
    modification time), the layer's whole QGIS style (renderer, resampling,
    brightness/contrast, opacity...), CRS and datum transformations, the
    tiles (extent, zooms) and the image settings; the plugin's raster code and
    QGIS/GDAL versions. None: not cacheable (online, database or virtual
    sources)."""
    from qgis.core import QgsMapLayerStyle  # pylint: disable=import-outside-toplevel
    from ..core import export_cache  # pylint: disable=import-outside-toplevel
    files = _raster_files(layer)
    if files is None:
        return None
    sources = []
    for path in files:
        stat = os.stat(path)
        sources.append([os.path.abspath(path), stat.st_size, stat.st_mtime_ns])
    style = QgsMapLayerStyle()
    style.readFromLayer(layer)
    here = os.path.dirname(os.path.abspath(__file__))
    code = []
    for name in ("raster_tiles.py", "pmtiles_builder.py"):
        with open(os.path.join(here, name), "rb") as handle:
            code.append(export_cache.hashlib.sha256(handle.read()).hexdigest())
    try:
        operations = sorted(project.transformContext().coordinateOperations().items())
    except AttributeError:
        operations = []
    return export_cache.make_key(
        "raster", code, sources, style.xmlData(), layer.crs().toWkt(), operations,
        [plan.min_zoom, plan.max_zoom, list(plan.extent_3857)],
        [tile_format(config.raster_format), int(config.raster_quality), bool(config.raster_hidpi)],
        config.title or layer.name(), blend)


def descriptor_to_dict(descriptor: ArchiveDescriptor) -> dict:
    data = dict(descriptor.__dict__)
    data.pop("path", None)
    data["zoom_counts"] = {str(k): v for k, v in descriptor.zoom_counts.items()}
    return data


def descriptor_from_dict(data: dict, path: str) -> ArchiveDescriptor:
    data = dict(data)
    for name in ("bounds", "center"):
        if name in data:
            data[name] = tuple(data[name])
    data["zoom_counts"] = {int(k): v for k, v in data.get("zoom_counts", {}).items()}
    return ArchiveDescriptor(path=path, **data)
