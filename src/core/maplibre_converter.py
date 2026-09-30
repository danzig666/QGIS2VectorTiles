"""Convert QGIS Vector Tile Layer styles to MapLibre GL JSON style format."""

import json
import os
from os.path import join
from typing import Any, Dict, List, Optional, Union

from qgis.PyQt.QtGui import QColor, QFontInfo
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
        return PropertyExtractor.length(
            symbol_layer.width(), symbol_layer.widthUnit(), width_prop,
            symbol_layer.widthMapUnitScale(),
        )

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
            width = width_px if ex.is_number(width_px) and width_px > 0 else 1.0
            unit = symbol_layer.customDashPatternUnit()
            return [
                max(0.0, PropertyExtractor.static_pixels(d, unit)) / width
                for d in dash_vector
            ]

        # Ratios are [dash_length, gap_length] as multiples of the line-width.
        fixed_presets = {
            2: [3, 2],             # Dash: 3x width dash, 2x width gap
            3: [1, 2],             # Dot: 1x width dash (looks like a square dot), 2x gap
            4: [3, 2, 1, 2],       # Dash-Dot: Dash, gap, dot, gap
            5: [3, 2, 1, 2, 1, 2], # Dash-Dot-Dot: Dash, gap, dot, gap, dot, gap
        }
        return fixed_presets.get(_enum_int(symbol_layer.penStyle()))

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
    def get_marker_line_spacing(symbol_layer: QgsSymbolLayer) -> float:
        """Return ``symbol-spacing`` in CSS px from the marker-line interval."""
        placements = LinePropertyExtractor.marker_line_placements(symbol_layer)
        try:
            interval = float(symbol_layer.interval())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            interval = 0.0

        if "Interval" in placements and interval > 0:
            return max(1.0, PropertyExtractor.static_pixels(interval, symbol_layer.intervalUnit()))

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
    def get_marker_line_offset(symbol_layer: QgsSymbolLayer) -> float:
        """Return the marker-line's perpendicular offset from the line, in CSS px."""
        try:
            offset = float(symbol_layer.offset())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return 0.0
        if offset == 0:
            return 0.0
        return PropertyExtractor.static_pixels(offset, symbol_layer.offsetUnit())


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
    def get_icon_size(
        symbol_layer: QgsSymbolLayer, default_size: float = 1.0
    ) -> Union[float, List]:
        """Return ``icon-size`` honouring any data-defined size.

        The sprite is rendered at ``_SPRITE_QUALITY`` times the static size,
        so the static icon-size is ``1 / _SPRITE_QUALITY``. A data-defined
        size ``v`` (same unit as the static size ``s``) scales the image by
        ``v / s``. The expression is built with the typed builder — the legacy
        code divided a Python list by a number and raised ``TypeError``.
        """
        base_scale = default_size / _SPRITE_QUALITY
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
        """Return ``icon-text-fit-padding`` ``[top, right, bottom, left]`` from buffer size."""
        if background.enabled() and background.sizeType() == 0:
            buf_px = PropertyExtractor.static_pixels(
                background.size().width(), background.sizeUnit()
            )
            buf_px = max(buf_px, 3)
            return [buf_px * 2, buf_px, buf_px * 2, buf_px]
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

        The empirical ``/(_MAPLIBRE_LABELS_FACTOR * 2)`` calibration is kept
        until glyph metrics are calibrated against QGIS (see plan §8.1).
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
        return ex.div(width, _MAPLIBRE_LABELS_FACTOR * 2)

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
    def get_text_allow_overlap() -> bool:
        """Return ``text-allow-overlap`` (MapLibre default: ``False``)."""
        return False

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
        return interval.style_bounds(self.maxzoom, self.profile.overzoom)

    def _convert_renderer_style(self, style):
        """Convert a single ``QgsVectorTileBasicRendererStyle`` into MapLibre layer(s)."""
        if not style.isEnabled() or not style.symbol():
            return
        bounds = self._zoom_bounds(style)
        if bounds is None:
            return
        self.context.component = style.styleName()
        self.context.source_layer = style.layerName()
        self._convert_symbol(
            style.symbol(), style.styleName(), style.layerName(),
            self.source_name, bounds[0], bounds[1],
        )

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
        """Names of the active data-defined properties of a symbol/symbol layer."""
        names = []
        try:
            props = obj.dataDefinedProperties()
            definitions = obj.propertyDefinitions()
        except (AttributeError, RuntimeError):
            return names
        for key in props.propertyKeys():
            prop = props.property(key)
            if prop and prop.isActive():
                definition = definitions.get(key)
                name = definition.name() if definition else str(key)
                names.append(name[:1].upper() + name[1:])
        return names

    def _classify(self, symbol_layer: QgsSymbolLayer, index: int) -> Strategy:
        """Classify a symbol layer and report unsupported data-defined properties."""
        layer_type = symbol_layer.layerType()
        result = classify(layer_type, self._active_ddp_names(symbol_layer))
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
            self._convert_marker_symbol(
                symbol_layer, symbol, style_name, source_layer_name,
                source_name, min_zoom, max_zoom,
            )
        elif symbol_type == QgsSymbol.SymbolType.Line:
            self._convert_line_symbol(
                symbol, style_name, source_layer_name, source_name, min_zoom, max_zoom
            )
        elif symbol_type == QgsSymbol.SymbolType.Fill:
            if self._classify(symbol_layer, 0) == Strategy.UNSUPPORTED:
                return
            self._convert_fill_symbol(
                symbol_layer, symbol, style_name, source_layer_name, source_name, min_zoom, max_zoom
            )

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
        self.marker_symbols[marker_name] = SpriteRequest(symbol.clone(), bake_rotation=False)

        layer_def["layout"].update({
            "icon-image": IconPropertyExtractor.get_icon_image(marker_name),
            "icon-size": IconPropertyExtractor.get_icon_size(symbol_layer, 1.0),
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
        self.marker_symbols[marker_name] = SpriteRequest(sub_symbol.clone(), bake_rotation=True)

        marker_sub_layer = sub_symbol.symbolLayer(0)
        rotate_with_line = LinePropertyExtractor.get_marker_line_rotate_symbols(symbol_layer)
        offset_px = LinePropertyExtractor.get_marker_line_offset(symbol_layer)

        layer_def["layout"].update({
            "icon-image": IconPropertyExtractor.get_icon_image(marker_name),
            "icon-size": IconPropertyExtractor.get_icon_size(marker_sub_layer, 1.0),
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
            # icon-offset is scaled by icon-size, so divide by the static
            # icon-size (a data-defined size uses the static base size).
            icon_size_value = layer_def["layout"]["icon-size"]
            scale = icon_size_value if ex.is_number(icon_size_value) and icon_size_value \
                else 1.0 / _SPRITE_QUALITY
            layer_def["layout"]["icon-offset"] = [0, offset_px / scale]

        layer_def["paint"].update({
            "icon-opacity": IconPropertyExtractor.get_icon_opacity(),
            "icon-halo-color": IconPropertyExtractor.get_icon_halo_color(),
            "icon-halo-width": IconPropertyExtractor.get_icon_halo_width(),
            "icon-halo-blur": IconPropertyExtractor.get_icon_halo_blur(),
            "icon-translate": IconPropertyExtractor.get_icon_translate(),
            "icon-translate-anchor": IconPropertyExtractor.get_icon_translate_anchor(),
        })

        self.style["layers"].append(layer_def)

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
            pattern_name = None
            if symbol_layer.layerType() == "LinePatternFill":
                pattern_name = self._register_line_pattern(symbol_layer, symbol)
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
            "text-allow-overlap": TextPropertyExtractor.get_text_allow_overlap(),
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
        if variable_anchor:
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

        if background.enabled() and background.markerSymbol():
            self._apply_icon_from_background(layer_def, background, style_name)
        else:
            self._apply_default_icon_props(layer_def)

        self.style["layers"].append(layer_def)

    def _apply_icon_from_background(self, layer_def: dict, background, style_name: str):
        """Configure icon layout/paint from a label background marker symbol."""
        marker = background.markerSymbol() if hasattr(background, "markerSymbol") else None
        if marker and marker.type() == QgsSymbol.SymbolType.Marker:
            marker_name = self._next_name("marker")
            self.marker_symbols[marker_name] = SpriteRequest(marker.clone(), bake_rotation=True)
            layer_def["layout"]["icon-image"] = marker_name
        else:
            self.context.report("Q2VT_UNSUPPORTED_SYMBOL_LAYER",
                                "Label background shape without a marker symbol is not exported.")
            return

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

        layer_def["paint"].update({
            "icon-opacity": IconPropertyExtractor.get_icon_opacity(background),
            "icon-color": IconPropertyExtractor.get_icon_color(background),
            "icon-halo-color": IconPropertyExtractor.get_icon_halo_color(background),
            "icon-halo-width": IconPropertyExtractor.get_icon_halo_width(background),
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
