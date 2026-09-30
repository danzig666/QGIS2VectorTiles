"""
zoom_levels.py

ZoomLevels — utility class for converting between map scales and web tile
zoom levels based on the configured tiling scheme.

The arithmetic lives in ``core/fidelity/zoom.py`` (pure Python, unit
tested). Integer tile zooms are derived from the exact fractional
visibility interval:

* ``"o"`` (zoomed-out edge, ``minimumScale``): ``floor(z_min)`` — tiles of
  that zoom are drawn while the map is between ``floor(z_min)`` and
  ``z_min + 1``.
* ``"i"`` (zoomed-in edge, ``maximumScale``): ``ceil(z_max) - 1``, because
  the visibility interval is half-open ``[z_min, z_max)``.

The legacy implementation rounded the other way (``ceil`` / ``floor``), which
turned a rule visible between two integer zooms into an inverted, never
visible range.
"""

from typing import Optional

from ..core.fidelity import zoom as _zoom


class ZoomLevels:
    """Manages zoom level scales and conversions for web mapping standards."""

    SCALES = [_zoom.TOP_SCALE / (2**zoom) for zoom in range(_zoom.MAX_TILE_ZOOM + 1)]

    @classmethod
    def scale_to_zoom(cls, scale: float, edge: str) -> int:
        """Convert a rule scale limit to the integer tile zoom of that edge."""
        if edge == "o":
            return _zoom.tile_min_zoom(scale)
        return _zoom.tile_max_zoom(scale)

    @classmethod
    def zoom_to_scale(cls, zoom: int) -> Optional[float]:
        """Convert zoom level to scale."""
        if 0 <= zoom < len(cls.SCALES):
            return cls.SCALES[zoom]
        return None
