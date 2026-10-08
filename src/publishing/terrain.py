"""
Terrain for the web map's 3D view, hillshade and elevation profiles: a DEM
(a QGIS raster layer of heights in metres, band 1) resampled by GDAL to Web
Mercator tiles of 256 px and stored as "terrain-RGB" PNG images
(height = -10000 + (R * 65536 + G * 256 + B) * 0.1 m, MapLibre's
``raster-dem`` "mapbox" encoding) in their own PMTiles archive.

Each zoom is warped once over its whole tile-aligned area (bilinear) and cut
into tiles; holes and the area beyond the DEM take the nearest heights (GDAL
FillNodata), so the relief has no cliff at the DEM's edge; tiles without
any data are left out.
"""

import math
from typing import Optional, Tuple

from .errors import PublishingError
from .pmtiles_builder import ArchiveDescriptor, TileSink
from .progress import Progress
from .raster_tiles import ORIGIN, native_resolution, native_zoom, tile_span
from .vendor.pmtiles.tile import Compression, TileType

TILE = 256
MAX_PIXELS = 48_000_000      # one zoom's warped area (~190 MB as float32)
MAX_TERRAIN_ZOOM = 15
NODATA = -32768.0


def encode_heights(heights) -> bytes:
    """RGB bytes (row-major, 3 per pixel) of a height array in metres."""
    import numpy as np  # pylint: disable=import-outside-toplevel
    value = np.clip(np.round((np.asarray(heights, dtype=np.float64) + 10000.0) * 10.0), 0, 2 ** 24 - 1)
    value = value.astype(np.uint32)
    rgb = np.stack([(value >> 16) & 255, (value >> 8) & 255, value & 255], axis=-1).astype(np.uint8)
    return rgb.tobytes()


def decode_rgb(r: int, g: int, b: int) -> float:
    return -10000.0 + (r * 65536 + g * 256 + b) * 0.1


def _png(rgb: bytes, width: int, height: int) -> bytes:
    from qgis.PyQt.QtGui import QImage  # pylint: disable=import-outside-toplevel
    from .raster_tiles import _encode  # pylint: disable=import-outside-toplevel
    image = QImage(rgb, width, height, width * 3, QImage.Format.Format_RGB888).copy()
    return _encode(image, "PNG", 100)


def terrain_zooms(layer, profile_min_zoom: int, latitude: float) -> Tuple[int, int]:
    """(min, max) zoom of the terrain tiles: down to two levels below the
    map's first zoom, up to the DEM's own resolution (at most 15)."""
    native = native_resolution(layer)
    high = native_zoom(native, latitude) if native else 14
    high = max(0, min(MAX_TERRAIN_ZOOM, high))
    low = max(0, min(high, int(profile_min_zoom) - 2))
    return low, high


def render_terrain(layer, extent_3857, min_zoom: int, max_zoom: int, output: str,
                   progress: Optional[Progress] = None) -> Optional[ArchiveDescriptor]:
    """Terrain-RGB PMTiles of ``layer`` over ``extent_3857`` (x0, y0, x1, y1)."""
    from osgeo import gdal  # pylint: disable=import-outside-toplevel
    import numpy as np  # pylint: disable=import-outside-toplevel
    progress = progress or Progress()
    source = layer.source().split("|")[0]
    dataset = gdal.Open(source)
    if dataset is None:
        raise PublishingError("Q2VT_PUB_TERRAIN", f'Terrain: "{layer.name()}" cannot be read as a file '
                                                  "(online services cannot be used as terrain).")
    band = dataset.GetRasterBand(1)
    source_nodata = band.GetNoDataValue()
    dataset = None
    zooms = list(range(int(min_zoom), int(max_zoom) + 1))
    with TileSink(output) as sink:
        for step, z in enumerate(zooms):
            progress.check()
            x0, y0, x1, y1 = tile_span(extent_3857, z)
            nx, ny = x1 - x0 + 1, y1 - y0 + 1
            if nx <= 0 or ny <= 0:
                continue
            if nx * ny * TILE * TILE > MAX_PIXELS:
                progress.info(f"Terrain: zoom {z} and above left out (area too large)")
                break
            size = 2 * ORIGIN / (1 << z)
            bounds = (-ORIGIN + x0 * size, ORIGIN - (y1 + 1) * size, -ORIGIN + (x1 + 1) * size, ORIGIN - y0 * size)
            options = gdal.WarpOptions(format="MEM", outputBounds=bounds, width=nx * TILE, height=ny * TILE,
                                       dstSRS="EPSG:3857", resampleAlg="bilinear", outputType=gdal.GDT_Float32,
                                       dstNodata=NODATA, srcNodata=source_nodata, multithread=True)
            warped = gdal.Warp("", source, options=options)
            if warped is None:
                raise PublishingError("Q2VT_PUB_TERRAIN", f'Terrain: "{layer.name()}" could not be resampled.')
            band = warped.GetRasterBand(1)
            valid = np.isfinite(band.ReadAsArray()) & (band.ReadAsArray() > NODATA + 1)
            if not valid.any():
                continue
            # Holes and the area beyond the DEM take the nearest edge heights
            # (no cliff at the DEM's edge in the relief or a profile).
            gdal.FillNodata(targetBand=band, maskBand=None, maxSearchDist=max(nx, ny) * TILE,
                            smoothingIterations=0)
            heights = band.ReadAsArray().astype(np.float64)
            warped = None
            filled = np.isfinite(heights) & (heights > NODATA + 1)
            heights[~filled] = float(heights[filled].mean())
            for ty in range(ny):
                for tx in range(nx):
                    window = (slice(ty * TILE, (ty + 1) * TILE), slice(tx * TILE, (tx + 1) * TILE))
                    if not valid[window].any():
                        continue
                    sink.add(z, x0 + tx, y0 + ty, _png(encode_heights(heights[window]), TILE, TILE))
            progress.update(0.9 * (step + 1) / len(zooms), f"Terrain: zoom {z}, {sink.count} tiles")
        if not sink.count:
            return None
        metadata = {"name": layer.name(), "format": "png", "type": "baselayer",
                    "description": "Terrain-RGB heights (mapbox encoding) by QWebMap", "encoding": "mapbox"}
        return sink.write(TileType.PNG, Compression.NONE, metadata, progress=progress.sub(0.9, 1.0))


def grow(extent_3857, fraction: float = 0.2):
    """The extent grown by ``fraction`` of its size on every side (the 3D
    view looks beyond the extent towards the horizon)."""
    x0, y0, x1, y1 = extent_3857
    dx, dy = (x1 - x0) * fraction, (y1 - y0) * fraction
    return (max(-ORIGIN, x0 - dx), max(-ORIGIN, y0 - dy), min(ORIGIN, x1 + dx), min(ORIGIN, y1 + dy))


def latitude_of(extent_3857) -> float:
    y = (extent_3857[1] + extent_3857[3]) / 2
    return math.degrees(2 * math.atan(math.exp(y / 6378137.0)) - math.pi / 2)
