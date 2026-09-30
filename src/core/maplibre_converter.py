"""Convert QGIS Vector Tile Layer styles to MapLibre GL JSON style format."""

import copy
import json
import math
import os
from os.path import join
from typing import Any, Dict, List, Optional, Union

from qgis.PyQt.QtGui import QColor, QFont, QFontInfo
from ..utils.config import Qt
from qgis.core import (
    QgsVectorTileLayer,
    QgsVectorTileBasicRenderer,
    QgsVectorTileBasicLabeling,
    QgsSymbol,
    QgsPalLayerSettings,
    QgsSimpleLineSymbolLayer,
    QgsSimpleFillSymbolLayer,
    QgsProcessingUtils,
    QgsExpression,
    QgsProperty,
    QgsSymbolLayer,
    QgsTextFormat,
    QgsProject,
    QgsTextBackgroundSettings,
)
from qgis.core import NULL, Qgis, QgsSymbolLayerUtils, QgsFillSymbol, QgsLineSymbol
from qgis.utils import iface
from .glyphs_generator import GlyphGenerator
from .sprite_generator import SpriteGenerator, SpriteRequest, PatternImages
from .fidelity import expressions as ex
from .fidelity.capabilities import classify
from .fidelity.diagnostics import DiagnosticCollector
from .fidelity.model import ExportProfile, Strategy, ZoomInterval
from .fidelity.patterns import LinePatternSpec, render_line_pattern, solve_periodic_cell
from .fidelity.units import LengthConverter, MapUnitScale, UnitError, normalize_unit
from ..utils.config import _SPRITE_QUALITY, _MAPLIBRE_LABELS_FACTOR, _FIELD_PREFIX


def _enum_int(value, default=None):
    """Integer value of a Qt/QGIS enum (Qt5 ints and Qt6/Python enums alike)."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    inner = getattr(value, "value", None)
    if isinstance(inner, int):
        return inner
    return default


def _argb_hex(value) -> str:
    """Generated colour fields store '#RRGGBBAA'; QGIS parses '#AARRGGBB'."""
    text = str(value or "")
    if len(text) == 9 and text.startswith("#"):
        return "#" + text[7:9] + text[1:7]
    return text


def hairline(width):
    """QGIS draws a zero stroke width as a one-pixel (cosmetic) hairline."""
    if ex.is_number(width):
        return 1.0 if width == 0 else width
    if ex.is_zoom_curve(width):
        return ex._map_outputs(width, hairline)  # pylint: disable=protected-access
    return ["case", ["==", width, 0], 1, width]


_PROPERTY_NAMES: Dict[type, Dict[int, str]] = {}


def _property_name(obj, key: int) -> str:
    """Enum member name without prefix (``StrokeWidth``) of a property key of
    ``obj`` (a symbol or symbol layer), for PyQGIS with and without scoped enums."""
    owner = QgsSymbol if isinstance(obj, QgsSymbol) else QgsSymbolLayer
    names = _PROPERTY_NAMES.get(owner)
    if names is None:
        names = {}
        enum = owner.Property
        for attr in dir(enum):
            if attr[:1].isupper():
                value = getattr(enum, attr)
                try:
                    names[int(getattr(value, "value", value))] = (
                        attr[8:] if attr.startswith("Property") else attr)
                except (TypeError, ValueError):
                    continue
        for attr in dir(owner):
            if attr.startswith("Property") and attr != "Property":
                value = getattr(owner, attr)
                try:
                    names.setdefault(int(getattr(value, "value", value)), attr[8:])
                except (TypeError, ValueError):
                    continue
        _PROPERTY_NAMES[owner] = names
    return names.get(int(key), str(key))


def _enum_name(value) -> str:
    name = getattr(value, "name", None)
    return name if isinstance(name, str) else ""


# QgsSymbol opacity property key: "PropertyOpacity" up to 3.3x, "Opacity" later.
_SYMBOL_OPACITY_KEY = getattr(QgsSymbol.Property, "Opacity",
                              getattr(QgsSymbol.Property, "PropertyOpacity", None))


class ConversionContext:
    """Per-export conversion state shared by the property extractors."""

    def __init__(self, diagnostics: Optional[DiagnosticCollector] = None,
                 lengths: Optional[LengthConverter] = None,
                 profile: Optional[ExportProfile] = None):
        self.diagnostics = diagnostics or DiagnosticCollector()
        self.lengths = lengths or LengthConverter()
        self.profile = profile or ExportProfile()
        # Location of the component being converted (for diagnostics).
        self.component = ""
        self.source_layer = ""
        self.reference_zoom = 0.0
        # Zooms over which the current component is visible above reference_zoom.
        self.reference_zoom_span = 0.0

    def report(self, code: str, message: str = "", **extra):
        extra.setdefault("component", self.component)
        extra.setdefault("layer_id", self.source_layer)
        return self.diagnostics.add(code, message, **extra)


class PropertyExtractor:
    """Utility class for extracting and converting PyQGIS properties to MapLibre format.

    Provides shared helpers used by every specialised extractor:
    - Resolution of static vs. data-defined values into MapLibre expressions.
    - Conversion of ``QColor`` instances into MapLibre ``rgba()`` strings.
    - Conversion of QGIS length units into pixels at a 96 DPI baseline.
    """

    # Shared conversion state; replaced by QgisMapLibreStyleExporter per export.
    context: ConversionContext = ConversionContext()

    @staticmethod
    def _infer_type(value: Any) -> str:
        if isinstance(value, bool):
            return "boolean"
        if ex.is_number(value):
            return "number"
        if isinstance(value, str) and (value.startswith("rgb") or value.startswith("#")):
            return "color"
        return "string"

    @classmethod
    def _coerce(cls, result: Any, kind: str, fallback: Any) -> Any:
        """Convert an evaluated QGIS value to the MapLibre type ``kind``."""
        if result is None or result == NULL:
            return fallback
        if kind == "number":
            if isinstance(result, bool):
                return 1.0 if result else 0.0
            return ex.finite(float(result))
        if kind == "color":
            color = result if isinstance(result, QColor) else QgsSymbolLayerUtils.decodeColor(str(result))
            if not color.isValid():
                raise ValueError(f"Invalid color value {result!r}")
            return cls.convert_qcolor_to_maplibre(color)
        if kind == "boolean":
            return bool(result)
        return str(result)

    @classmethod
    def get_value_or_expression(
        cls, value: Any, prop: QgsProperty, kind: Optional[str] = None
    ) -> Union[Any, List]:
        """Return a static value or a typed MapLibre expression.

        * Property reading a generated ``q2vt_*`` tile attribute → a typed
          ``["get", field]`` expression with the static value as fallback.
          The field is found by parsing the QGIS expression
          (``referencedColumns``), not by splitting its text.
        * Feature-independent expression → evaluated once. Valid falsy
          results (0, False, '') are kept; NULL falls back to ``value``;
          evaluation errors are reported instead of silently ignored.
        * Anything else has no browser emitter and is reported.
        """
        if not prop or not prop.isActive():
            return value
        kind = kind or cls._infer_type(value)
        expression = prop.asExpression()
        qexpr = QgsExpression(expression)
        generated = sorted(c for c in qexpr.referencedColumns() if c.startswith(f"{_FIELD_PREFIX}_"))
        if generated:
            field_expr = ex.get(generated[0])
            if kind == "number":
                return ex.to_number(field_expr, value if ex.is_number(value) else 0)
            if kind == "color":
                return ex.to_color(field_expr, value if isinstance(value, str) else "rgba(0, 0, 0, 0)")
            if kind == "boolean":
                return ["to-boolean", field_expr]
            return ["to-string", field_expr]
        if qexpr.hasParserError() or qexpr.referencedColumns() or qexpr.needsGeometry():
            cls.context.report(
                "Q2VT_DDP_NO_EMITTER",
                f"Data-defined property '{expression}' is not available in the web style; "
                "the static value is used.", detail=expression)
            return value
        result = qexpr.evaluate()
        if qexpr.hasEvalError():
            cls.context.report("Q2VT_DDP_EVAL_ERROR",
                               f"Could not evaluate '{expression}': {qexpr.evalErrorString()}",
                               detail=expression)
            return value
        try:
            return cls._coerce(result, kind, value)
        except (TypeError, ValueError, ex.ExpressionError) as err:
            cls.context.report("Q2VT_DDP_EVAL_ERROR",
                               f"Value of '{expression}' is not a valid {kind}: {err}",
                               detail=expression)
            return value

    @staticmethod
    def convert_qcolor_to_maplibre(color: QColor) -> str:
        """Convert a ``QColor`` into a MapLibre-compatible ``rgba()`` string."""
        return f"rgba({color.red()}, {color.green()}, {color.blue()}, {round(color.alphaF(), 4)})"

    @staticmethod
    def map_unit_scale(mus) -> Optional[MapUnitScale]:
        """Plain copy of a ``QgsMapUnitScale`` (or None)."""
        if mus is None:
            return None
        try:
            return MapUnitScale(
                min_scale=mus.minScale, max_scale=mus.maxScale,
                min_size_mm=mus.minSizeMM if mus.minSizeMMEnabled else None,
                max_size_mm=mus.maxSizeMM if mus.maxSizeMMEnabled else None,
            )
        except AttributeError:
            return None

    @classmethod
    def length(cls, value: float, unit_obj=None, prop: QgsProperty = None,
               map_unit_scale=None) -> Union[float, List]:
        """Convert a static or data-defined QGIS length to CSS pixels.

        The QGIS value (static or evaluated per feature) is converted with its
        own unit; map units become zoom curves. An unknown unit is reported
        and the raw number is used as pixels — never silently as millimeters.
        """
        if value is None:
            return value
        mus = cls.map_unit_scale(map_unit_scale)
        raw = cls.get_value_or_expression(value, prop, "number") if prop is not None else value
        try:
            return cls.context.lengths.convert(raw, unit_obj, mus)
        except UnitError as err:
            code = "Q2VT_UNIT_PERCENTAGE" if err.unit == "pct" else "Q2VT_UNIT_UNKNOWN"
            cls.context.report(code, f"{err} (value {value})")
            return raw

    @classmethod
    def convert_length_to_pixels(cls, value: float, unit_obj=None) -> Union[float, List]:
        """Backward-compatible static conversion (see :meth:`length`)."""
        return cls.length(value, unit_obj)

    @classmethod
    def static_pixels(cls, value: float, unit_obj=None, reference_zoom: Optional[float] = None) -> float:
        """A single pixel number, sampling zoom curves at the reference zoom.

        Used where MapLibre needs a constant (e.g. array-valued offsets).
        """
        result = cls.length(value, unit_obj)
        if ex.is_number(result):
            return result
        zoom = cls.context.reference_zoom if reference_zoom is None else reference_zoom
        return ex.evaluate_zoom_curve(result, zoom)

    @staticmethod
    def opacity(symbol=None, symbol_layer=None) -> Union[float, List]:
        """Symbol opacity × data-defined opacity (QGIS 0–100 → MapLibre 0–1)."""
        try:
            base = float(symbol.opacity()) if symbol is not None else 1.0
        except (AttributeError, RuntimeError):
            base = 1.0
        result: Any = base
        props = []
        if symbol is not None:
            props.append(symbol.dataDefinedProperties().property(_SYMBOL_OPACITY_KEY))
        if symbol_layer is not None:
            props.append(symbol_layer.dataDefinedProperties().property(
                QgsSymbolLayer.Property.PropertyOpacity))
        for prop in props:
            if prop is not None and prop.isActive():
                value = PropertyExtractor.get_value_or_expression(100.0, prop, "number")
                result = ex.mul(result, ex.div(value, 100.0))
        if ex.is_number(result):
            return max(0.0, min(1.0, result))
        return ex.clamp(result, 0, 1)

    @staticmethod
    def get_attribute(obj: Any, *names) -> Any:
        """Fetch the first available attribute from ``obj`` matching any of ``names``.

        Tries each attribute name in turn, returning the resolved value
        (calling it if callable). Returns ``None`` if no attribute exists or
        every access fails.

        Args:
            obj:    The object to inspect.
            *names: Attribute names to try, in order.

        Returns:
            The resolved value, or ``None`` if every name fails.
        """
        for name in names:
            if hasattr(obj, name):
                try:
                    attr = getattr(obj, name)
                    return attr() if callable(attr) else attr
                except (RuntimeError, AttributeError, OSError):
                    continue
        return None


class LinePropertyExtractor:
    """Extract line paint and layout properties from QGIS line symbol layers."""

    @staticmethod
    def get_line_color(symbol_layer: QgsSimpleLineSymbolLayer) -> Union[str, List]:
        """Return ``line-color`` resolving any data-defined override."""
        base_color = PropertyExtractor.convert_qcolor_to_maplibre(symbol_layer.color())
        color_prop = symbol_layer.dataDefinedProperties().property(QgsSymbolLayer.Property.PropertyStrokeColor)
        return PropertyExtractor.get_value_or_expression(base_color, color_prop, "color")

    @staticmethod
    def get_line_width(symbol_layer: QgsSimpleLineSymbolLayer) -> Union[float, List]:
        """Return ``line-width`` in CSS px; data-defined widths keep their QGIS unit."""
        width_prop = symbol_layer.dataDefinedProperties().property(
            QgsSymbolLayer.Property.PropertyStrokeWidth
        )
        return hairline(PropertyExtractor.length(
            symbol_layer.width(), symbol_layer.widthUnit(), width_prop,
            symbol_layer.widthMapUnitScale(),
        ))

    @staticmethod
    def get_line_opacity(
        symbol_layer: QgsSimpleLineSymbolLayer, symbol: "QgsSymbol" = None
    ) -> Union[float, List]:
        """Return ``line-opacity`` from the symbol's general Opacity setting.

        Colour alpha lives in ``line-color``; ``symbol.opacity()`` is QGIS's
        separate "Opacity" slider. Data-defined opacity (QGIS 0–100) is
        converted to MapLibre's 0–1 range.
        """
        return PropertyExtractor.opacity(symbol, symbol_layer)

    @staticmethod
    def get_line_cap(symbol_layer: QgsSimpleLineSymbolLayer) -> str:
        """Return ``line-cap`` mapped from the Qt pen-cap style (Qt5 and Qt6)."""
        cap_map = {0x00: "butt", 0x10: "square", 0x20: "round"}
        return cap_map.get(_enum_int(symbol_layer.penCapStyle()), "round")

    @staticmethod
    def get_line_join(symbol_layer: QgsSimpleLineSymbolLayer) -> str:
        """Return ``line-join`` mapped from the Qt pen-join style (Qt5 and Qt6)."""
        join_map = {0x00: "miter", 0x40: "bevel", 0x80: "round", 0x100: "miter"}
        return join_map.get(_enum_int(symbol_layer.penJoinStyle()), "round")

    @staticmethod
    def get_line_miter_limit() -> float:
        """Return ``line-miter-limit`` (MapLibre default: 2.0)."""
        return 2.0

    @staticmethod
    def get_line_round_limit() -> float:
        """Return ``line-round-limit`` (MapLibre default: 1.05)."""
        return 1.05

    @staticmethod
    def get_line_dasharray(
        symbol_layer: QgsSimpleLineSymbolLayer, width_px: float
    ) -> Optional[List[float]]:
        """Return ``line-dasharray`` from a custom dash vector or pen-style preset.

        MapLibre dash lengths are multiples of the line width. QGIS custom
        dash lengths are absolute, so they are converted to pixels and divided
        by the static line width. Qt pen-style presets are already relative.
        """
        try:
            custom_dash_enabled = bool(symbol_layer.useCustomDashPattern())
            dash_vector = symbol_layer.customDashVector()
        except (RuntimeError, AttributeError):
            custom_dash_enabled, dash_vector = False, None

        if custom_dash_enabled and dash_vector:
            # QGIS divides by max(1, width) and Qt scales the pattern back by
            # the same clamped width, so on screen a dash is its own length;
            # MapLibre multiplies by the line width.
            width = width_px if ex.is_number(width_px) and width_px > 0 else 1.0
            unit = symbol_layer.customDashPatternUnit()
            pattern = [max(0.0, PropertyExtractor.static_pixels(d, unit)) / width
                       for d in dash_vector]
        else:
            # Qt pen styles, in pen widths (QPen::dashPattern).
            pattern = {
                2: [4, 2],               # DashLine
                3: [1, 2],               # DotLine
                4: [4, 2, 1, 2],         # DashDotLine
                5: [4, 2, 1, 2, 1, 2],   # DashDotDotLine
            }.get(_enum_int(symbol_layer.penStyle()))
            if pattern is None:
                return None
        # Qt draws square and round caps on every dash (one line width longer,
        # gaps one width shorter); MapLibre does so for round caps only
        # (measured), so square-capped dashes are lengthened here.
        if _enum_int(symbol_layer.penCapStyle()) == 0x10:  # Qt::SquareCap
            pattern = [value + 1.0 if i % 2 == 0 else max(0.0, value - 1.0)
                       for i, value in enumerate(pattern)]
        if len(pattern) % 2:
            pattern = pattern + pattern
        return [round(v, 4) for v in pattern]

    @staticmethod
    def get_line_offset(symbol_layer: QgsSimpleLineSymbolLayer) -> Union[float, List]:
        """Return ``line-offset`` in CSS px (static or data-defined, with units).

        QGIS and MapLibre both offset positive values to the right of the
        line direction (verified by rendering in
        ``tests/integration/test_units_and_properties.py``).
        """
        offset_prop = symbol_layer.dataDefinedProperties().property(
            QgsSymbolLayer.Property.PropertyOffset
        )
        value = PropertyExtractor.length(
            symbol_layer.offset(), symbol_layer.offsetUnit(), offset_prop,
            symbol_layer.offsetMapUnitScale(),
        )
        return value

    @staticmethod
    def get_line_blur() -> float:
        """Return ``line-blur`` in pixels (MapLibre default: 0)."""
        return 0

    @staticmethod
    def get_line_gap_width() -> float:
        """Return ``line-gap-width`` in pixels (MapLibre default: 0)."""
        return 0

    @staticmethod
    def get_line_translate() -> List[float]:
        """Return ``line-translate`` ``[x, y]`` offset in pixels (default ``[0, 0]``)."""
        return [0, 0]

    @staticmethod
    def get_line_translate_anchor() -> str:
        """Return ``line-translate-anchor`` (MapLibre default: ``"map"``)."""
        return "map"

    @staticmethod
    def get_line_sort_key(symbol_layer: QgsSymbolLayer) -> Union[float, List]:
        """Return ``line-sort-key``.

        QGIS feature order within a symbol layer is not reproduced yet; the
        legacy implementation used the layer's *enabled* property as a sort
        key, which is a visibility flag, not an order.
        """
        return 0

    @staticmethod
    def is_pattern_line(symbol_layer: QgsSymbolLayer) -> bool:
        """Return ``True`` if this line symbol layer represents a raster pattern.

        Pattern detection is performed by class-name inspection so QGIS
        versions lacking a particular subclass remain compatible.
        """
        pattern_class_names = {
            "QgsRasterLineSymbolLayer",
            "QgsLineburstSymbolLayer",
        }
        return type(symbol_layer).__name__ in pattern_class_names

    @staticmethod
    def get_line_pattern_path(symbol_layer: QgsSymbolLayer) -> Optional[str]:
        """Return the source image path for a pattern line, or ``None``.

        Used by the converter to register a pattern image with the sprite
        pipeline and to populate ``line-pattern``.
        """
        path = PropertyExtractor.get_attribute(symbol_layer, "path", "imagePath")
        if isinstance(path, str) and path:
            return path
        return None

    @staticmethod
    def is_marker_line(symbol_layer: QgsSymbolLayer) -> bool:
        """Return ``True`` if this line symbol layer decorates the line with markers.

        Detected by class-name inspection (matching the ``is_pattern_line``
        convention above) so QGIS versions lacking the symbol-layer class
        remain compatible.
        """
        return type(symbol_layer).__name__ == "QgsMarkerLineSymbolLayer"

    @staticmethod
    def marker_line_placements(symbol_layer: QgsSymbolLayer) -> set:
        """Names of the active ``Qgis.MarkerLinePlacement`` flags.

        Uses the named flags API. The legacy code converted ``placement()``
        to an ordinal, but it returns flag values (LastVertex = 4), so a
        last-vertex marker was exported as a line-center marker.
        """
        try:
            flags = _enum_int(symbol_layer.placements(), 0)
            return {
                member.name for member in Qgis.MarkerLinePlacement
                if flags & _enum_int(member, 0)
            }
        except (AttributeError, RuntimeError, TypeError):
            return {"Interval"}

    @staticmethod
    def get_marker_line_symbol_placement(symbol_layer: QgsSymbolLayer) -> str:
        """Map QGIS marker-line placements to MapLibre ``symbol-placement``.

        MapLibre supports evenly spaced (``"line"``) or one-per-line
        (``"line-center"``) placement. Central-point markers map exactly;
        vertex/first/last/segment-center/curve placements are approximated and
        reported (exact positions need materialized point features).
        """
        placements = LinePropertyExtractor.marker_line_placements(symbol_layer)
        exact = {"Interval"}, {"CentralPoint"}
        if placements not in exact:
            PropertyExtractor.context.report(
                "Q2VT_MARKER_PLACEMENT_APPROX",
                f"Marker-line placement {sorted(placements)} approximated.",
                strategy=Strategy.APPROXIMATE.value,
            )
        if placements & {"CentralPoint", "SegmentCenter"} and "Interval" not in placements:
            return "line-center"
        return "line"

    @staticmethod
    def get_marker_line_spacing(symbol_layer: QgsSymbolLayer) -> Union[float, List]:
        """Return ``symbol-spacing`` in CSS px from the marker-line interval.

        Map-unit intervals become zoom curves; a data-defined interval that
        depends only on ``@map_scale`` has been resolved per zoom band.
        """
        placements = LinePropertyExtractor.marker_line_placements(symbol_layer)
        try:
            interval = float(symbol_layer.interval())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            interval = 0.0
        prop = symbol_layer.dataDefinedProperties().property(QgsSymbolLayer.Property.PropertyInterval)
        if "Interval" in placements and (interval > 0 or (prop and prop.isActive())):
            spacing = PropertyExtractor.length(interval, symbol_layer.intervalUnit(), prop,
                                               symbol_layer.intervalMapUnitScale())
            if not ex.is_camera_only(spacing):
                PropertyExtractor.context.report(
                    "Q2VT_DDP_NO_EMITTER",
                    "Feature-dependent marker-line interval is not supported; static value used.")
                spacing = PropertyExtractor.length(interval, symbol_layer.intervalUnit())
            if normalize_unit(symbol_layer.intervalUnit()) not in ("map", "m"):
                # MapLibre lays line symbols out once per tile, at the tile's
                # integer zoom, so a screen-size spacing grows up to 2x until
                # the next zoom. Laying out at s/sqrt(2) centres that error
                # (0.71x-1.41x of QGIS instead of 1x-2x). Map-unit spacings
                # grow with the map like QGIS and need no correction.
                spacing = ex.mul(spacing, 1.0 / math.sqrt(2.0))
            return ex.clamp(spacing, 1.0, None)

        # Vertex-type placements have no MapLibre equivalent; a small spacing
        # approximates dense per-vertex markers (reported by the placement).
        if placements & {"Vertex", "FirstVertex", "LastVertex", "CurvePoint", "InnerVertices"}:
            return 1.0
        return 250.0

    @staticmethod
    def get_marker_line_rotate_symbols(symbol_layer: QgsSymbolLayer) -> bool:
        """Return whether markers should rotate to follow the line bearing.

        Mirrors QGIS ``rotateSymbols()``; defaults to ``True`` (QGIS's own
        default) if the property cannot be read.
        """
        try:
            return bool(symbol_layer.rotateSymbols())
        except (AttributeError, RuntimeError):
            return True

    @staticmethod
    def get_marker_line_offset(symbol_layer: QgsSymbolLayer) -> Union[float, List]:
        """Return the marker-line's perpendicular offset in CSS px (curve for map units)."""
        try:
            offset = float(symbol_layer.offset())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return 0.0
        if offset == 0:
            return 0.0
        return PropertyExtractor.length(offset, symbol_layer.offsetUnit(), None,
                                        symbol_layer.offsetMapUnitScale())


class FillPropertyExtractor:
    """Extract fill paint and layout properties from QGIS fill symbol layers."""

    @staticmethod
    def get_fill_color(symbol_layer: QgsSimpleFillSymbolLayer) -> Union[str, List]:
        """Return ``fill-color`` resolving any data-defined override."""
        base_color = PropertyExtractor.convert_qcolor_to_maplibre(symbol_layer.color())
        color_prop = symbol_layer.dataDefinedProperties().property(QgsSymbolLayer.Property.PropertyFillColor)
        return PropertyExtractor.get_value_or_expression(base_color, color_prop, "color")

    @staticmethod
    def get_fill_opacity(
        symbol_layer: QgsSimpleFillSymbolLayer, symbol: "QgsSymbol" = None
    ) -> Union[float, List]:
        """Return ``fill-opacity`` from the symbol's general Opacity setting.

        Colour alpha lives in ``fill-color``; data-defined opacity (0–100 in
        QGIS) is converted to MapLibre's 0–1 range.
        """
        return PropertyExtractor.opacity(symbol, symbol_layer)

    @staticmethod
    def get_fill_outline_color(
        symbol_layer: QgsSimpleFillSymbolLayer,
    ) -> Union[str, List, None]:
        """Return ``fill-outline-color`` if the polygon stroke is visible."""
        try:
            stroke_visible = (symbol_layer.strokeWidth() >= 0
                              and _enum_int(symbol_layer.strokeStyle()) != 0)
        except (AttributeError, RuntimeError):
            stroke_visible = False
        if stroke_visible:
            base_color = PropertyExtractor.convert_qcolor_to_maplibre(symbol_layer.strokeColor())
            color_prop = symbol_layer.dataDefinedProperties().property(
                QgsSymbolLayer.Property.PropertyStrokeColor
            )
            return PropertyExtractor.get_value_or_expression(base_color, color_prop, "color")
        return None

    @staticmethod
    def get_fill_antialias() -> bool:
        """Return ``fill-antialias`` (MapLibre default: ``True``)."""
        return True

    @staticmethod
    def get_fill_translate() -> List[float]:
        """Return ``fill-translate`` ``[x, y]`` offset in pixels (default ``[0, 0]``)."""
        return [0, 0]

    @staticmethod
    def get_fill_translate_anchor() -> str:
        """Return ``fill-translate-anchor`` (MapLibre default: ``"map"``)."""
        return "map"

    @staticmethod
    def get_fill_sort_key(symbol_layer: QgsSymbolLayer) -> Union[float, List]:
        """Return ``fill-sort-key`` (see ``LinePropertyExtractor.get_line_sort_key``)."""
        return 0

    @staticmethod
    def is_pattern_fill(symbol_layer: QgsSymbolLayer) -> bool:
        """Return ``True`` for pattern-based fill layers (point/line/raster/SVG)."""
        pattern_class_names = {
            "QgsPointPatternFillSymbolLayer",
            "QgsLinePatternFillSymbolLayer",
            "QgsRasterFillSymbolLayer",
            "QgsSVGFillSymbolLayer",
            "QgsRandomMarkerFillSymbolLayer",
        }
        return type(symbol_layer).__name__ in pattern_class_names

    @staticmethod
    def get_fill_pattern_path(symbol_layer: QgsSymbolLayer) -> Optional[str]:
        """Return the source image path for a raster/SVG fill pattern, or ``None``."""
        path = PropertyExtractor.get_attribute(symbol_layer, "imageFilePath", "svgFilePath", "path")
        if isinstance(path, str) and path:
            return path
        return None


class IconPropertyExtractor:
    """Extract icon paint and layout properties for MapLibre symbol layers."""

    @staticmethod
    def get_icon_image(marker_name: str) -> str:
        """Return the registered sprite name for ``icon-image``."""
        return marker_name

    @staticmethod
    def marker_scale(symbol: QgsSymbol, symbol_layer: QgsSymbolLayer):
        """``(icon-size, map_units_per_pixel)`` for a marker symbol.

        Sprites are oversampled ``Q`` times and declare ``pixelRatio = Q``, so
        their *logical* size is the symbol's real size. (Shrinking a large
        image with ``icon-size`` instead makes MapLibre space line markers by
        the unscaled image width.)

        Physical units: ``icon-size = 1`` (times ``v / s`` for a data-defined
        size ``v`` over the static size ``s``).

        Map units: the sprite's logical size is the symbol's displayed size
        at the rule's first visible zoom ``z0`` (MapLibre lays out and spaces
        line markers by the logical size), ``icon-size(z) = px(size, z) /
        px(size, z0)`` follows the map like QGIS (including
        ``QgsMapUnitScale`` limits), and the image is oversampled enough to
        stay sharp a few zooms higher. Returns ``(icon-size,
        map_units_per_pixel, oversampling)``.
        """
        try:
            unit = normalize_unit(symbol.sizeUnit())
            size = float(symbol.size())
        except (AttributeError, TypeError):
            unit, size = "unknown", 0.0
        size_prop = symbol_layer.dataDefinedProperties().property(QgsSymbolLayer.Property.PropertySize)
        layer_units = {normalize_unit(symbol.symbolLayer(i).outputUnit())
                       for i in range(symbol.symbolLayerCount())} if symbol else set()
        uses_map = unit in ("map", "m") or bool(layer_units & {"map", "m"})
        if not uses_map:
            return IconPropertyExtractor.get_icon_size(symbol_layer, 1.0), 1.0, None
        if unit not in ("map", "m") or (layer_units - {"map", "m"}):
            PropertyExtractor.context.report(
                "Q2VT_MIXED_UNITS",
                "Marker mixes map units with screen units; the whole icon scales with the map.")
        value = size
        if size_prop and size_prop.isActive():
            value = PropertyExtractor.get_value_or_expression(size, size_prop, "number")
            if ex.is_number(value):
                # A static (e.g. per-zoom resolved) size is applied by QGIS
                # when the sprite is rendered: it *is* the sprite's size.
                size = value
        if size <= 0:
            size = 1.0
        context = PropertyExtractor.context
        unit_name = "map" if unit not in ("map", "m") else unit
        reference_px = max(1.0, PropertyExtractor.static_pixels(size, unit_name, context.reference_zoom))
        map_units_per_pixel = size / reference_px
        span = max(0.0, min(3.0, context.reference_zoom_span))
        oversampling = max(float(_SPRITE_QUALITY),
                           min(_SPRITE_QUALITY * 2.0 ** span, 512.0 / reference_px))
        px = PropertyExtractor.length(value, unit_name, None, symbol.sizeMapUnitScale())
        return ex.clamp(ex.mul(px, 1.0 / reference_px), 0, None), \
            map_units_per_pixel, oversampling

    @staticmethod
    def get_icon_size(
        symbol_layer: QgsSymbolLayer, default_size: float = 1.0
    ) -> Union[float, List]:
        """Return ``icon-size`` honouring any data-defined size.

        The sprite's logical size is the static size (see ``marker_scale``),
        so the static icon-size is ``1``. A data-defined
        size ``v`` (same unit as the static size ``s``) scales the image by
        ``v / s``. The expression is built with the typed builder — the legacy
        code divided a Python list by a number and raised ``TypeError``.
        """
        base_scale = default_size
        size_prop = symbol_layer.dataDefinedProperties().property(QgsSymbolLayer.Property.PropertySize)
        if not size_prop or not size_prop.isActive():
            return base_scale
        try:
            static_size = float(symbol_layer.size())
        except (AttributeError, RuntimeError, TypeError):
            static_size = 0.0
        if static_size <= 0:
            PropertyExtractor.context.report(
                "Q2VT_DDP_NO_EMITTER",
                "Data-defined marker size cannot be scaled from a zero static size.")
            return base_scale
        value = PropertyExtractor.get_value_or_expression(static_size, size_prop, "number")
        if ex.is_number(value):
            return base_scale  # static size: already applied in the rendered sprite
        return ex.clamp(ex.mul(ex.div(value, static_size, fallback=1.0), base_scale), 0, None)

    @staticmethod
    def get_icon_rotate(
        symbol_layer: QgsSymbolLayer = None,
        background: QgsTextBackgroundSettings = None,
    ) -> Union[float, List]:
        """Return ``icon-rotate`` in degrees, honouring data-defined overrides.

        When given a marker symbol layer, the static rotation is taken from
        ``angle()`` and any active ``PropertyAngle`` data-defined override is
        applied. When given a label background, the background rotation is
        used instead.
        """
        if symbol_layer is not None:
            base_angle = 0.0
            try:
                base_angle = float(symbol_layer.angle())
            except (AttributeError, RuntimeError, TypeError):
                pass
            try:
                angle_prop = symbol_layer.dataDefinedProperties().property(
                    QgsSymbolLayer.Property.PropertyAngle
                )
                return PropertyExtractor.get_value_or_expression(base_angle, angle_prop)
            except (AttributeError, RuntimeError):
                return base_angle
        if background is not None and background.enabled():
            try:
                return float(background.rotation())
            except (AttributeError, RuntimeError, TypeError):
                return 0.0
        return 0.0

    @staticmethod
    def get_icon_padding() -> float:
        """Return ``icon-padding`` in pixels (MapLibre default: 2)."""
        return 1

    @staticmethod
    def get_icon_rotation_alignment() -> str:
        """Return ``icon-rotation-alignment`` (default: ``"map"`` for QGIS markers)."""
        return "map"

    @staticmethod
    def get_icon_pitch_alignment() -> str:
        """Return ``icon-pitch-alignment`` (default: ``"viewport"``)."""
        return "viewport"

    @staticmethod
    def get_icon_anchor() -> str:
        """Return ``icon-anchor`` (default: ``"center"``)."""
        return "center"

    @staticmethod
    def get_icon_allow_overlap(allow_overlap: bool = False) -> bool:
        """Return ``icon-allow-overlap`` (MapLibre default: ``False``)."""
        return allow_overlap

    @staticmethod
    def get_icon_ignore_placement() -> bool:
        """Return ``icon-ignore-placement`` (MapLibre default: ``False``)."""
        return False

    @staticmethod
    def get_icon_optional() -> bool:
        """Return ``icon-optional`` (MapLibre default: ``False``)."""
        return False

    @staticmethod
    def get_icon_keep_upright() -> bool:
        """Return ``icon-keep-upright``.

        Defaults to ``True`` to match QGIS rendering for line-aligned markers.
        """
        return True

    @staticmethod
    def get_icon_text_fit(background: QgsTextBackgroundSettings) -> Optional[str]:
        """Return ``icon-text-fit`` if the background is sized as a text buffer."""
        if background.enabled() and background.sizeType() == 0:
            return "both"
        return None

    @staticmethod
    def get_icon_text_fit_padding(
        background: QgsTextBackgroundSettings,
    ) -> Optional[List[float]]:
        """``icon-text-fit-padding`` ``[top, right, bottom, left]``.

        QGIS adds the buffer size (x horizontally, y vertically) around the
        text bounds.
        """
        if background.enabled() and background.sizeType() == 0:
            size = background.size()
            x_px = PropertyExtractor.static_pixels(size.width(), background.sizeUnit())
            y_px = PropertyExtractor.static_pixels(size.height(), background.sizeUnit())
            return [y_px, x_px, y_px, x_px]
        return None

    @staticmethod
    def get_icon_offset(background: QgsTextBackgroundSettings = None) -> List[float]:
        """Return ``icon-offset`` ``[x, y]`` in pixels from background offset settings."""
        if background and background.enabled():
            offset = background.offset()
            unit = background.offsetUnit()
            return [
                PropertyExtractor.static_pixels(offset.x(), unit),
                PropertyExtractor.static_pixels(offset.y(), unit),
            ]
        return [0, 0]

    @staticmethod
    def get_icon_opacity(background: QgsTextBackgroundSettings = None) -> float:
        """Return ``icon-opacity`` from background settings (default ``1.0``)."""
        if background and background.enabled():
            try:
                return background.opacity()
            except (OSError, RuntimeError):
                pass
        return 1.0

    @staticmethod
    def get_icon_color(background: QgsTextBackgroundSettings = None) -> str:
        """Return ``icon-color`` from background fill colour (default white)."""
        if background and background.enabled():
            try:
                return PropertyExtractor.convert_qcolor_to_maplibre(background.fillColor())
            except (OSError, RuntimeError):
                pass
        return "rgb(255, 255, 255)"

    @staticmethod
    def get_icon_halo_color(background: QgsTextBackgroundSettings = None) -> str:
        """Return ``icon-halo-color``.

        When a label background is provided and has a stroke, the stroke
        colour is used; otherwise black is returned to match the MapLibre
        default.
        """
        if background and background.enabled():
            try:
                return PropertyExtractor.convert_qcolor_to_maplibre(background.strokeColor())
            except (OSError, RuntimeError, AttributeError):
                pass
        return "rgb(0, 0, 0)"

    @staticmethod
    def get_icon_halo_width(background: QgsTextBackgroundSettings = None) -> float:
        """Return ``icon-halo-width`` in pixels from the background stroke width."""
        if background and background.enabled():
            try:
                return PropertyExtractor.static_pixels(
                    background.strokeWidth(), background.strokeWidthUnit()
                )
            except (OSError, RuntimeError, AttributeError):
                pass
        return 0

    @staticmethod
    def get_icon_halo_blur() -> float:
        """Return ``icon-halo-blur`` in pixels (default 0)."""
        return 0

    @staticmethod
    def get_icon_translate() -> List[float]:
        """Return ``icon-translate`` ``[x, y]`` offset in pixels (default ``[0, 0]``)."""
        return [0, 0]

    @staticmethod
    def get_icon_translate_anchor() -> str:
        """Return ``icon-translate-anchor`` (MapLibre default: ``"map"``)."""
        return "map"

    @staticmethod
    def get_symbol_placement(label_settings: QgsPalLayerSettings = None) -> str:
        """Return ``symbol-placement`` (``"point"`` or ``"line"``).

        Line, curved and perimeter placements follow the line geometry;
        everything else (including "outside polygons") is point placement.
        """
        if label_settings is None:
            return "point"
        placement = TextPropertyExtractor.placement_name(label_settings)
        if placement in ("Line", "Curved", "PerimeterCurved"):
            return "line"
        return "point"

    @staticmethod
    def get_symbol_spacing(label_settings: QgsPalLayerSettings = None) -> float:
        """Return ``symbol-spacing`` in pixels (MapLibre default: 250)."""
        if label_settings is None:
            return 250.0
        try:
            distance = label_settings.repeatDistance
            if distance and distance > 0:
                return max(1.0, PropertyExtractor.static_pixels(
                    distance, label_settings.repeatDistanceUnit))
        except (AttributeError, RuntimeError):
            pass
        return 250.0

    @staticmethod
    def get_symbol_avoid_edges() -> bool:
        """Return ``symbol-avoid-edges`` (MapLibre default: ``False``)."""
        return False

    @staticmethod
    def get_symbol_sort_key() -> float:
        """Return ``symbol-sort-key`` (default 0; lower keys render first)."""
        return 0

    @staticmethod
    def get_symbol_z_order() -> str:
        """Return ``symbol-z-order`` (default ``"auto"``)."""
        return "auto"


class TextPropertyExtractor:
    """Extract text paint and layout properties from QGIS label settings."""

    @staticmethod
    def get_text_field(label_settings: QgsPalLayerSettings) -> Optional[List]:
        """Return ``text-field`` as a MapLibre ``["get", field]`` expression."""
        if label_settings.fieldName:
            return ["get", label_settings.fieldName]
        return None

    @staticmethod
    def get_text_font(text_format: QgsTextFormat) -> str:
        """Return the fontstack name for ``text-font``.

        Uses the family/style the font actually resolves to on this system
        (fontconfig may substitute the family) and the same naming as the
        glyph generator. An unresolvable font is reported as an error, since
        the browser would otherwise render the labels without glyphs.
        """
        font = text_format.font()
        info = QFontInfo(font)
        candidates = [
            (font.family(), font.styleName()),
            (info.family(), info.styleName()),
            (font.family(), ""),
            (info.family(), ""),
        ]
        for family, style in candidates:
            stack = GlyphGenerator.resolve_fontstack(family, style)
            if stack:
                return stack
        PropertyExtractor.context.report(
            "Q2VT_FONT_UNRESOLVED",
            f"Label font '{f'{font.family()} {font.styleName()}'.strip()}' is not installed; "
            "no glyphs can be generated for it.")
        return f"{font.family()} {font.styleName()}".strip()

    @staticmethod
    def placement_name(label_settings: QgsPalLayerSettings) -> str:
        """Name of the ``Qgis.LabelPlacement`` value (version independent)."""
        names = {0: "AroundPoint", 1: "OverPoint", 2: "Line", 3: "Curved", 4: "Horizontal",
                 5: "Free", 6: "OrderedPositionsAroundPoint", 7: "PerimeterCurved",
                 8: "OutsidePolygons"}
        try:
            placement = label_settings.placement
        except AttributeError:
            return "AroundPoint"
        return _enum_name(placement) or names.get(_enum_int(placement), "AroundPoint")

    @staticmethod
    def get_text_size(
        text_format: QgsTextFormat, label_settings: QgsPalLayerSettings, viewer: int
    ) -> Union[float, List]:
        """Return ``text-size`` in CSS px from the format's size *and unit*.

        The legacy code always read ``font().pointSizeF()``, ignoring text
        formats sized in millimeters, pixels or map units.
        """
        size_prop = label_settings.dataDefinedProperties().property(QgsPalLayerSettings.Property.Size)
        size = PropertyExtractor.length(
            text_format.size(), text_format.sizeUnit(), size_prop,
            text_format.sizeMapUnitScale(),
        )
        if viewer == 0:
            # In MapLibre viewer texts are being displayed bigger then QGIS
            # original project although when being read in QGIS canvas as vector tiles style
            # they being displayed correctly. Because of that they being divided in this module
            # and being increased later in server_initializer so the output qlr exts size will be valid.
            size = ex.div(size, _MAPLIBRE_LABELS_FACTOR)
        return size

    @staticmethod
    def get_text_color(
        text_format: QgsTextFormat, label_settings: QgsPalLayerSettings
    ) -> Union[str, List]:
        """Return ``text-color`` honouring any data-defined override."""
        base_color = PropertyExtractor.convert_qcolor_to_maplibre(text_format.color())
        color_prop = label_settings.dataDefinedProperties().property(QgsPalLayerSettings.Property.Color)
        return PropertyExtractor.get_value_or_expression(base_color, color_prop, "color")

    @staticmethod
    def get_text_opacity(
        text_format: QgsTextFormat,
        label_settings: QgsPalLayerSettings = None,
    ) -> Union[float, List]:
        """Return ``text-opacity`` (format opacity × data-defined 0–100 opacity)."""
        try:
            base_opacity = text_format.opacity()
        except (AttributeError, RuntimeError):
            base_opacity = 1.0
        if label_settings is None:
            return base_opacity
        prop_key = getattr(QgsPalLayerSettings.Property, "FontOpacity", None)
        if prop_key is None:
            return base_opacity
        opacity_prop = label_settings.dataDefinedProperties().property(prop_key)
        if not opacity_prop or not opacity_prop.isActive():
            return base_opacity
        value = PropertyExtractor.get_value_or_expression(100.0, opacity_prop, "number")
        return ex.clamp(ex.mul(ex.div(value, 100.0), base_opacity), 0, 1)

    @staticmethod
    def get_text_halo_color(
        text_format: QgsTextFormat,
        label_settings: QgsPalLayerSettings = None,
    ) -> Union[str, List]:
        """Return ``text-halo-color`` from buffer settings (data-defined aware)."""
        buffer = text_format.buffer()
        if not buffer.enabled():
            return "rgba(255, 255, 255, 0)"
        color = QColor(buffer.color())
        color.setAlphaF(color.alphaF() * buffer.opacity())
        base_color = PropertyExtractor.convert_qcolor_to_maplibre(color)
        if label_settings is None:
            return base_color
        color_prop = label_settings.dataDefinedProperties().property(
            QgsPalLayerSettings.Property.BufferColor
        )
        return PropertyExtractor.get_value_or_expression(base_color, color_prop, "color")

    @staticmethod
    def get_text_halo_width(
        text_format: QgsTextFormat,
        label_settings: QgsPalLayerSettings = None,
        viewer: int = 0
    ) -> Union[float, List]:
        """Return ``text-halo-width`` in pixels from the buffer size and unit.

        QGIS buffers and MapLibre halos both extend the stated distance beyond
        the glyph outline.
        """
        buffer = text_format.buffer()
        if not buffer.enabled():
            return 0
        size_prop = None
        if label_settings is not None:
            size_prop = label_settings.dataDefinedProperties().property(
                QgsPalLayerSettings.Property.BufferSize
            )
        width = PropertyExtractor.length(buffer.size(), buffer.sizeUnit(), size_prop,
                                         buffer.sizeMapUnitScale())
        return ex.div(width, _MAPLIBRE_LABELS_FACTOR)

    @staticmethod
    def get_text_halo_blur(
        text_format: QgsTextFormat,
        label_settings: QgsPalLayerSettings = None,
    ) -> Union[float, List]:
        """Return ``text-halo-blur`` in pixels (0: QGIS buffers are not blurred)."""
        return 0

    @staticmethod
    def get_text_anchor(label_settings: QgsPalLayerSettings) -> str:
        """Return ``text-anchor``.

        Only "over point" placement uses the quadrant; horizontal/free
        polygon labels are centred, line labels are centred on the line.
        """
        if TextPropertyExtractor.placement_name(label_settings) != "OverPoint":
            return "center"
        anchor_map = {
            0: "bottom-right", 1: "bottom",  2: "bottom-left",
            3: "right",        4: "center",  5: "left",
            6: "top-right",    7: "top",     8: "top-left",
        }
        return anchor_map.get(_enum_int(label_settings.quadOffset), "center")

    @staticmethod
    def get_text_justify(label_settings: QgsPalLayerSettings) -> Union[str, List]:
        """Return ``text-justify`` mapped from the multi-line alignment setting.

        Honours a data-defined ``MultiLineAlignment`` override when active.
        For field-based overrides the source attribute is expected to
        contain the literal MapLibre keyword (``"left"``, ``"center"``,
        ``"right"`` or ``"auto"``); QGIS-specific integer/string codes are
        not translated server-side.
        """
        try:
            justification = label_settings.multiLineAlignment
        except AttributeError:
            try:
                justification = label_settings.alignment
            except AttributeError:
                justification = 1
        justify_map = {0: "left", 1: "center", 2: "right", 3: "center"}
        base_justify = justify_map.get(justification, "left")
        try:
            justify_prop = label_settings.dataDefinedProperties().property(
                QgsPalLayerSettings.Property.MultiLineAlignment
            )
            return PropertyExtractor.get_value_or_expression(base_justify, justify_prop)
        except (AttributeError, RuntimeError):
            return base_justify

    @staticmethod
    def get_text_offset(label_settings: QgsPalLayerSettings,
                        text_size_px: Union[float, List] = 16.0) -> List[float]:
        """Return ``text-offset`` ``[x, y]`` in **ems**.

        MapLibre text offsets are measured in ems of the text size; the
        legacy code emitted pixels. Data-defined text sizes use the static
        size as the em reference.
        """
        x_offset = label_settings.xOffset
        y_offset = label_settings.yOffset
        if x_offset == 0 and y_offset == 0:
            return [0, 0]
        size = text_size_px if ex.is_number(text_size_px) else 16.0
        if size <= 0:
            return [0, 0]
        unit = label_settings.offsetUnits
        return [
            PropertyExtractor.static_pixels(x_offset, unit) / size,
            PropertyExtractor.static_pixels(y_offset, unit) / size,
        ]

    @staticmethod
    def get_text_radial_offset(label_settings: QgsPalLayerSettings,
                               text_size_px: Union[float, List] = 16.0) -> float:
        """Return ``text-radial-offset`` in ems for "around point" placement.

        0.7 em is the historical empirical clearance around the point symbol;
        the QGIS label distance is added on top of it.
        """
        base = 0.7
        try:
            distance = float(label_settings.dist)
        except (AttributeError, TypeError, ValueError):
            return base
        size = text_size_px if ex.is_number(text_size_px) else 16.0
        if not distance or size <= 0:
            return base
        return base + PropertyExtractor.static_pixels(distance, label_settings.distUnits) / size

    @staticmethod
    def get_text_variable_anchor(label_settings: QgsPalLayerSettings = None) -> Optional[List[str]]:
        """Return ``text-variable-anchor`` for "around point" placements only."""
        if label_settings is not None and TextPropertyExtractor.placement_name(label_settings) \
                not in ("AroundPoint", "OrderedPositionsAroundPoint"):
            return None
        return ["bottom",  "bottom-left", "bottom-right", "left", "right", "top", "top-left", "top-right"]

    @staticmethod
    def get_text_max_angle(label_settings: QgsPalLayerSettings) -> float:
        """Return ``text-max-angle`` in degrees for curved-label placement.

        Sourced from ``maxCurvedCharAngleIn`` (with a fallback to
        ``maxCurvedCharAngleOut``); MapLibre defaults to 45° when omitted.
        """
        try:
            angle_in = label_settings.maxCurvedCharAngleIn
            if angle_in is not None:
                return abs(float(angle_in))
        except (AttributeError, RuntimeError, TypeError):
            pass
        try:
            angle_out = label_settings.maxCurvedCharAngleOut
            if angle_out is not None:
                return abs(float(angle_out))
        except (AttributeError, RuntimeError, TypeError):
            pass
        return 45.0

    @staticmethod
    def get_text_allow_overlap(label_settings: QgsPalLayerSettings = None) -> bool:
        """Return ``text-allow-overlap``: QGIS labels that may overlap
        ("show all labels", overlap if required / at no cost) are always
        drawn; MapLibre has no "only if required" mode."""
        if label_settings is None:
            return False
        try:
            handling = label_settings.placementSettings().overlapHandling()
        except AttributeError:  # QGIS < 3.26
            return bool(getattr(label_settings, "displayAll", False))
        return _enum_int(handling, 0) != 0  # Qgis.LabelOverlapHandling.PreventOverlap

    @staticmethod
    def get_text_ignore_placement() -> bool:
        """Return ``text-ignore-placement`` (MapLibre default: ``False``)."""
        return False

    @staticmethod
    def get_text_optional() -> bool:
        """Return ``text-optional`` (MapLibre default: ``False``)."""
        return False

    @staticmethod
    def get_text_padding() -> float:
        """Return ``text-padding`` in pixels (MapLibre default: 2)."""
        return 10

    @staticmethod
    def get_text_line_height() -> float:
        """Return ``text-line-height`` (MapLibre default: 1.2)."""
        return 1.2

    @staticmethod
    def get_text_letter_spacing() -> float:
        """Return ``text-letter-spacing`` in ems (default 0)."""
        return 0

    @staticmethod
    def get_text_transform(label_settings: QgsPalLayerSettings = None) -> str:
        """Return ``text-transform`` (``"none"``, ``"uppercase"``, or ``"lowercase"``).

        Maps from the QGIS capitalisation setting when available.
        """
        if label_settings is None:
            return "none"
        try:
            cap = PropertyExtractor.get_attribute(
                label_settings.format(), "capitalization"
            )
        except (AttributeError, RuntimeError):
            return "none"
        # QgsStringUtils::Capitalization: 1=AllUppercase, 2=AllLowercase
        if cap == 1:
            return "uppercase"
        if cap == 2:
            return "lowercase"
        return "none"

    @staticmethod
    def get_text_max_width(label_settings: QgsPalLayerSettings) -> float:
        """Return ``text-max-width`` in ems.

        Uses ``autoWrapLength`` when set; otherwise a very large value to
        effectively disable wrapping.
        """
        if label_settings.autoWrapLength > 0:
            return label_settings.autoWrapLength
        return 999

    @staticmethod
    def get_text_keep_upright() -> bool:
        """Return ``text-keep-upright`` (default ``True``)."""
        return True

    @staticmethod
    def get_text_rotate(label_settings: QgsPalLayerSettings) -> Union[float, List]:
        """Return ``text-rotate`` in degrees, honouring data-defined overrides."""
        base_rotation = label_settings.angleOffset if label_settings.angleOffset != 0 else 0
        rotation_prop = label_settings.dataDefinedProperties().property(
            QgsPalLayerSettings.Property.LabelRotation
        )
        return PropertyExtractor.get_value_or_expression(base_rotation, rotation_prop, "number")

    @staticmethod
    def get_text_rotation_alignment() -> str:
        """Return ``text-rotation-alignment`` (default ``"map"``)."""
        return "map"

    @staticmethod
    def get_text_pitch_alignment() -> str:
        """Return ``text-pitch-alignment`` (default ``"viewport"``)."""
        return "viewport"

    @staticmethod
    def get_text_translate() -> List[float]:
        """Return ``text-translate`` ``[x, y]`` offset in pixels (default ``[0, 0]``)."""
        return [0, 0]

    @staticmethod
    def get_text_translate_anchor() -> str:
        """Return ``text-translate-anchor`` (MapLibre default: ``"map"``)."""
        return "map"


class QgisMapLibreStyleExporter:
    """Export QGIS Vector Tile Layer styles to a MapLibre GL style JSON.

    Walks a ``QgsVectorTileLayer``'s renderer and labelling, converting each
    style entry into the appropriate MapLibre layer (``fill``, ``line``,
    ``symbol``, etc.). Marker symbols and label background markers are
    registered with an internal sprite registry so the companion
    ``SpriteGenerator`` can render them as a sprite sheet alongside the
    emitted ``style.json``.

    Every symbol layer is classified by ``fidelity.capabilities``; anything
    that cannot be reproduced is reported through ``diagnostics`` instead of
    being emitted as a plausible-looking default.
    """

    def __init__(
        self,
        output_dir: str,
        utils_dir,
        layer: Optional[QgsVectorTileLayer] = None,
        background_type: int = 0,
        viewer: int = 0,
        minzoom: int = 0,
        maxzoom: int = 14,
        diagnostics: Optional[DiagnosticCollector] = None,
        profile: Optional[ExportProfile] = None,
        visibility: Optional[Dict[str, ZoomInterval]] = None,
        lengths: Optional[LengthConverter] = None,
        ordered_styles: Optional[set] = None,
    ):
        """Initialise the exporter.

        Args:
            output_dir:      Directory where ``style.json`` and the sprite
                             folder will be written.
            layer:           The vector tile layer to export. If ``None``,
                             the active layer in the QGIS interface is used.
            background_type: Background tile preset:
                             ``0`` = OpenStreetMap raster,
                             ``1`` = NASA Blue Marble raster,
                             anything else = solid project background colour.
            minzoom/maxzoom: Native zoom range of the tile archive.
            diagnostics:     Collector receiving fidelity diagnostics.
            profile:         Export profile (mode, overzoom policy, tolerances).
            visibility:      Exact visibility interval per style name (rule
                             description); falls back to the integer zoom
                             range stored on the vector tile style.
            lengths:         Unit converter (map-unit context of the project).
            ordered_styles:  Style names whose features carry a QGIS
                             drawing rank (renderer order-by) to sort by.
        """
        self.output_dir = output_dir
        self.utils_dir = utils_dir
        self.marker_symbols: dict = {}
        self.pattern_images: Dict[str, PatternImages] = {}
        self.marker_counter = 0
        self.glyphs = {}
        self.viewer = viewer
        self.minzoom = minzoom
        self.maxzoom = maxzoom
        self.diagnostics = diagnostics or DiagnosticCollector()
        self.profile = profile or ExportProfile()
        self.visibility = visibility or {}
        self.ordered_styles = ordered_styles or set()
        self.context = ConversionContext(self.diagnostics, lengths, self.profile)
        PropertyExtractor.context = self.context
        self.sprite_names: List[str] = []
        self.layer = self._resolve_layer(layer)
        self.source_name = "q2vt_tiles"
        self.style = self._build_style_skeleton()
        self.style["layers"].append(self._build_background_layer(background_type))

    def _resolve_layer(self, layer: Optional[QgsVectorTileLayer]) -> QgsVectorTileLayer:
        """Return ``layer`` or fall back to the active QGIS layer.

        Raises:
            ValueError: If no layer is supplied and no active layer exists,
                        or if the resolved layer is not a ``QgsVectorTileLayer``.
        """
        if layer is None:
            if iface and iface.activeLayer():
                layer = iface.activeLayer()
            else:
                raise ValueError("No active layer found and no layer provided")
        if not isinstance(layer, QgsVectorTileLayer):
            raise ValueError(f"Layer must be a QgsVectorTileLayer, got {type(layer).__name__}")
        return layer

    def _build_style_skeleton(self) -> dict:
        """Build the empty MapLibre style document with sources and sprite refs.

        The vector source declares the archive's native zoom range so that
        MapLibre overzooms the last generated tiles instead of requesting
        tiles that do not exist (the local server answers those with 204).
        """
        return {
            "version": 8,
            "name": f"{self.source_name}_style",
            "sources": {
                self.source_name: {
                    "type": "vector",
                    "tiles": ["http://localhost:9000/tiles/tiles/{z}/{x}/{y}.pbf?v=10031993"],
                    "minzoom": max(0, int(self.minzoom)),
                    "maxzoom": max(int(self.minzoom), int(self.maxzoom)),
                }
            },
            "glyphs": "http://localhost:9000/style/glyphs/{fontstack}/{range}.pbf",
            "sprite": "http://localhost:9000/style/sprite/sprite",
            "layers": [],
        }

    def _build_background_layer(self, background_type: int) -> dict:
        """Add a background source to the style and return its layer definition.

        Args:
            background_type: ``0`` for OSM, ``1`` for NASA Blue Marble; any
                             other value emits a solid-colour background
                             using the project's background colour.
        """
        if background_type == 0:
            self.style["sources"]["osm"] = {
                "type": "raster",
                "tiles": [
                    "https://a.tile.openstreetmap.org/{z}/{x}/{y}.png",
                    "https://b.tile.openstreetmap.org/{z}/{x}/{y}.png",
                    "https://c.tile.openstreetmap.org/{z}/{x}/{y}.png",
                ],
                "tileSize": 256,
                "attribution": (
                    '&copy; <a href="https://www.openstreetmap.org/copyright">'
                    "OpenStreetMap</a> contributors"
                ),
            }
            return {"id": "osm-background", "type": "raster", "source": "osm",
                    "minzoom": 0, "maxzoom": 22}

        if background_type == 1:
            self.style["sources"]["bluemarbel"] = {
                "type": "raster",
                "tiles": [
                    "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
                    "BlueMarble_ShadedRelief/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg"
                ],
                "tileSize": 256,
            }
            return {"id": "bluemarbel-background", "type": "raster", "source": "bluemarbel",
                    "minzoom": 0, "maxzoom": 22}

        bg_color = PropertyExtractor.convert_qcolor_to_maplibre(
            QgsProject.instance().backgroundColor()
        )
        return {
            "id": "background", "type": "background", "minzoom": 0, "maxzoom": 22,
            "paint": {"background-color": bg_color},
        }

    def export(self) -> Dict[str, Any]:
        """Convert all styles and labelling on the layer and write to disk.

        Returns:
            The complete MapLibre style dictionary.
        """
        renderer = self.layer.renderer()
        if isinstance(renderer, QgsVectorTileBasicRenderer):
            for style in renderer.styles():
                self._convert_renderer_style(style)

        labeling = self.layer.labeling()
        if isinstance(labeling, QgsVectorTileBasicLabeling):
            for style in labeling.styles():
                self._convert_labeling_style(style)

        self.save_to_file()
        return self.style

    # --- visibility ---------------------------------------------------------
    def _style_interval(self, style) -> ZoomInterval:
        """Exact visibility interval of a vector tile style entry."""
        interval = self.visibility.get(style.styleName())
        if interval is None:
            # Integer tile range [o, i] covers the half-open interval [o, i + 1).
            interval = ZoomInterval(float(style.minZoomLevel()), float(style.maxZoomLevel() + 1))
        return interval.intersect(ZoomInterval(float(self.minzoom), None))

    def _zoom_bounds(self, style):
        interval = self._style_interval(style)
        if interval.is_empty:
            self.diagnostics.add(
                "Q2VT_ZOOM_EMPTY_INTERVAL",
                f"Style '{style.styleName()}' has an empty visibility interval.",
                component=style.styleName(), layer_id=style.layerName())
            return None
        self.context.reference_zoom = interval.min_zoom
        bounds = interval.style_bounds(self.maxzoom, self.profile.overzoom)
        self.context.reference_zoom_span = min(bounds[1], self.maxzoom + 1) - interval.min_zoom
        return bounds

    def _convert_renderer_style(self, style):
        """Convert a single ``QgsVectorTileBasicRendererStyle`` into MapLibre layer(s)."""
        if not style.isEnabled() or not style.symbol():
            return
        bounds = self._zoom_bounds(style)
        if bounds is None:
            return
        self.context.component = style.styleName()
        self.context.source_layer = style.layerName()
        first = len(self.style["layers"])
        self._convert_symbol(
            style.symbol(), style.styleName(), style.layerName(),
            self.source_name, bounds[0], bounds[1],
        )
        if style.styleName() in self.ordered_styles:
            self._apply_draw_order(self.style["layers"][first:])

    _SORT_KEYS = {"fill": "fill-sort-key", "line": "line-sort-key",
                  "circle": "circle-sort-key", "symbol": "symbol-sort-key"}

    def _apply_draw_order(self, layer_defs) -> None:
        """Sort a layer's features like QGIS' "Control feature rendering order".

        The ordering holds within each style layer; features of different
        symbol layers are not interleaved per feature as in QGIS.
        """
        from .rules_exporter import ORDER_FIELD
        for layer_def in layer_defs:
            key = self._SORT_KEYS.get(layer_def["type"])
            if key and "text-field" not in layer_def.get("layout", {}):
                layer_def["layout"][key] = ["to-number", ["get", ORDER_FIELD], 0]

    def _convert_labeling_style(self, style):
        """Convert a single ``QgsVectorTileBasicLabelingStyle`` into a MapLibre symbol layer."""
        if not style.isEnabled() or not style.labelSettings():
            return
        bounds = self._zoom_bounds(style)
        if bounds is None:
            return
        self.context.component = style.styleName()
        self.context.source_layer = style.layerName()
        self._convert_label(
            style.labelSettings(), style.styleName(), style.layerName(),
            self.source_name, bounds[0], bounds[1],
        )

    # --- classification -----------------------------------------------------
    @staticmethod
    def _active_ddp_names(obj) -> List[str]:
        """Enum names (``StrokeWidth``, ``Opacity``...) of the active
        data-defined properties of a symbol or symbol layer.

        The property *definitions* use legacy names (``outlineWidth``,
        ``alpha``), so the enum member names are used instead.
        """
        names = []
        try:
            props = obj.dataDefinedProperties()
        except (AttributeError, RuntimeError):
            return names
        for key in props.propertyKeys():
            prop = props.property(key)
            if prop and prop.isActive():
                names.append(_property_name(obj, key))
        return names

    _STROKE_DDP = frozenset({"StrokeColor", "StrokeWidth", "StrokeStyle", "JoinStyle"})

    def _classify(self, symbol_layer: QgsSymbolLayer, index: int) -> Strategy:
        """Classify a symbol layer and report unsupported data-defined properties."""
        layer_type = symbol_layer.layerType()
        names = self._active_ddp_names(symbol_layer)
        try:
            if layer_type == "SimpleFill" and symbol_layer.strokeStyle() == Qt.PenStyle.NoPen:
                # The outline was split into its own line layer.
                names = [n for n in names if n not in self._STROKE_DDP]
        except (AttributeError, RuntimeError):
            pass
        result = classify(layer_type, names)
        for name in result.unsupported_properties:
            self.context.report(
                "Q2VT_DDP_NO_EMITTER",
                f"{layer_type}: data-defined '{name}' has no browser equivalent; "
                "the static value is used.",
                symbol_layer_index=index, strategy=result.strategy.value)
        if result.strategy == Strategy.UNSUPPORTED:
            self.context.report(
                "Q2VT_UNSUPPORTED_SYMBOL_LAYER",
                f"{layer_type}: {result.reason}", symbol_layer_index=index,
                strategy=result.strategy.value)
        try:
            effect = symbol_layer.paintEffect()
            if effect is not None and effect.enabled() and type(effect).__name__ not in (
                    "QgsDefaultPaintEffect",):
                stack = getattr(effect, "effectList", lambda: [])()
                if not (type(effect).__name__ == "QgsEffectStack" and all(
                        type(e).__name__ == "QgsDrawSourceEffect" for e in stack)):
                    self.context.report("Q2VT_UNSUPPORTED_EFFECT",
                                        f"{layer_type}: paint effect is ignored.",
                                        symbol_layer_index=index)
        except (AttributeError, RuntimeError):
            pass
        return result.strategy

    # --- symbols ------------------------------------------------------------
    def _convert_symbol(
        self,
        symbol: QgsSymbol,
        style_name: str,
        source_layer_name: str,
        source_name: str,
        min_zoom: float = -1,
        max_zoom: float = -1,
    ):
        """Dispatch a QGIS symbol to the correct conversion routine by symbol type."""
        if symbol.symbolLayerCount() == 0:
            return
        symbol_layer = symbol.symbolLayer(0)
        symbol_type = symbol.type()
        for name in self._active_ddp_names(symbol):
            if name not in ("Opacity",):
                self.context.report("Q2VT_DDP_NO_EMITTER",
                                    f"Symbol-level data-defined '{name}' is not exported.")

        if symbol_type == QgsSymbol.SymbolType.Marker:
            if self._classify(symbol_layer, 0) == Strategy.UNSUPPORTED:
                return
            if symbol.symbolLayerCount() == 1 and symbol_layer.layerType() == "FontMarker" \
                    and self._font_marker_as_text(symbol_layer, source_layer_name):
                self._convert_font_marker(symbol_layer, symbol, style_name, source_layer_name,
                                          source_name, min_zoom, max_zoom)
                return
            if self._native_circle(symbol, style_name, source_layer_name, source_name,
                                   min_zoom, max_zoom):
                return
            self._convert_marker_symbol(
                symbol_layer, symbol, style_name, source_layer_name,
                source_name, min_zoom, max_zoom,
            )
        elif symbol_type == QgsSymbol.SymbolType.Line:
            self._convert_line_symbol(
                symbol, style_name, source_layer_name, source_name, min_zoom, max_zoom
            )
        elif symbol_type == QgsSymbol.SymbolType.Fill:
            # Normally one layer (the flattener splits symbols); generator
            # sub-symbols can carry several, drawn bottom-to-top.
            for index in range(symbol.symbolLayerCount()):
                fill_layer = symbol.symbolLayer(index)
                if self._classify(fill_layer, index) == Strategy.UNSUPPORTED:
                    continue
                self._convert_fill_symbol(
                    fill_layer, symbol, style_name if index == 0 else f"{style_name}_layer{index}",
                    source_layer_name, source_name, min_zoom, max_zoom)

    def _base_layer_def(
        self,
        layer_type: str,
        style_name: str,
        source_layer_name: str,
        source_name: str,
        min_zoom: float,
        max_zoom: float,
    ) -> dict:
        """Build a common MapLibre layer-definition skeleton with id/source/zoom range."""
        layer_def: dict = {
            "id": style_name,
            "type": layer_type,
            "source": source_name,
            "source-layer": source_layer_name,
            "paint": {},
            "layout": {},
        }
        if min_zoom is not None and min_zoom >= 0:
            layer_def["minzoom"] = min_zoom
        if max_zoom is not None and max_zoom >= 0:
            layer_def["maxzoom"] = max_zoom
        return layer_def

    def _next_name(self, prefix: str) -> str:
        name = f"{prefix}_{self.marker_counter}"
        self.marker_counter += 1
        return name

    def _register_pattern(self, symbol_or_layer) -> str:
        """Register a pattern preview (symbol or symbol layer) in the sprite registry.

        A bare symbol layer is wrapped in a matching symbol (fill or line) with
        the wrapper's default layer removed — the sprite renderer only accepts
        whole symbols. The legacy code passed the symbol layer itself, which
        failed inside the renderer and was replaced by a transparent image.
        """
        pattern_name = self._next_name("pattern")
        symbol = symbol_or_layer
        if isinstance(symbol_or_layer, QgsSymbolLayer):
            wrapper = QgsLineSymbol() if symbol_or_layer.type() == QgsSymbol.SymbolType.Line \
                else QgsFillSymbol()
            wrapper.changeSymbolLayer(0, symbol_or_layer.clone())
            symbol = wrapper
        else:
            symbol = symbol_or_layer.clone()
        self.marker_symbols[pattern_name] = SpriteRequest(symbol, bake_rotation=True)
        return pattern_name

    def _register_line_pattern(self, symbol_layer, symbol: QgsSymbol) -> Optional[str]:
        """Register a verified periodic hatch for a ``QgsLinePatternFillSymbolLayer``.

        Returns ``None`` when the hatch cannot be described analytically
        (e.g. a non-solid or multi-layer line sub-symbol); the caller then
        falls back to an approximate preview texture.
        """
        sub = symbol_layer.subSymbol()
        if sub is None or sub.symbolLayerCount() != 1:
            return None
        line = sub.symbolLayer(0)
        if not isinstance(line, QgsSimpleLineSymbolLayer) or _enum_int(line.penStyle()) != 1 \
                or self._active_ddp_names(line) or self._active_ddp_names(symbol_layer):
            return None

        def screen_px(value, unit, what):
            unit_name = normalize_unit(unit)
            if unit_name in ("map", "m"):
                self.context.report(
                    "Q2VT_PATTERN_MAP_UNITS",
                    f"Hatch {what} in map units is frozen at zoom "
                    f"{self.context.reference_zoom:g}.", strategy=Strategy.APPROXIMATE.value)
            return PropertyExtractor.static_pixels(value, unit)

        color = QColor(line.color())
        color.setAlphaF(color.alphaF() * sub.opacity())
        spec = LinePatternSpec(
            angle_deg=float(symbol_layer.lineAngle()),
            spacing_px=screen_px(symbol_layer.distance(), symbol_layer.distanceUnit(), "spacing"),
            line_width_px=screen_px(line.width(), line.widthUnit(), "width"),
            color_rgba=(color.red(), color.green(), color.blue(), color.alpha()),
            offset_px=screen_px(symbol_layer.offset(), symbol_layer.offsetUnit(), "offset"),
        )
        cell = solve_periodic_cell(spec, self.profile)
        if cell is None:
            return None
        if not cell.within_tolerance:
            self.context.report(
                "Q2VT_PATTERN_NONPERIODIC",
                f"Hatch {spec.angle_deg:g}°/{spec.spacing_px:.2f}px exported as "
                f"{cell.angle_deg:.2f}°/{cell.spacing_px:.2f}px "
                f"(cell {cell.size}px).", strategy=Strategy.APPROXIMATE.value)
        name = self._next_name("pattern")
        self.pattern_images[name] = PatternImages(
            render_line_pattern(spec, cell, 1), render_line_pattern(spec, cell, 2))
        return name

    def _font_marker_as_text(self, symbol_layer, source_layer_name: str) -> bool:
        """Whether browser text can draw the marker's characters: MapLibre
        glyph ranges stop at U+FFFF, and a character missing from the font
        is drawn by QGIS from a fallback font. Otherwise the marker becomes
        sprites (one per distinct data-defined character)."""
        from qgis.PyQt.QtGui import QFontMetrics  # pylint: disable=import-outside-toplevel
        texts = [symbol_layer.character()]
        props = symbol_layer.dataDefinedProperties()
        prop = props.property(QgsSymbolLayer.Property.PropertyCharacter)
        if prop is not None and prop.isActive():
            fields = sorted(ex.referenced_fields(PropertyExtractor.get_value_or_expression(
                symbol_layer.character(), prop, "string")))
            combos = self._distinct_values(source_layer_name, fields) if fields else []
            if combos is None:
                return True  # too many values for sprites
            texts += [str(v) for combo in combos for v in combo if v is not None]
        metrics = QFontMetrics(QFont(symbol_layer.fontFamily()))
        for text in texts:
            for char in text or "":
                if ord(char) > 0xFFFF or (not char.isspace()
                                          and not metrics.inFontUcs4(ord(char))):
                    self.context.report(
                        "Q2VT_FONT_MARKER_SPRITE",
                        f"Font marker character U+{ord(char):04X} cannot be drawn as browser "
                        "text; the marker is exported as images.",
                        strategy=Strategy.SPRITE.value)
                    return False
        return True

    def _convert_font_marker(self, symbol_layer, symbol, style_name, source_layer_name,
                             source_name, min_zoom, max_zoom):
        """Export a font marker as native browser text.

        Font markers draw one or more characters of a font; as text they stay
        crisp at every zoom, keep data-defined characters (e.g. zoning
        parameters read from attributes) and need no sprite per value. QGIS
        sets the font pixel size to the marker size and centres the string
        horizontally and on half its ascent vertically.
        """
        props = symbol_layer.dataDefinedProperties()
        layer_def = self._base_layer_def(
            "symbol", style_name, source_layer_name, source_name, min_zoom, max_zoom)
        char = PropertyExtractor.get_value_or_expression(
            symbol_layer.character(), props.property(QgsSymbolLayer.Property.PropertyCharacter),
            "string")
        font = QFont(symbol_layer.fontFamily())
        stack = GlyphGenerator.resolve_fontstack(symbol_layer.fontFamily(),
                                                 symbol_layer.fontStyle() or "")
        if stack is None:
            info = QFontInfo(font)
            stack = GlyphGenerator.resolve_fontstack(info.family(), info.styleName())
        if stack is None:
            self.context.report("Q2VT_FONT_UNRESOLVED",
                                f"Font marker font '{symbol_layer.fontFamily()}' is not installed.")
            stack = symbol_layer.fontFamily()
        dataset = join(self.utils_dir, f"{source_layer_name}.gpkg")
        fields = ex.referenced_fields(char)
        entry = (dataset, sorted(fields)[0]) if fields else (None, str(char))
        self.glyphs.setdefault(stack, []).append(entry)

        size = PropertyExtractor.length(
            symbol_layer.size(), symbol_layer.sizeUnit(),
            props.property(QgsSymbolLayer.Property.PropertySize),
            symbol_layer.sizeMapUnitScale())
        layout = {
            "text-field": char,
            "text-font": [stack],
            "text-size": size,
            "text-anchor": "center",
            "text-max-width": 999,
            "text-padding": 0,
            "text-allow-overlap": True,
            "text-ignore-placement": True,
            "text-rotation-alignment": "map",
            "text-pitch-alignment": "viewport",
            "text-rotate": IconPropertyExtractor.get_icon_rotate(symbol_layer=symbol_layer),
            "symbol-placement": "point",
            "visibility": "visible",
        }
        offset = symbol_layer.offset()
        if offset.x() or offset.y():
            static_size = size if not isinstance(size, list) or ex.is_zoom_curve(size) else \
                PropertyExtractor.length(symbol_layer.size(), symbol_layer.sizeUnit())
            dx = PropertyExtractor.length(offset.x(), symbol_layer.offsetUnit())
            dy = PropertyExtractor.length(offset.y(), symbol_layer.offsetUnit())
            ems = [ex.ratio(dx, static_size), ex.ratio(dy, static_size)]
            if all(ex.is_number(v) for v in ems):
                layout["text-offset"] = ems
            else:
                self.context.report("Q2VT_MIXED_UNITS",
                                    "Font marker offset and size use different unit families.")
        layer_def["layout"].update(layout)
        paint = {
            "text-color": PropertyExtractor.get_value_or_expression(
                PropertyExtractor.convert_qcolor_to_maplibre(symbol_layer.color()),
                props.property(QgsSymbolLayer.Property.PropertyFillColor), "color"),
            "text-opacity": PropertyExtractor.opacity(symbol, symbol_layer),
        }
        stroke = symbol_layer.strokeColor()
        if symbol_layer.strokeWidth() > 0 and stroke.alpha() > 0:
            width = PropertyExtractor.length(symbol_layer.strokeWidth(),
                                             symbol_layer.strokeWidthUnit())
            paint["text-halo-color"] = PropertyExtractor.convert_qcolor_to_maplibre(stroke)
            paint["text-halo-width"] = ex.mul(width, 0.5)  # stroke is centred on the outline
        layer_def["paint"].update(paint)
        self.style["layers"].append(layer_def)

    # --- sprite variants -----------------------------------------------------------
    MAX_VARIANTS = 64

    def _distinct_values(self, source_layer: str, fields: List[str]) -> Optional[list]:
        """Distinct value combinations of ``fields`` in an exported dataset
        (None when more than ``MAX_VARIANTS``)."""
        from osgeo import ogr  # pylint: disable=import-outside-toplevel
        path = join(self.utils_dir, f"{source_layer}.gpkg")
        dataset = ogr.Open(path)
        if dataset is None:
            return []
        layer = dataset.GetLayer(0)
        seen = []
        keys = set()
        for feature in layer:
            combo = tuple(feature.GetField(f) if feature.GetFieldIndex(f) >= 0 else None
                          for f in fields)
            if combo in keys:
                continue
            keys.add(combo)
            seen.append(combo)
            if len(seen) > self.MAX_VARIANTS:
                return None
        return seen

    def _variant_images(self, fields: List[str], combos: list, make_name) -> list:
        """``["match", key, value, name, ..., default]`` pieces for combos."""
        key = ex.get(fields[0]) if len(fields) == 1 else \
            ["concat"] + sum(([["to-string", ex.get(f)], "|"] for f in fields), [])[:-1]
        cases = []
        for combo in combos:
            if any(v is None for v in combo):
                continue
            value = str(combo[0]) if len(fields) == 1 else "|".join(str(v) for v in combo)
            cases += [value, make_name(combo)]
        return key, cases

    def _variant_fields(self, obj, excluded=()) -> List[str]:
        """Generated fields read by appearance-changing data-defined properties."""
        fields = set()
        try:
            props = obj.dataDefinedProperties()
        except (AttributeError, RuntimeError):
            return []
        for key in props.propertyKeys():
            prop = props.property(key)
            if not prop or not prop.isActive() or _property_name(obj, key).lower() in excluded:
                continue
            fields |= {c for c in QgsExpression(prop.asExpression()).referencedColumns()
                       if c.startswith(f"{_FIELD_PREFIX}_")}
        return sorted(fields)

    # --- screen-unit pattern textures -------------------------------------------
    def _reference_map_units_per_px(self) -> float:
        """Map units per CSS px at the component's reference zoom."""
        px_per_unit = PropertyExtractor.static_pixels(1.0, "map")
        return 1.0 / px_per_unit if px_per_unit else 1.0

    def _marker_images(self, marker: QgsSymbol):
        """Marker rendered at 1x and 2x for pasting into a texture cell."""
        from .sprite_generator import SymbolImage  # pylint: disable=import-outside-toplevel
        reference = self._reference_map_units_per_px()
        one = SymbolImage(marker, "pattern-marker", 1, True, reference).img
        two = SymbolImage(marker, "pattern-marker", 2, True, reference).img
        return one, two

    @staticmethod
    def _pattern_uses_map_units(layer) -> bool:
        units = []
        for getter in ("distanceXUnit", "distanceYUnit", "patternWidthUnit", "widthUnit",
                       "densityAreaUnit"):
            if hasattr(layer, getter):
                units.append(getattr(layer, getter)())
        sub = layer.subSymbol()
        if sub is not None:
            units += [sub.symbolLayer(i).outputUnit() for i in range(sub.symbolLayerCount())]
        return any(normalize_unit(unit) in ("map", "m") for unit in units)

    # Upper bound on per-zoom textures of one map-unit pattern.
    MAX_PATTERN_ZOOMS = 12

    def _per_zoom_pattern(self, register, min_zoom: float, max_zoom: float):
        """One texture per integer zoom for a pattern sized in map units.

        MapLibre textures keep their screen size, so each zoom band gets a
        texture rendered at the middle of the band (sizes stay within about
        ±41 % of QGIS inside the band); ``fill-pattern`` steps between them.
        """
        low = max(float(self.context.reference_zoom), float(min_zoom if min_zoom >= 0 else 0))
        top = float(max_zoom) if max_zoom is not None and max_zoom >= 0 else 24.0
        top = min(top, float(self.maxzoom) + 3.0, low + self.MAX_PATTERN_ZOOMS)
        saved = self.context.reference_zoom
        self._pattern_zoom_bands = True
        stops = []
        try:
            zoom = low
            while zoom < top - 1e-9:
                upper = min(math.floor(zoom) + 1.0, top)
                self.context.reference_zoom = (zoom + upper) / 2.0
                name = register()
                if name is None:
                    return None
                stops.append((zoom, name))
                zoom = upper
        finally:
            self.context.reference_zoom = saved
            self._pattern_zoom_bands = False
        if not stops:
            return None
        self.context.report(
            "Q2VT_PATTERN_MAP_UNITS",
            "Pattern in map units drawn with one texture per zoom level (sizes within "
            "about ±41 % of QGIS between integer zooms).", strategy=Strategy.APPROXIMATE.value)
        if len(stops) == 1:
            return stops[0][1]
        expr: List[Any] = ["step", ["zoom"], stops[0][1]]
        for zoom, name in stops[1:]:
            expr += [zoom, name]
        return expr

    _pattern_zoom_bands = False

    def _pattern_px(self, value, unit, what: str) -> float:
        if normalize_unit(unit) in ("map", "m") and value and not self._pattern_zoom_bands:
            self.context.report(
                "Q2VT_PATTERN_MAP_UNITS",
                f"Pattern {what} in map units is frozen at zoom {self.context.reference_zoom:g}.",
                strategy=Strategy.APPROXIMATE.value)
        return PropertyExtractor.static_pixels(value, unit) if value else 0.0

    def _textures(self, cell_1x, cell_2x, error: float, what: str) -> str:
        if error > self.profile.tolerance_rel and error > 0:
            self.context.report(
                "Q2VT_PATTERN_NONPERIODIC",
                f"{what}: spacing rounded to whole pixels changes it by {error:.1%}.",
                strategy=Strategy.APPROXIMATE.value)
        name = self._next_name("pattern")
        self.pattern_images[name] = PatternImages(cell_1x, cell_2x)
        return name

    def _register_point_pattern(self, layer) -> Optional[str]:
        """Seamless texture for a point pattern spaced in screen units."""
        from .fidelity.patterns import point_pattern_cell, tile_markers  # pylint: disable=import-outside-toplevel
        marker = layer.subSymbol()
        if marker is None:
            return None
        if layer.maximumRandomDeviationX() or layer.maximumRandomDeviationY() or layer.angle():
            self.context.report("Q2VT_PATTERN_APPROXIMATE",
                                "Random deviation or rotation of pattern markers is ignored.",
                                strategy=Strategy.APPROXIMATE.value)
        dx = self._pattern_px(layer.distanceX(), layer.distanceXUnit(), "spacing")
        dy = self._pattern_px(layer.distanceY(), layer.distanceYUnit(), "spacing")
        if dx <= 0 or dy <= 0:
            return None
        disp_x = self._pattern_px(layer.displacementX(), layer.displacementXUnit(), "displacement")
        disp_y = self._pattern_px(layer.displacementY(), layer.displacementYUnit(), "displacement")
        one, two = self._marker_images(marker)
        cells = []
        for ratio, image in ((1, one), (2, two)):
            width, height, positions, error = point_pattern_cell(
                dx * ratio, dy * ratio, disp_x * ratio, disp_y * ratio)
            cells.append(tile_markers(image, width, height, positions))
        _, _, _, error = point_pattern_cell(dx, dy, disp_x, disp_y)
        return self._textures(cells[0], cells[1], error, "Point pattern")

    def _register_random_pattern(self, layer) -> Optional[str]:
        """Seamless texture of a random marker fill at its QGIS density
        (``count`` markers per ``densityArea``, in screen units as QGIS
        counts them). Absolute counts depend on the feature: no texture."""
        import random  # pylint: disable=import-outside-toplevel
        from .fidelity.patterns import tile_markers  # pylint: disable=import-outside-toplevel
        marker = layer.subSymbol()
        if marker is None or _enum_int(layer.countMethod()) != \
                _enum_int(Qgis.PointCountMethod.DensityBased):
            return None
        side = self._pattern_px(math.sqrt(max(layer.densityArea(), 0.0)),
                                layer.densityAreaUnit(), "density area")
        if side <= 0 or layer.pointCount() <= 0:
            return None
        per_px = layer.pointCount() / side ** 2
        # Large enough that the repetition is not obvious: 128 px, or a few
        # dozen markers for sparse fills (at most 512 px).
        cell = int(min(512, max(128, math.ceil(math.sqrt(40 / per_px)))))
        count = max(1, round(per_px * cell * cell))
        rng = random.Random(int(layer.seed()) or 1)
        positions = [(rng.uniform(0, cell), rng.uniform(0, cell)) for _ in range(count)]
        one, two = self._marker_images(marker)
        cells = [tile_markers(one, cell, cell, positions),
                 tile_markers(two, 2 * cell, 2 * cell, [(2 * x, 2 * y) for x, y in positions])]
        error = abs(count - per_px * cell * cell) / (per_px * cell * cell)
        return self._textures(cells[0], cells[1], error, "Random marker fill")

    def _register_svg_pattern(self, layer) -> Optional[str]:
        """Seamless texture for an SVG fill: one SVG per cell, as QGIS tiles it."""
        from qgis.core import QgsSvgMarkerSymbolLayer, QgsMarkerSymbol, QgsApplication  # pylint: disable=import-outside-toplevel
        from .fidelity.patterns import tile_markers  # pylint: disable=import-outside-toplevel
        width = self._pattern_px(layer.patternWidth(), layer.patternWidthUnit(), "width")
        if width <= 0 or not layer.svgFilePath():
            return None
        box = QgsApplication.svgCache().svgViewboxSize(
            layer.svgFilePath(), 100, layer.svgFillColor(), layer.svgStrokeColor(),
            layer.svgStrokeWidth(), 1.0)
        aspect = box.height() / box.width() if box.width() > 0 else 1.0
        marker_layer = QgsSvgMarkerSymbolLayer(layer.svgFilePath(), layer.patternWidth(), layer.angle())
        marker_layer.setSizeUnit(layer.patternWidthUnit())
        marker_layer.setFillColor(layer.svgFillColor())
        marker_layer.setStrokeColor(layer.svgStrokeColor())
        marker_layer.setStrokeWidth(layer.svgStrokeWidth())
        marker_layer.setStrokeWidthUnit(layer.svgStrokeWidthUnit())
        one, two = self._marker_images(QgsMarkerSymbol([marker_layer]))
        cells = []
        for ratio, image in ((1, one), (2, two)):
            cell_w = max(1, round(width * ratio))
            cell_h = max(1, round(width * aspect * ratio))
            cells.append(tile_markers(image, cell_w, cell_h, [(cell_w / 2.0, cell_h / 2.0)]))
        error = abs(round(width) - width) / width
        return self._textures(cells[0], cells[1], error, "SVG fill")

    def _register_raster_pattern(self, layer) -> Optional[str]:
        """Texture for a raster image fill (local file or embedded image)."""
        from qgis.core import QgsApplication  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtCore import QSize  # pylint: disable=import-outside-toplevel
        from .sprite_generator import SymbolImage  # pylint: disable=import-outside-toplevel
        path = layer.imageFilePath()
        if not path:
            return None
        width = self._pattern_px(layer.width(), layer.widthUnit(), "width") if layer.width() else 0
        cells = []
        for ratio in (1, 2):
            size = QSize(round(width * ratio), 0) if width else QSize(0, 0)
            image, _ = QgsApplication.imageCache().pathAsImage(path, size, True, layer.opacity(), True)
            if image.isNull():
                self.context.report("Q2VT_SPRITE_RENDER_FAILED",
                                    "Raster fill image could not be loaded.", detail=path)
                return None
            cells.append(SymbolImage._qt_to_pil(image))  # pylint: disable=protected-access
        return self._textures(cells[0], cells[1], 0.0, "Raster fill")

    _SPRITE_INDEPENDENT = frozenset({"size", "angle", "opacity", "layerenabled"})

    def _marker_variants(self, symbol, marker_name, source_layer, map_units_per_pixel,
                         oversampling):
        """``icon-image``: one sprite per distinct data-defined appearance.

        Size, angle and opacity are applied by the style; every other
        data-defined property (colours, SVG path, shape, stroke width...)
        changes the image, so each distinct value combination in the data is
        rendered by QGIS with those attribute values.
        """
        fields = sorted({f for i in range(symbol.symbolLayerCount())
                         for f in self._variant_fields(symbol.symbolLayer(i),
                                                       self._SPRITE_INDEPENDENT)})
        if not fields:
            return marker_name
        combos = self._distinct_values(source_layer, fields)
        if combos is None:
            self.context.report(
                "Q2VT_SPRITE_VARIANTS_BUDGET",
                f"More than {self.MAX_VARIANTS} distinct data-defined appearances; "
                "the static symbol is used for all features.")
            return marker_name
        names = {}

        def make(combo):
            name = f"{marker_name}_v{len(names)}"
            names[combo] = name
            self.marker_symbols[name] = SpriteRequest(
                symbol.clone(), bake_rotation=False, map_units_per_pixel=map_units_per_pixel,
                attributes=dict(zip(fields, combo)), oversampling=oversampling)
            return name
        key, cases = self._variant_images(fields, combos, make)
        if not cases:
            return marker_name
        return ["match", key] + cases + [marker_name]

    # Data-defined properties a native circle reproduces exactly.
    _CIRCLE_DDP = frozenset({"Size", "Angle", "Opacity", "FillColor", "Color", "StrokeColor",
                             "StrokeWidth", "LayerEnabled"})

    def _native_circle(self, symbol, style_name, source_layer_name, source_name,
                       min_zoom, max_zoom) -> bool:
        """Emit a ``circle`` layer for a plain circular simple marker.

        Eligible: one ``SimpleMarker`` layer of shape circle, solid or no
        stroke, no offset or paint effect, and only data-defined size,
        colours, stroke width and opacity. QGIS centres the stroke on the
        circle's edge while MapLibre draws it outside ``circle-radius``, so
        the radius is ``(size - stroke) / 2``. A translucent stroke over a
        visible fill would show the fill under the inner half of the QGIS
        stroke, so that case stays a sprite. Returns False when the symbol
        is not eligible (it is then rendered as a sprite).
        """
        if symbol.symbolLayerCount() != 1:
            return False
        layer = symbol.symbolLayer(0)
        if layer.layerType() != "SimpleMarker":
            return False
        try:
            if layer.shape() != Qgis.MarkerShape.Circle:
                return False
            offset = layer.offset()
            if offset.x() or offset.y():
                return False
            pen = layer.strokeStyle()
        except (AttributeError, RuntimeError):
            return False
        if pen not in (Qt.PenStyle.SolidLine, Qt.PenStyle.NoPen):
            return False
        effect = layer.paintEffect()
        if effect is not None and effect.enabled() and type(effect).__name__ != "QgsDefaultPaintEffect":
            stack = getattr(effect, "effectList", lambda: [])()
            if not (type(effect).__name__ == "QgsEffectStack" and all(
                    type(e).__name__ == "QgsDrawSourceEffect" for e in stack)):
                return False
        names = set(self._active_ddp_names(layer))
        if names - self._CIRCLE_DDP or self._active_ddp_names(symbol) not in ([], ["Opacity"]):
            return False
        props = layer.dataDefinedProperties()
        P = QgsSymbolLayer.Property
        stroke_color = layer.strokeColor()
        has_stroke = pen != Qt.PenStyle.NoPen and (
            stroke_color.alpha() > 0 or props.property(P.PropertyStrokeColor).isActive())
        opacity = PropertyExtractor.opacity(symbol, layer)
        if has_stroke and layer.color().alpha() > 0 and (
                stroke_color.alpha() < 255 or not ex.is_number(opacity) or opacity < 1
                or props.property(P.PropertyStrokeColor).isActive()):
            return False

        lengths = PropertyExtractor.length
        size = lengths(layer.size(), layer.sizeUnit(), props.property(P.PropertySize),
                       layer.sizeMapUnitScale())
        stroke = 0.0
        if has_stroke:
            width = layer.strokeWidth()
            width_prop = props.property(P.PropertyStrokeWidth)
            if width > 0 or width_prop.isActive():
                stroke = lengths(width, layer.strokeWidthUnit(), width_prop,
                                 layer.strokeWidthMapUnitScale())
            else:
                stroke = 1.0  # QGIS draws a zero width as a one-pixel hairline
        try:
            radius = ex.clamp(ex.mul(ex.add(size, ex.mul(stroke, -1.0)), 0.5), 0, None)
        except ex.ExpressionError:
            return False

        fill_color = PropertyExtractor.get_value_or_expression(
            PropertyExtractor.convert_qcolor_to_maplibre(layer.color()),
            props.property(P.PropertyFillColor), "color")
        layer_def = self._base_layer_def("circle", style_name, source_layer_name, source_name,
                                         min_zoom, max_zoom)
        layer_def["layout"]["visibility"] = "visible"
        paint = layer_def["paint"]
        paint.update({
            "circle-radius": radius,
            "circle-color": fill_color,
            "circle-opacity": opacity,
            "circle-pitch-alignment": "map",
            "circle-pitch-scale": "map",
        })
        if has_stroke:
            paint.update({
                "circle-stroke-width": stroke,
                "circle-stroke-color": PropertyExtractor.get_value_or_expression(
                    PropertyExtractor.convert_qcolor_to_maplibre(stroke_color),
                    props.property(P.PropertyStrokeColor), "color"),
                "circle-stroke-opacity": opacity,
            })
        self.style["layers"].append(layer_def)
        return True

    def _convert_marker_symbol(
        self,
        symbol_layer: QgsSymbolLayer,
        symbol: QgsSymbol,
        style_name: str,
        source_layer_name: str,
        source_name: str,
        min_zoom: float = -1,
        max_zoom: float = -1,
    ):
        """Convert a QGIS marker symbol into a MapLibre ``symbol`` layer."""
        layer_def = self._base_layer_def(
            "symbol", style_name, source_layer_name, source_name, min_zoom, max_zoom
        )

        marker_name = self._next_name("marker")
        # Rotation is applied once, by icon-rotate; the sprite is unrotated.
        icon_size, map_units_per_pixel, oversampling = IconPropertyExtractor.marker_scale(
            symbol, symbol_layer)
        self.marker_symbols[marker_name] = SpriteRequest(
            symbol.clone(), bake_rotation=False, map_units_per_pixel=map_units_per_pixel,
            oversampling=oversampling)
        icon_image = self._marker_variants(symbol, marker_name, source_layer_name,
                                           map_units_per_pixel, oversampling)

        layer_def["layout"].update({
            "icon-image": icon_image,
            "icon-size": icon_size,
            "icon-rotate": IconPropertyExtractor.get_icon_rotate(symbol_layer=symbol_layer),
            "icon-padding": IconPropertyExtractor.get_icon_padding(),
            "icon-rotation-alignment": IconPropertyExtractor.get_icon_rotation_alignment(),
            "icon-pitch-alignment": IconPropertyExtractor.get_icon_pitch_alignment(),
            "icon-anchor": IconPropertyExtractor.get_icon_anchor(),
            "icon-allow-overlap": IconPropertyExtractor.get_icon_allow_overlap(True),
            "icon-ignore-placement": IconPropertyExtractor.get_icon_ignore_placement(),
            "icon-optional": IconPropertyExtractor.get_icon_optional(),
            "icon-keep-upright": IconPropertyExtractor.get_icon_keep_upright(),
            "symbol-placement": IconPropertyExtractor.get_symbol_placement(),
            "symbol-spacing": IconPropertyExtractor.get_symbol_spacing(),
            "symbol-avoid-edges": IconPropertyExtractor.get_symbol_avoid_edges(),
            "symbol-sort-key": IconPropertyExtractor.get_symbol_sort_key(),
            "symbol-z-order": IconPropertyExtractor.get_symbol_z_order(),
            "visibility": "visible",
        })
        layer_def["paint"].update({
            "icon-opacity": IconPropertyExtractor.get_icon_opacity(),
            "icon-halo-color": IconPropertyExtractor.get_icon_halo_color(),
            "icon-halo-width": IconPropertyExtractor.get_icon_halo_width(),
            "icon-halo-blur": IconPropertyExtractor.get_icon_halo_blur(),
            "icon-translate": IconPropertyExtractor.get_icon_translate(),
            "icon-translate-anchor": IconPropertyExtractor.get_icon_translate_anchor(),
        })

        self.style["layers"].append(layer_def)

    def _convert_line_symbol(
        self,
        symbol: QgsSymbol,
        style_name: str,
        source_layer_name: str,
        source_name: str,
        min_zoom: float = -1,
        max_zoom: float = -1,
    ):
        """Convert every symbol layer of a QGIS line symbol into MapLibre layer(s).

        Sub-layers are appended bottom-to-top in QGIS symbol-layer order.
        """
        for index in range(symbol.symbolLayerCount()):
            symbol_layer = symbol.symbolLayer(index)
            if self._classify(symbol_layer, index) == Strategy.UNSUPPORTED:
                continue
            layer_id = style_name if index == 0 else f"{style_name}_layer{index}"

            if LinePropertyExtractor.is_marker_line(symbol_layer):
                self._convert_marker_line_symbol_layer(
                    symbol_layer, layer_id, source_layer_name, source_name,
                    min_zoom, max_zoom,
                )
            else:
                self._convert_simple_or_pattern_line_symbol_layer(
                    symbol_layer, symbol, layer_id, source_layer_name, source_name,
                    min_zoom, max_zoom,
                )

    def _convert_marker_line_symbol_layer(
        self,
        symbol_layer: QgsSymbolLayer,
        style_name: str,
        source_layer_name: str,
        source_name: str,
        min_zoom: float = -1,
        max_zoom: float = -1,
    ):
        """Convert a QGIS ``QgsMarkerLineSymbolLayer`` into a MapLibre ``symbol`` layer.

        The marker sub-symbol is rendered to a sprite (with its own angle
        baked in, since MapLibre rotates it by the line bearing only);
        placements are mapped by named flags, and the perpendicular offset
        is reproduced through ``icon-offset``.
        """
        sub_symbol = None
        try:
            sub_symbol = symbol_layer.subSymbol()
        except (AttributeError, RuntimeError):
            pass
        if sub_symbol is None or sub_symbol.symbolLayerCount() == 0:
            return

        layer_def = self._base_layer_def(
            "symbol", style_name, source_layer_name, source_name, min_zoom, max_zoom
        )

        marker_name = self._next_name("marker")
        marker_sub_layer = sub_symbol.symbolLayer(0)
        icon_size, map_units_per_pixel, oversampling = IconPropertyExtractor.marker_scale(
            sub_symbol, marker_sub_layer)
        self.marker_symbols[marker_name] = SpriteRequest(
            sub_symbol.clone(), bake_rotation=True, map_units_per_pixel=map_units_per_pixel,
            oversampling=oversampling)
        rotate_with_line = LinePropertyExtractor.get_marker_line_rotate_symbols(symbol_layer)
        offset_px = LinePropertyExtractor.get_marker_line_offset(symbol_layer)

        layer_def["layout"].update({
            "icon-image": IconPropertyExtractor.get_icon_image(marker_name),
            "icon-size": icon_size,
            "icon-rotate": 0,
            "icon-padding": IconPropertyExtractor.get_icon_padding(),
            # Following the line's bearing ("map") reproduces rotateSymbols()
            # == True; "viewport" keeps markers upright regardless of the
            # line's direction, matching rotateSymbols() == False.
            "icon-rotation-alignment": "map" if rotate_with_line else "viewport",
            "icon-pitch-alignment": IconPropertyExtractor.get_icon_pitch_alignment(),
            "icon-anchor": IconPropertyExtractor.get_icon_anchor(),
            "icon-allow-overlap": IconPropertyExtractor.get_icon_allow_overlap(True),
            # QGIS marker lines stamp every marker unconditionally; both flags
            # are needed to disable MapLibre collision-based hiding.
            "icon-ignore-placement": True,
            "icon-optional": IconPropertyExtractor.get_icon_optional(),
            "icon-keep-upright": False,
            "symbol-placement": LinePropertyExtractor.get_marker_line_symbol_placement(symbol_layer),
            "symbol-spacing": LinePropertyExtractor.get_marker_line_spacing(symbol_layer),
            "symbol-avoid-edges": IconPropertyExtractor.get_symbol_avoid_edges(),
            "symbol-sort-key": IconPropertyExtractor.get_symbol_sort_key(),
            "symbol-z-order": IconPropertyExtractor.get_symbol_z_order(),
            "visibility": "visible",
        })
        if offset_px:
            layer_def["layout"]["icon-offset"] = self._icon_offset(offset_px, icon_size)

        layer_def["paint"].update({
            "icon-opacity": IconPropertyExtractor.get_icon_opacity(),
            "icon-halo-color": IconPropertyExtractor.get_icon_halo_color(),
            "icon-halo-width": IconPropertyExtractor.get_icon_halo_width(),
            "icon-halo-blur": IconPropertyExtractor.get_icon_halo_blur(),
            "icon-translate": IconPropertyExtractor.get_icon_translate(),
            "icon-translate-anchor": IconPropertyExtractor.get_icon_translate_anchor(),
        })

        self.style["layers"].append(layer_def)

    @staticmethod
    def _icon_offset(offset_px, icon_size):
        """``icon-offset`` ``[0, y]``: offsets are multiplied by icon-size.

        Map-unit offsets over map-unit icons give a constant; otherwise the
        ratio is sampled per zoom. A data-defined icon-size uses its static
        base (MapLibre offsets cannot depend on features and zoom at once).
        """
        if isinstance(icon_size, list) and not ex.is_zoom_curve(icon_size):
            icon_size = 1.0
        elif ex.is_zoom_curve(icon_size) and any(
                not ex.is_number(o) for o in icon_size[4::2]):
            icon_size = 1.0
        value = ex.ratio(offset_px, icon_size)
        if ex.is_number(value):
            return [0, value]
        out = ["step", ["zoom"], ["literal", [0, value[2]]]]
        for zoom, item in zip(value[3::2], value[4::2]):
            out.extend([zoom, ["literal", [0, item]]])
        return out

    def _convert_simple_or_pattern_line_symbol_layer(
        self,
        symbol_layer: QgsSymbolLayer,
        symbol: QgsSymbol,
        style_name: str,
        source_layer_name: str,
        source_name: str,
        min_zoom: float = -1,
        max_zoom: float = -1,
    ):
        """Convert a plain-stroke or raster-pattern line symbol layer to MapLibre ``line``."""
        layer_def = self._base_layer_def(
            "line", style_name, source_layer_name, source_name, min_zoom, max_zoom
        )

        if isinstance(symbol_layer, QgsSimpleLineSymbolLayer):
            if _enum_int(symbol_layer.penStyle()) == 0:  # Qt.NoPen: draws nothing
                return
            layer_def["paint"].update({
                "line-color": LinePropertyExtractor.get_line_color(symbol_layer),
                "line-width": LinePropertyExtractor.get_line_width(symbol_layer),
                "line-opacity": LinePropertyExtractor.get_line_opacity(symbol_layer, symbol),
                "line-blur": LinePropertyExtractor.get_line_blur(),
                "line-gap-width": LinePropertyExtractor.get_line_gap_width(),
                "line-translate": LinePropertyExtractor.get_line_translate(),
                "line-translate-anchor": LinePropertyExtractor.get_line_translate_anchor(),
            })

            offset = LinePropertyExtractor.get_line_offset(symbol_layer)
            if isinstance(offset, list) or (ex.is_number(offset) and offset != 0):
                layer_def["paint"]["line-offset"] = offset

            width_value = layer_def["paint"]["line-width"]
            width_px = width_value if ex.is_number(width_value) else \
                PropertyExtractor.static_pixels(symbol_layer.width(), symbol_layer.widthUnit())
            dasharray = LinePropertyExtractor.get_line_dasharray(symbol_layer, width_px)
            if dasharray:
                layer_def["paint"]["line-dasharray"] = dasharray

            layer_def["layout"].update({
                "line-cap": LinePropertyExtractor.get_line_cap(symbol_layer),
                "line-join": LinePropertyExtractor.get_line_join(symbol_layer),
                "line-miter-limit": LinePropertyExtractor.get_line_miter_limit(),
                "line-round-limit": LinePropertyExtractor.get_line_round_limit(),
                "visibility": "visible",
            })
        elif LinePropertyExtractor.is_pattern_line(symbol_layer):
            self.context.report("Q2VT_PATTERN_APPROXIMATE",
                                f"{symbol_layer.layerType()} exported from a symbol preview.",
                                strategy=Strategy.APPROXIMATE.value)
            pattern_name = self._register_pattern(symbol_layer)
            layer_def["paint"]["line-pattern"] = pattern_name
            layer_def["paint"].update({
                "line-opacity": PropertyExtractor.opacity(symbol),
                "line-translate": LinePropertyExtractor.get_line_translate(),
                "line-translate-anchor": LinePropertyExtractor.get_line_translate_anchor(),
            })
            layer_def["layout"].update({
                "line-cap": "round",
                "line-join": "round",
                "line-miter-limit": LinePropertyExtractor.get_line_miter_limit(),
                "line-round-limit": LinePropertyExtractor.get_line_round_limit(),
                "visibility": "visible",
            })
        else:
            self.context.report("Q2VT_UNSUPPORTED_SYMBOL_LAYER",
                                f"{symbol_layer.layerType()} has no line conversion.")
            return

        self.style["layers"].append(layer_def)

    def _convert_fill_symbol(
        self,
        symbol_layer: QgsSymbolLayer,
        symbol: QgsSymbol,
        style_name: str,
        source_layer_name: str,
        source_name: str,
        min_zoom: float = -1,
        max_zoom: float = -1,
    ):
        """Convert a QGIS fill symbol into a MapLibre ``fill`` layer."""
        layer_def = self._base_layer_def(
            "fill", style_name, source_layer_name, source_name, min_zoom, max_zoom
        )

        if isinstance(symbol_layer, QgsSimpleFillSymbolLayer):
            if symbol_layer.brushStyle() != Qt.BrushStyle.NoBrush:
                if _enum_int(symbol_layer.brushStyle()) != 1:  # not Qt.SolidPattern
                    self.context.report(
                        "Q2VT_PATTERN_APPROXIMATE",
                        "Qt brush patterns are drawn as a solid fill.",
                        strategy=Strategy.APPROXIMATE.value)
                layer_def["paint"].update({
                    "fill-color": FillPropertyExtractor.get_fill_color(symbol_layer),
                    "fill-opacity": FillPropertyExtractor.get_fill_opacity(symbol_layer, symbol),
                    "fill-antialias": FillPropertyExtractor.get_fill_antialias(),
                    "fill-translate": FillPropertyExtractor.get_fill_translate(),
                    "fill-translate-anchor": FillPropertyExtractor.get_fill_translate_anchor(),
                })

            else:
                layer_def["paint"].update({
                    "fill-color": "rgba(0, 0, 0, 0.0)"})
            if symbol_layer.strokeStyle() != Qt.PenStyle.NoPen:
                outline_color = FillPropertyExtractor.get_fill_outline_color(symbol_layer)
                if outline_color:
                    layer_def["paint"]["fill-outline-color"] = outline_color

                layer_def["layout"].update({
                    "fill-sort-key": FillPropertyExtractor.get_fill_sort_key(symbol_layer),
                    "visibility": "visible",
                })
        elif FillPropertyExtractor.is_pattern_fill(symbol_layer):
            kind = symbol_layer.layerType()

            def register():
                if kind == "LinePatternFill":
                    return self._register_line_pattern(symbol_layer, symbol)
                if kind == "PointPatternFill":
                    return self._register_point_pattern(symbol_layer)
                if kind == "SVGFill":
                    return self._register_svg_pattern(symbol_layer)
                if kind == "RasterFill":
                    return self._register_raster_pattern(symbol_layer)
                if kind == "RandomMarkerFill":
                    return self._register_random_pattern(symbol_layer)
                return None
            if kind != "LinePatternFill" and self._pattern_uses_map_units(symbol_layer):
                pattern_name = self._per_zoom_pattern(register, min_zoom, max_zoom)
            else:
                pattern_name = register()
            if pattern_name is None:
                self.context.report(
                    "Q2VT_PATTERN_APPROXIMATE",
                    f"{symbol_layer.layerType()} exported from a cropped symbol preview; "
                    "its repeat period is not verified.",
                    strategy=Strategy.APPROXIMATE.value)
                pattern_name = self._register_pattern(symbol_layer)
            layer_def["paint"].update({
                "fill-pattern": pattern_name,
                "fill-opacity": FillPropertyExtractor.get_fill_opacity(symbol_layer, symbol),
                "fill-antialias": FillPropertyExtractor.get_fill_antialias(),
                "fill-translate": FillPropertyExtractor.get_fill_translate(),
                "fill-translate-anchor": FillPropertyExtractor.get_fill_translate_anchor(),
            })
            layer_def["layout"].update({
                "fill-sort-key": FillPropertyExtractor.get_fill_sort_key(symbol_layer),
                "visibility": "visible",
            })
        else:
            # Never emit an empty fill layer: MapLibre would draw it black.
            self.context.report("Q2VT_UNSUPPORTED_SYMBOL_LAYER",
                                f"{symbol_layer.layerType()} has no fill conversion.")
            return

        self.style["layers"].append(layer_def)

    def _convert_label(
        self,
        label_settings: QgsPalLayerSettings,
        style_name: str,
        source_layer_name: str,
        source_name: str,
        min_zoom: float = -1,
        max_zoom: float = -1,
    ):
        """Convert a ``QgsPalLayerSettings`` into a MapLibre ``symbol`` layer."""
        layer_def = self._base_layer_def(
            "symbol", style_name, source_layer_name, source_name, min_zoom, max_zoom
        )

        text_format = label_settings.format()

        text_field = TextPropertyExtractor.get_text_field(label_settings)
        if text_field:
            layer_def["layout"]["text-field"] = text_field
        font = TextPropertyExtractor.get_text_font(text_format)
        dataset = join(self.utils_dir, f'{source_layer_name}.gpkg')
        if self.glyphs.get(font):
            self.glyphs[font].append(dataset)
        else:
            self.glyphs[font] = [dataset]
        text_size = TextPropertyExtractor.get_text_size(text_format, label_settings, self.viewer)
        em_size = text_size if ex.is_number(text_size) else \
            PropertyExtractor.static_pixels(text_format.size(), text_format.sizeUnit())
        placement = TextPropertyExtractor.placement_name(label_settings)
        layer_def["layout"].update({
            "text-font": [font],
            "text-size": text_size,
            "text-anchor": TextPropertyExtractor.get_text_anchor(label_settings),
            "text-justify": TextPropertyExtractor.get_text_justify(label_settings),
            "text-allow-overlap": TextPropertyExtractor.get_text_allow_overlap(label_settings),
            "text-ignore-placement": TextPropertyExtractor.get_text_ignore_placement(),
            "text-optional": TextPropertyExtractor.get_text_optional(),
            "text-padding": TextPropertyExtractor.get_text_padding(),
            "text-line-height": TextPropertyExtractor.get_text_line_height(),
            "text-letter-spacing": TextPropertyExtractor.get_text_letter_spacing(),
            "text-transform": TextPropertyExtractor.get_text_transform(label_settings),
            "text-max-width": TextPropertyExtractor.get_text_max_width(label_settings),
            "text-max-angle": TextPropertyExtractor.get_text_max_angle(label_settings),
            "text-keep-upright": TextPropertyExtractor.get_text_keep_upright(),
            "text-rotate": TextPropertyExtractor.get_text_rotate(label_settings),
            "text-rotation-alignment": TextPropertyExtractor.get_text_rotation_alignment(),
            "text-pitch-alignment": TextPropertyExtractor.get_text_pitch_alignment(),
            "symbol-placement": IconPropertyExtractor.get_symbol_placement(label_settings),
            "symbol-spacing": IconPropertyExtractor.get_symbol_spacing(label_settings),
            "symbol-avoid-edges": IconPropertyExtractor.get_symbol_avoid_edges(),
            "symbol-sort-key": IconPropertyExtractor.get_symbol_sort_key(),
            "symbol-z-order": IconPropertyExtractor.get_symbol_z_order(),
            "visibility": "visible",
        })

        # Emit only the placement properties that apply to this placement
        # mode; MapLibre lets some of them override each other.
        variable_anchor = TextPropertyExtractor.get_text_variable_anchor(label_settings)
        pinned = self._is_pinned(label_settings)
        if pinned:
            # Data-defined position: the label's alignment point sits on the
            # exported point; offsets and candidate positions do not apply.
            # Always shown, so a callout leader never outlives its label.
            layer_def["layout"].update({
                "symbol-placement": "point",
                "text-anchor": self._pinned_anchor(label_settings),
                "text-allow-overlap": True,
            })
        elif variable_anchor:
            layer_def["layout"]["text-variable-anchor"] = variable_anchor
            layer_def["layout"]["text-radial-offset"] = \
                TextPropertyExtractor.get_text_radial_offset(label_settings, em_size)
        elif placement == "OverPoint":
            offset = TextPropertyExtractor.get_text_offset(label_settings, em_size)
            if offset != [0, 0]:
                layer_def["layout"]["text-offset"] = offset

        layer_def["paint"].update({
            "text-color": TextPropertyExtractor.get_text_color(text_format, label_settings),
            "text-opacity": TextPropertyExtractor.get_text_opacity(text_format, label_settings),
            "text-halo-color": TextPropertyExtractor.get_text_halo_color(
                text_format, label_settings
            ),
            "text-halo-width": TextPropertyExtractor.get_text_halo_width(text_format, label_settings),
            "text-translate": TextPropertyExtractor.get_text_translate(),
            "text-translate-anchor": TextPropertyExtractor.get_text_translate_anchor(),
        })

        # Deep-copy label settings to avoid mutating the caller's object.
        label_settings = QgsPalLayerSettings(label_settings)
        label_format = QgsTextFormat(label_settings.format())
        background = QgsTextBackgroundSettings(label_format.background())
        if background.markerSymbol():
            background.setMarkerSymbol(background.markerSymbol().clone())
        label_format.setBackground(background)
        label_settings.setFormat(label_format)

        if background.enabled():
            self._apply_icon_from_background(layer_def, background, style_name, label_settings)
        else:
            self._apply_default_icon_props(layer_def)

        self.style["layers"].extend(self._line_label_zoom_split(layer_def))

    # MapLibre checks that a line label fits along its line with the
    # text-size evaluated at zoom 18 (symbol_layout.ts, textMaxSize), whatever
    # the tile zoom; map-unit text doubles per zoom, so below z18 labels that
    # fit are dropped.
    LINE_LABEL_FIT_ZOOM = 18

    @classmethod
    def _line_label_zoom_split(cls, layer_def: dict) -> list:
        """A line label with a zoom-curve ``text-size`` as one style layer per
        integer zoom below 18, each with the curve clamped to its own zoom
        range (the sizes it renders are unchanged; the zoom-18 fit check then
        uses the largest size the layer draws)."""
        layout = layer_def["layout"]
        size = layout.get("text-size")
        if layout.get("symbol-placement") not in ("line", "line-center") \
                or not ex.is_zoom_curve(size) or size[0] != "interpolate":
            return [layer_def]
        try:
            values = {z: ex.evaluate_zoom_curve(size, z) for z in range(0, 25)}
        except ex.ExpressionError:
            return [layer_def]
        low = layer_def.get("minzoom", 0)
        high = layer_def.get("maxzoom", 24)
        top = cls.LINE_LABEL_FIT_ZOOM
        if low >= top:
            return [layer_def]
        out = []
        for zoom in range(int(math.floor(low)), min(int(math.ceil(high)), top)):
            part = copy.deepcopy(layer_def)
            part["id"] = f"{layer_def['id']}_z{zoom}"
            part["minzoom"] = max(low, zoom)
            part["maxzoom"] = min(high, zoom + 1)
            part["layout"]["text-size"] = ["interpolate", list(size[1]), ["zoom"],
                                           zoom, values[zoom], zoom + 1, values[zoom + 1]]
            out.append(part)
        if high > top:
            rest = copy.deepcopy(layer_def)
            rest["minzoom"] = max(low, top)
            out.append(rest)
        return out

    @staticmethod
    def _is_pinned(label_settings) -> bool:
        props = label_settings.dataDefinedProperties()
        P = QgsPalLayerSettings.Property
        x_prop, y_prop = props.property(P.PositionX), props.property(P.PositionY)
        return bool(x_prop and y_prop and x_prop.isActive() and y_prop.isActive())

    _HALI = {"left": "left", "center": "", "right": "right"}
    _VALI = {"bottom": "bottom", "base": "bottom", "half": "", "cap": "top", "top": "top"}

    @classmethod
    def _anchor_name(cls, hali: str, vali: str) -> str:
        vertical = cls._VALI.get(str(vali).lower(), "bottom")
        horizontal = cls._HALI.get(str(hali).lower(), "left")
        return "-".join(p for p in (vertical, horizontal) if p) or "center"

    def _pinned_anchor(self, label_settings):
        """``text-anchor`` from the data-defined alignment (QGIS default: the
        bottom-left corner of the label at the position)."""
        props = label_settings.dataDefinedProperties()
        P = QgsPalLayerSettings.Property
        parts = []
        for key, default in ((P.Hali, "Left"), (P.Vali, "Bottom")):
            prop = props.property(key)
            value = PropertyExtractor.get_value_or_expression(default, prop, "string") \
                if prop and prop.isActive() else default
            parts.append(value)
        if all(isinstance(p, str) for p in parts):
            return self._anchor_name(*parts)
        cases = []
        for hali in ("Left", "Center", "Right"):
            for vali in ("Bottom", "Base", "Half", "Cap", "Top"):
                cases += [f"{hali}|{vali}", self._anchor_name(hali, vali)]
        key = ["concat"] + [p if isinstance(p, str) else ["to-string", p] for p in
                            (parts[0], "|", parts[1])]
        return ["match", key] + cases + ["bottom-left"]

    def _background_image(self, background) -> Optional[str]:
        """Sprite for a label background shape (rectangle/ellipse/SVG/marker)."""
        from .fidelity.patterns import frame_image  # pylint: disable=import-outside-toplevel
        shape = _enum_int(background.type(), 0)
        names = {0: "rectangle", 1: "rectangle", 2: "ellipse", 3: "ellipse", 4: "svg", 5: "marker"}
        kind = names.get(shape, "rectangle")
        if kind == "marker":
            marker = background.markerSymbol()
            if marker is None:
                return None
            name = self._next_name("marker")
            self.marker_symbols[name] = SpriteRequest(marker.clone(), bake_rotation=True)
            return name
        if kind == "svg":
            from qgis.core import QgsSvgMarkerSymbolLayer, QgsMarkerSymbol  # pylint: disable=import-outside-toplevel
            if not background.svgFile():
                return None
            svg = QgsSvgMarkerSymbolLayer(background.svgFile(), 10)
            svg.setFillColor(background.fillColor())
            svg.setStrokeColor(background.strokeColor())
            name = self._next_name("marker")
            self.marker_symbols[name] = SpriteRequest(QgsMarkerSymbol([svg]), bake_rotation=True)
            return name
        if shape in (1, 3):
            self.context.report("Q2VT_PATTERN_APPROXIMATE",
                                "Square/circle label backgrounds are fitted to the text box "
                                "(width and height may differ).")
        fill = background.fillColor()
        stroke = background.strokeColor()
        stroke_px = PropertyExtractor.static_pixels(background.strokeWidth(),
                                                    background.strokeWidthUnit())
        radii = background.radii()
        radius_px = PropertyExtractor.static_pixels(max(radii.width(), radii.height()),
                                                    background.radiiUnit()) if kind == "rectangle" else 0
        fixed = 0
        if _enum_int(background.sizeType(), 0) == 1:  # SizeFixed
            size = background.size()
            fixed = max(1, round(PropertyExtractor.static_pixels(
                max(size.width(), size.height()), background.sizeUnit())))
        rgba = lambda c: (c.red(), c.green(), c.blue(), c.alpha())  # noqa: E731
        one, meta = frame_image(kind, rgba(fill), rgba(stroke), stroke_px, radius_px, 1, fixed)
        two, _ = frame_image(kind, rgba(fill), rgba(stroke), stroke_px, radius_px, 2, fixed)
        name = self._next_name("frame")
        self.pattern_images[name] = PatternImages(one, two, meta)
        return name

    def _frame_variants(self, background, label_settings, base: str, source_layer: str):
        """Frame per distinct data-defined frame fill/border colour."""
        from qgis.core import QgsTextBackgroundSettings  # pylint: disable=import-outside-toplevel
        props = label_settings.dataDefinedProperties()
        keys = {"stroke": QgsPalLayerSettings.Property.ShapeStrokeColor,
                "fill": QgsPalLayerSettings.Property.ShapeFillColor}
        fields = {}
        for part, key in keys.items():
            prop = props.property(key)
            if prop and prop.isActive():
                refs = sorted(c for c in QgsExpression(prop.asExpression()).referencedColumns()
                              if c.startswith(f"{_FIELD_PREFIX}_"))
                if refs:
                    fields[part] = refs[0]
        if not fields:
            return base
        names = sorted(set(fields.values()))
        combos = self._distinct_values(source_layer, names)
        if combos is None:
            self.context.report("Q2VT_SPRITE_VARIANTS_BUDGET",
                                "Too many distinct label frame colours; static colour used.")
            return base

        def make(combo):
            values = dict(zip(names, combo))
            variant = QgsTextBackgroundSettings(background)
            for part, field in fields.items():
                color = QgsSymbolLayerUtils.decodeColor(_argb_hex(values[field]))
                if part == "stroke":
                    variant.setStrokeColor(color)
                else:
                    variant.setFillColor(color)
            return self._background_image(variant)
        key, cases = self._variant_images(names, combos, make)
        return ["match", key] + cases + [base] if cases else base

    def _text_fit_icon_size(self):
        """Compensate MapLibre's text-fit layout at tile zoom + 1.

        With a zoom-dependent ``text-size`` MapLibre fits the icon to the text
        box shaped at ``tile zoom + 1``; map-unit sizes double per zoom, so
        the frame is scaled by ``2^(z - tile_zoom - 1)``: a sawtooth per zoom
        level up to the archive's max zoom, then smooth overzoom.
        """
        top = int(self.maxzoom)
        stops = []
        for level in range(0, top):
            stops += [level, 0.5, level + 0.999, 2 ** -0.001]
        stops += [top, 0.5, 24, 2.0 ** (24 - top - 1)]
        return ["interpolate", ["exponential", 2], ["zoom"]] + stops

    def _text_fit_padding_curve(self, background):
        """``icon-text-fit-padding`` as a zoom curve for map-unit buffers."""
        if _enum_int(background.sizeType(), 0) != 0 or \
                normalize_unit(background.sizeUnit()) not in ("map", "m"):
            return None
        size = background.size()
        x = PropertyExtractor.length(size.width(), background.sizeUnit())
        y = PropertyExtractor.length(size.height(), background.sizeUnit())
        if not (ex.is_zoom_curve(x) and ex.is_zoom_curve(y)):
            return None
        out = ["interpolate", ["exponential", 2], ["zoom"]]
        for zoom in (0, 24):
            vx, vy = ex.evaluate_zoom_curve(x, zoom), ex.evaluate_zoom_curve(y, zoom)
            out += [zoom, ["literal", [vy, vx, vy, vx]]]
        return out

    def _apply_icon_from_background(self, layer_def: dict, background, style_name: str,
                                    label_settings=None):
        """Configure icon layout/paint from a label background shape."""
        image = self._background_image(background)
        if image is None:
            self.context.report("Q2VT_UNSUPPORTED_SYMBOL_LAYER",
                                "Label background shape could not be exported.")
            return
        is_frame = image.startswith("frame_")
        if is_frame and label_settings is not None:
            image = self._frame_variants(background, label_settings, image,
                                         layer_def.get("source-layer", ""))
        layer_def["layout"]["icon-image"] = image

        text_fit = IconPropertyExtractor.get_icon_text_fit(background)
        if text_fit:
            layer_def["layout"]["icon-text-fit"] = text_fit

        text_fit_padding = IconPropertyExtractor.get_icon_text_fit_padding(background)
        if text_fit_padding:
            layer_def["layout"]["icon-text-fit-padding"] = text_fit_padding

        layer_def["layout"].update({
            "icon-anchor": IconPropertyExtractor.get_icon_anchor(),
            "icon-rotate": IconPropertyExtractor.get_icon_rotate(background=background),
            "icon-padding": IconPropertyExtractor.get_icon_padding(),
            "icon-rotation-alignment": IconPropertyExtractor.get_icon_rotation_alignment(),
            "icon-pitch-alignment": IconPropertyExtractor.get_icon_pitch_alignment(),
            "icon-allow-overlap": IconPropertyExtractor.get_icon_allow_overlap(),
            "icon-ignore-placement": IconPropertyExtractor.get_icon_ignore_placement(),
            "icon-keep-upright": IconPropertyExtractor.get_icon_keep_upright(),
            "icon-offset": IconPropertyExtractor.get_icon_offset(background),
        })

        if is_frame:
            layer_def["layout"]["icon-allow-overlap"] = True
            layer_def["layout"]["icon-ignore-placement"] = True
            if ex.is_zoom_curve(layer_def["layout"].get("text-size")):
                layer_def["layout"]["icon-size"] = self._text_fit_icon_size()
                padding = self._text_fit_padding_curve(background)
                if padding is not None:
                    layer_def["layout"]["icon-text-fit-padding"] = padding
        layer_def["paint"].update({
            "icon-opacity": IconPropertyExtractor.get_icon_opacity(background),
            "icon-halo-blur": IconPropertyExtractor.get_icon_halo_blur(),
            "icon-translate": IconPropertyExtractor.get_icon_translate(),
            "icon-translate-anchor": IconPropertyExtractor.get_icon_translate_anchor(),
        })

    def _apply_default_icon_props(self, layer_def: dict):
        """Apply default icon layout/paint when no background marker is present."""
        layer_def["layout"].update({
            "icon-allow-overlap": IconPropertyExtractor.get_icon_allow_overlap(),
            "icon-ignore-placement": IconPropertyExtractor.get_icon_ignore_placement(),
            "icon-optional": IconPropertyExtractor.get_icon_optional(),
            "icon-padding": IconPropertyExtractor.get_icon_padding(),
        })
        layer_def["paint"].update({
            "icon-opacity": IconPropertyExtractor.get_icon_opacity(),
            "icon-halo-color": IconPropertyExtractor.get_icon_halo_color(),
            "icon-halo-width": IconPropertyExtractor.get_icon_halo_width(),
            "icon-halo-blur": IconPropertyExtractor.get_icon_halo_blur(),
            "icon-translate": IconPropertyExtractor.get_icon_translate(),
            "icon-translate-anchor": IconPropertyExtractor.get_icon_translate_anchor(),
        })

    def to_json(self, indent: int = 2) -> str:
        """Serialise the in-memory style dict to a JSON string."""
        return json.dumps(self.style, indent=indent)

    def _drop_layers_with_failed_images(self, failed: Dict[str, str]):
        """Remove style layers whose sprite failed to render (already reported)."""
        if not failed:
            return
        kept = []
        for layer_def in self.style["layers"]:
            images = {
                (layer_def.get("layout") or {}).get("icon-image"),
                (layer_def.get("paint") or {}).get("fill-pattern"),
                (layer_def.get("paint") or {}).get("line-pattern"),
            }
            if images & set(failed):
                self.diagnostics.add(
                    "Q2VT_SPRITE_RENDER_FAILED",
                    f"Style layer '{layer_def['id']}' omitted: its image could not be rendered.",
                    component=layer_def["id"], layer_id=layer_def.get("source-layer", ""))
                continue
            kept.append(layer_def)
        self.style["layers"] = kept

    def save_to_file(self, filename: str = "style.json", indent: int = 2) -> str:
        """Write the style JSON and the sprite sheet to the output directory.

        Args:
            filename: Filename for the JSON file (relative to ``style/``).
            indent:   JSON indentation level for human-readable output.

        Returns:
            The absolute path of the written ``style.json`` file.
        """
        style_dir = os.path.join(self.output_dir, "style")
        os.makedirs(style_dir, exist_ok=True)

        generated = None
        if self.marker_symbols or self.pattern_images:
            sprites = SpriteGenerator(
                self.marker_symbols, style_dir, _SPRITE_QUALITY, False,
                diagnostics=self.diagnostics, pattern_images=self.pattern_images,
            )
            generated = sprites.generate()
            self._drop_layers_with_failed_images(sprites.failed)
            self.sprite_names = sprites.names
        if not generated:
            del self.style["sprite"]
        if self.glyphs:
            glyphs_dir = os.path.join(self.output_dir, "style", "glyphs")
            os.makedirs(glyphs_dir, exist_ok=True)
            GlyphGenerator(self.glyphs, 'q2vt_label', glyphs_dir).generate()
        else:
            del self.style["glyphs"]
        rounded_style = self.round_numeric_values(self.style)
        self.style = rounded_style
        filepath = os.path.join(style_dir, filename)
        with open(filepath, "w", encoding="utf8") as f:
            json.dump(rounded_style, f, indent=indent, ensure_ascii=False)
        return filepath

    def round_numeric_values(self, obj, digits: int = 4):
        """Recursively round floats for compact output.

        Only real numbers are touched. The legacy version also converted
        numeric-looking *strings* (field names like ``"2020"``, text values)
        into numbers and rounded to 2 decimals, which zeroed small factors
        such as map-unit curve stops.
        """
        if isinstance(obj, dict):
            return {k: self.round_numeric_values(v, digits) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.round_numeric_values(item, digits) for item in obj]
        if isinstance(obj, float):
            if obj != 0 and abs(obj) < 10 ** -digits:
                return float(f"{obj:.{digits}g}")
            rounded = round(obj, digits)
            return int(rounded) if rounded.is_integer() else rounded
        return obj


if __name__ == "__console__":
    exporter = QgisMapLibreStyleExporter(output_dir=QgsProcessingUtils.tempFolder(), utils_dir=None)
