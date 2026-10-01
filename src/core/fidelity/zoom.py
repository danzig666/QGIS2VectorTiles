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

# QGIS map scale of MapLibre zoom 0 at 96 DPI in a Web Mercator project:
# 40075016.68557849 m / 512 px x 96 px/in / 0.0254 m/in. For another project
# CRS the scale of the same ground resolution is this value divided by the
# Mercator units per map unit (≈ cos(latitude) for projected metre CRSs).
WEB_MERCATOR_TOP_SCALE = 40075016.68557849 / 512.0 * 96.0 / 0.0254
# Historical plugin constant (≈ 1.4174 × the value above): scale-dependent
# rules switched about half a zoom level late.
LEGACY_TOP_SCALE = 419311712.0

TOP_SCALE = WEB_MERCATOR_TOP_SCALE
MAX_TILE_ZOOM = 22


def configure(top_scale: float) -> None:
    """Set the scale of zoom 0 for this export (see ``WEB_MERCATOR_TOP_SCALE``)."""
    global TOP_SCALE  # pylint: disable=global-statement
    if top_scale <= 0 or not math.isfinite(top_scale):
        raise ValueError(f"Invalid top scale {top_scale!r}")
    TOP_SCALE = float(top_scale)


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


def fit_zoom(width_m: float, height_m: float, min_zoom: float, max_zoom: float,
             viewport=(1280, 800), padding: float = 0.9) -> float:
    """MapLibre zoom (512 px tiles) at which a Web Mercator extent of
    ``width_m`` x ``height_m`` fills a typical browser window, clamped to the
    exported zooms. Used for the viewers' start view: opening at the minimum
    zoom shows the whole earth when the export starts at zoom 0."""
    earth = 40075016.68557849
    fits = []
    for size_m, pixels in ((width_m, viewport[0]), (height_m, viewport[1])):
        if size_m > 0:
            fits.append(math.log2(pixels * padding * earth / (512.0 * size_m)))
    zoom = min(fits) if fits else float(min_zoom)
    return round(max(float(min_zoom), min(float(max_zoom), zoom)), 2)
