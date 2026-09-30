"""
zoom.py

Conversions between QGIS map scales and MapLibre zoom levels.

The exporter's scale ↔ zoom convention is ``scale(z) = TOP_SCALE / 2**z``
(``TOP_SCALE`` is the historical ``_TOP_SCALE`` of this plugin). Fractional
zooms are preserved so that scale breakpoints between integer zooms survive
the conversion.

QGIS shows a rule when ``maximumScale <= scale <= minimumScale`` (0 means
unbounded). ``minimumScale`` is therefore the zoomed-*out* limit and
``maximumScale`` the zoomed-*in* limit.
"""

import math
from typing import Optional

from .model import ZoomInterval

TOP_SCALE = 419311712.0
MAX_TILE_ZOOM = 22


def zoom_to_scale(zoom: float) -> float:
    return TOP_SCALE / (2.0 ** zoom)


def scale_to_zoom(scale: float) -> float:
    if scale <= 0 or not math.isfinite(scale):
        raise ValueError(f"Scale must be positive and finite, got {scale!r}")
    return math.log2(TOP_SCALE / scale)


def interval_from_scales(minimum_scale: float, maximum_scale: float,
                         floor_zoom: float = 0.0) -> ZoomInterval:
    """Visibility interval of a QGIS scale range (0 = unbounded)."""
    low = floor_zoom
    if minimum_scale and minimum_scale > 0:
        low = max(floor_zoom, scale_to_zoom(minimum_scale))
    high: Optional[float] = None
    if maximum_scale and maximum_scale > 0:
        high = scale_to_zoom(maximum_scale)
    interval = ZoomInterval(_snap(low), None if high is None else _snap(high))
    return interval


def _snap(zoom: float, eps: float = 1e-6) -> float:
    """Snap values within ``eps`` of an integer (float noise from log2)."""
    nearest = round(zoom)
    return float(nearest) if abs(zoom - nearest) < eps else zoom


def tile_min_zoom(minimum_scale: float) -> int:
    """First integer tile zoom needed by a rule with this ``minimumScale``."""
    interval = interval_from_scales(minimum_scale, 0)
    return max(0, min(MAX_TILE_ZOOM, int(math.floor(interval.min_zoom + 1e-9))))


def tile_max_zoom(maximum_scale: float) -> int:
    """Last integer tile zoom needed by a rule with this ``maximumScale``."""
    interval = interval_from_scales(0, maximum_scale)
    if interval.max_zoom is None:
        return MAX_TILE_ZOOM
    return max(0, min(MAX_TILE_ZOOM, int(math.ceil(interval.max_zoom - 1e-9)) - 1))
