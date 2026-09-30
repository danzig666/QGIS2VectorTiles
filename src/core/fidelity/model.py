"""
model.py

Immutable plan records shared by the fidelity helpers.

These records deliberately carry plain values only (no QGIS objects) so they
can cross thread boundaries and be unit-tested without QGIS.
"""

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, Tuple


class FidelityMode(str, Enum):
    """How the exporter treats components it cannot reproduce exactly."""

    VECTOR_FIRST = "vector-first"  # default: approximate where declared, report the rest
    STRICT = "strict"              # fail before publication on any fidelity loss
    HYBRID = "hybrid"              # permit raster fallback for non-text cartography

    @classmethod
    def from_index(cls, index: int) -> "FidelityMode":
        return [cls.VECTOR_FIRST, cls.STRICT, cls.HYBRID][int(index)]


class OverzoomPolicy(str, Enum):
    """What the browser shows beyond the archive's native max zoom.

    ``PERSIST``: open-ended rules keep rendering (MapLibre overzooms the last
    generated tiles). ``STOP``: every layer is hidden above the export max zoom.
    """

    PERSIST = "persist"
    STOP = "stop"


class Strategy(str, Enum):
    """Export strategy chosen for one symbol component."""

    NATIVE = "native"
    SPRITE = "sprite"
    MATERIALIZED = "materialized"
    APPROXIMATE = "approximate"
    RASTER = "raster"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ExportProfile:
    """User-selected export behavior."""

    mode: FidelityMode = FidelityMode.VECTOR_FIRST
    overzoom: OverzoomPolicy = OverzoomPolicy.PERSIST
    reference_dpi: float = 96.0
    # Screen-size tolerance for sizes, spacing and pattern periods (CSS px or
    # relative), whichever is larger: plan target 0.5 px / 2 %.
    tolerance_px: float = 0.5
    tolerance_rel: float = 0.02
    # Pattern angle tolerance in degrees.
    tolerance_angle_deg: float = 0.5
    max_pattern_cell_px: int = 256
    # Reference latitude for map-unit sizes in non-Mercator projected CRSs.
    reference_latitude: float = 0.0

    @property
    def strict(self) -> bool:
        return self.mode == FidelityMode.STRICT

    def within_tolerance(self, expected: float, actual: float) -> bool:
        allowed = max(self.tolerance_px, abs(expected) * self.tolerance_rel)
        return abs(expected - actual) <= allowed + 1e-9


MAX_STYLE_ZOOM = 24.0


@dataclass(frozen=True)
class ZoomInterval:
    """A half-open browser visibility interval ``[min_zoom, max_zoom)``.

    ``max_zoom=None`` means unbounded above (visible while overzooming).
    MapLibre hides a layer at exactly its ``maxzoom``.
    """

    min_zoom: float = 0.0
    max_zoom: Optional[float] = None

    def __post_init__(self):
        for value in (self.min_zoom, self.max_zoom):
            if value is not None and not math.isfinite(value):
                raise ValueError(f"Zoom must be finite, got {value!r}")

    @property
    def is_empty(self) -> bool:
        return self.max_zoom is not None and self.max_zoom <= self.min_zoom + 1e-9

    def contains(self, zoom: float) -> bool:
        if zoom < self.min_zoom - 1e-9:
            return False
        return self.max_zoom is None or zoom < self.max_zoom - 1e-9

    def intersect(self, other: "ZoomInterval") -> "ZoomInterval":
        low = max(self.min_zoom, other.min_zoom)
        if self.max_zoom is None:
            high = other.max_zoom
        elif other.max_zoom is None:
            high = self.max_zoom
        else:
            high = min(self.max_zoom, other.max_zoom)
        if high is not None and high < low:
            high = low
        return ZoomInterval(low, high)

    def tile_zooms(self, archive_min: int, archive_max: int) -> Optional[Tuple[int, int]]:
        """Inclusive integer tile zooms needed to draw this interval.

        A map displayed at fractional zoom ``z`` uses tiles of ``floor(z)``, so
        ``[3.2, 3.8)`` needs zoom-3 tiles only. Returns ``None`` when the
        interval does not intersect the archive.
        """
        if self.is_empty:
            return None
        first = max(archive_min, int(math.floor(self.min_zoom + 1e-9)))
        if self.max_zoom is None:
            last = archive_max
        else:
            last = min(archive_max, int(math.ceil(self.max_zoom - 1e-9)) - 1)
        if last < first:
            return None
        return first, last

    def style_bounds(self, archive_max: int, overzoom: OverzoomPolicy) -> Tuple[float, float]:
        """``(minzoom, maxzoom)`` for a MapLibre layer."""
        high = self.max_zoom
        if overzoom == OverzoomPolicy.STOP:
            cap = archive_max + 1.0
            high = cap if high is None else min(high, cap)
        elif high is None:
            high = MAX_STYLE_ZOOM
        return self.min_zoom, min(high, MAX_STYLE_ZOOM)


@dataclass(frozen=True)
class PropertyBinding:
    """How one MapLibre property value was produced from a QGIS property."""

    target: str                         # e.g. "line-width"
    kind: str                           # "constant" | "field" | "zoom" | "field+zoom"
    result_type: str                    # "number" | "color" | "string" | "boolean"
    value: Any = None                   # emitted MapLibre value/expression
    source: str = ""                    # QGIS expression or field (not redacted)
    unit: str = ""                      # original QGIS unit name, if a length
    field_id: str = ""                  # generated tile attribute, if any
    fallback: Any = None


@dataclass(frozen=True)
class ComponentPlan:
    """Planned export route for one symbol component."""

    layer_id: str
    rule_id: str
    symbol_layer_index: Optional[int]
    qgis_type: str
    strategy: Strategy
    reason: str = ""
    constraints: Tuple[str, ...] = field(default_factory=tuple)
