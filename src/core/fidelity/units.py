"""
units.py

Single, context-aware conversion of QGIS render units to MapLibre CSS pixels.

* Units are identified by name (``Qgis.RenderUnit`` members, their legacy
  integer values, or strings) — never by substring guessing, and an unknown
  unit is an error rather than an implicit millimeter.
* Physical units (mm, pt, in, px) convert with the reference DPI (CSS px are
  defined at 96 DPI). Device-pixel ratio is irrelevant here: it affects image
  sampling density, not cartographic size.
* Map units and meters-at-scale become zoom curves. For Web Mercator
  coordinates on a flat MapLibre camera::

      resolution(z) = 40075016.68557849 / (512 * 2**z)   # Mercator m per CSS px
      width_px(z)   = width_mercator / resolution(z)

  512 is MapLibre's logical world size, not the MVT extent.
"""

import math
from dataclasses import dataclass
from typing import Any, Optional

from . import expressions as ex
from .zoom import scale_to_zoom

EARTH_CIRCUMFERENCE = 40075016.68557849
WORLD_SIZE_PX = 512.0
MERCATOR_UNITS_PER_DEGREE = EARTH_CIRCUMFERENCE / 360.0
CURVE_MIN_ZOOM = 0.0
CURVE_MAX_ZOOM = 24.0

# Canonical unit names.
MM, PT, PX, INCH, MAP, METERS, PERCENT, UNKNOWN = (
    "mm", "pt", "px", "in", "map", "m", "pct", "unknown")

# Qgis.RenderUnit / QgsUnitTypes.RenderUnit integer values (stable since 3.0).
_BY_INT = {0: MM, 1: MAP, 2: PX, 3: PERCENT, 4: PT, 5: INCH, 6: UNKNOWN, 7: METERS}
_BY_NAME = {
    "millimeters": MM, "rendermillimeters": MM, "mm": MM,
    "mapunits": MAP, "rendermapunits": MAP, "map": MAP,
    "pixels": PX, "renderpixels": PX, "px": PX,
    "percentage": PERCENT, "renderpercentage": PERCENT, "pct": PERCENT,
    "points": PT, "renderpoints": PT, "pt": PT,
    "inches": INCH, "renderinches": INCH, "in": INCH,
    "metersinmapunits": METERS, "rendermetersinmapunits": METERS, "m": METERS,
    "unknownunit": UNKNOWN, "renderunknownunit": UNKNOWN, "unknown": UNKNOWN,
}


class UnitError(ValueError):
    """Raised when a unit cannot be converted in the given context."""

    def __init__(self, unit: str, message: str):
        self.unit = unit
        super().__init__(message)


def normalize_unit(unit: Any) -> str:
    """Return the canonical unit name for a QGIS unit value."""
    if unit is None:
        return UNKNOWN
    name = getattr(unit, "name", None)
    if isinstance(name, str) and name:
        return _BY_NAME.get(name.lower(), UNKNOWN)
    if isinstance(unit, bool):
        return UNKNOWN
    if isinstance(unit, int):
        return _BY_INT.get(unit, UNKNOWN)
    try:
        as_int = int(unit)  # sip/Qt enums
        if not isinstance(unit, str):
            return _BY_INT.get(as_int, UNKNOWN)
    except (TypeError, ValueError):
        pass
    text = str(unit).strip().split(".")[-1].lower()
    return _BY_NAME.get(text, UNKNOWN)


def physical_factor(unit: str, dpi: float = 96.0) -> Optional[float]:
    """CSS px per unit for context-free units, or ``None``."""
    return {
        MM: dpi / 25.4,
        PT: dpi / 72.0,
        INCH: dpi,
        PX: dpi / 96.0,
    }.get(unit)


@dataclass(frozen=True)
class MapUnitContext:
    """How project map units and ground meters relate to Web Mercator units.

    ``exact`` is False when a reference-latitude approximation was needed.
    """

    mercator_per_map_unit: float
    mercator_per_meter: float
    exact: bool
    description: str

    @classmethod
    def for_project(cls, crs_is_web_mercator: bool, map_units: str,
                    reference_latitude: float) -> "MapUnitContext":
        lat = max(-85.0, min(85.0, reference_latitude))
        mercator_per_meter = 1.0 / math.cos(math.radians(lat))
        units = (map_units or "").lower()
        if crs_is_web_mercator:
            return cls(1.0, mercator_per_meter, True,
                       "Web Mercator project: map units are Mercator units")
        if units.startswith("deg"):
            return cls(MERCATOR_UNITS_PER_DEGREE, mercator_per_meter, False,
                       "Geographic project: 1 degree = Mercator x-extent of 1 degree")
        feet = {"feet": 0.3048, "usfeet": 1200.0 / 3937.0}.get(units.replace(" ", ""))
        unit_m = feet or 1.0
        return cls(unit_m * mercator_per_meter, mercator_per_meter, False,
                   f"Projected project CRS; scale factor at latitude {lat:.4f}")


@dataclass(frozen=True)
class MapUnitScale:
    """Plain copy of ``QgsMapUnitScale`` (0 = unset)."""

    min_scale: float = 0.0
    max_scale: float = 0.0
    min_size_mm: Optional[float] = None
    max_size_mm: Optional[float] = None


class LengthConverter:
    """Convert QGIS lengths (static or data-defined) to CSS pixels."""

    def __init__(self, dpi: float = 96.0, map_context: Optional[MapUnitContext] = None):
        self.dpi = dpi
        self.map_context = map_context or MapUnitContext.for_project(True, "meters", 0.0)

    # --- public API -------------------------------------------------------
    def static(self, value: float, unit: Any,
               map_unit_scale: Optional[MapUnitScale] = None) -> ex.Expression:
        """Convert a constant length; returns a number or a zoom curve."""
        return self.convert(ex.finite(float(value)), unit, map_unit_scale)

    def convert(self, value: ex.Expression, unit: Any,
                map_unit_scale: Optional[MapUnitScale] = None) -> ex.Expression:
        """Convert a number or a feature expression to CSS px."""
        name = normalize_unit(unit)
        factor = physical_factor(name, self.dpi)
        if factor is not None:
            return ex.mul(value, factor)
        if name in (MAP, METERS):
            per_unit = (self.map_context.mercator_per_map_unit if name == MAP
                        else self.map_context.mercator_per_meter)
            return self._map_curve(value, per_unit, map_unit_scale)
        if name == PERCENT:
            raise UnitError(name, "Percentage units need a reference dimension")
        raise UnitError(name, f"Unknown render unit: {unit!r}")

    # --- helpers ----------------------------------------------------------
    def _map_curve(self, value: ex.Expression, mercator_per_unit: float,
                   mus: Optional[MapUnitScale]) -> ex.Expression:
        base = mercator_per_unit * WORLD_SIZE_PX / EARTH_CIRCUMFERENCE  # px per unit at z0
        z_lo, z_hi = CURVE_MIN_ZOOM, CURVE_MAX_ZOOM
        min_px = max_px = None
        if mus is not None:
            # QgsMapUnitScale.minScale is the zoomed-out limit (large number).
            if mus.min_scale:
                z_lo = max(z_lo, min(z_hi, scale_to_zoom(mus.min_scale)))
            if mus.max_scale:
                z_hi = min(z_hi, max(z_lo, scale_to_zoom(mus.max_scale)))
            mm = physical_factor(MM, self.dpi)
            if mus.min_size_mm is not None:
                min_px = mus.min_size_mm * mm
            if mus.max_size_mm is not None:
                max_px = mus.max_size_mm * mm

        def raw(zoom: float) -> float:
            clamped_zoom = min(max(zoom, z_lo), z_hi)
            return base * 2.0 ** clamped_zoom

        stops = {CURVE_MIN_ZOOM, CURVE_MAX_ZOOM, z_lo, z_hi}
        if ex.is_number(value):
            magnitude = abs(value)
            if magnitude == 0:
                return 0
            for limit in (min_px, max_px):
                if limit:
                    knee = math.log2(limit / (magnitude * base))
                    if z_lo < knee < z_hi:
                        stops.add(knee)
            out = []
            for zoom in sorted(stops):
                px = value * raw(zoom)
                out.append((zoom, ex.clamp(px, min_px, max_px) if (min_px or max_px) else px))
            return ex.exponential_zoom_curve(out)

        # Feature-dependent value: multiply inside stop outputs, with a stop at
        # every integer zoom. Clamp knees depend on the value; and MapLibre
        # lays out a data-driven text-size/icon-size at the stops around the
        # tile zoom, packed into 16 bits (at most 512 px): with stops at 0 and
        # 24 only, the zoom-24 size was clipped to 512 px and map-unit labels
        # came out ~4x too small at zoom 17 (Földrészletek).
        stops |= {float(z) for z in range(int(CURVE_MIN_ZOOM), int(CURVE_MAX_ZOOM) + 1)}
        out = []
        for zoom in sorted(stops):
            px = ex.mul(value, raw(zoom))
            out.append((zoom, ex.clamp(px, min_px, max_px) if (min_px or max_px) else px))
        return ex.exponential_zoom_curve(out)
