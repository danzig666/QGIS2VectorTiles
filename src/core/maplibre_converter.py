"""Convert QGIS Vector Tile Layer styles to MapLibre GL JSON style format."""

import copy
import dataclasses
import json
import math
import re
import os
from os.path import join
from typing import Any, Dict, List, Optional, Tuple, Union

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
from qgis.core import NULL, Qgis, QgsSymbolLayerUtils, QgsFillSymbol, QgsLineSymbol, QgsUnitTypes
from qgis.utils import iface
from .glyphs_generator import GlyphGenerator
from .sprite_generator import SpriteGenerator, SpriteRequest, PatternImages
from .fidelity import expressions as ex
from .fidelity import feature_order
from .fidelity import html_labels
from .fidelity import materialize as mat
from .fidelity.capabilities import SPRITE_FAMILIES, capability, classify
from .fidelity.diagnostics import DiagnosticCollector
from .fidelity.model import ExportProfile, Strategy, ZoomInterval
from .materializer import pattern_anchor_kind, pattern_in_viewport
from .fidelity.patterns import (LinePatternSpec, qgis_image_hatch, render_line_pattern,
                                 solve_periodic_cell)
from .fidelity.units import LengthConverter, MapUnitScale, UnitError, normalize_unit
from ..utils.config import (_SPRITE_QUALITY, _MAPLIBRE_LABELS_FACTOR, _FIELD_PREFIX,
                            _MAPLIBRE_BASELINE_BELOW_MIDDLE_EM)


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


def _label_enum(settings, name: str, default=None):
    """An enum attribute of label settings as an int; ``default`` when PyQGIS
    cannot read it: an old project's unset value (-1, saved as 4294967295,
    e.g. ``multilineAlign``) is no member of the enum and reading the
    attribute raises ValueError, though QGIS itself draws such labels."""
    try:
        return _enum_int(getattr(settings, name), default)
    except ValueError:
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


# MapLibre's line antialiasing inks a line thinner than ~0.9 px as if it
# were wider (0.2 px draws ~0.36 px of ink, averaged over sub-pixel
# positions at device pixel ratio 1); Qt inks exactly the width. Opacity
# factors by width in CSS px, measured against QGIS renders.
_THIN_LINE_FACTORS = ((0.1, 0.29), (0.2, 0.56), (0.3, 0.71), (0.4, 0.85), (0.5, 0.89),
                      (0.6, 0.92), (0.7, 0.96), (0.8, 0.98), (0.9, 1.0))


def _thin_line_factor(width_px: float) -> float:
    if width_px >= _THIN_LINE_FACTORS[-1][0]:
        return 1.0
    if width_px <= _THIN_LINE_FACTORS[0][0]:
        # Below 0.1 px MapLibre draws a ~0.34 px floor.
        return max(0.0, width_px / 0.34)
    for (w0, f0), (w1, f1) in zip(_THIN_LINE_FACTORS, _THIN_LINE_FACTORS[1:]):
        if w0 <= width_px <= w1:
            return f0 + (f1 - f0) * (width_px - w0) / (w1 - w0)
    return 1.0


def thin_line_opacity(width, opacity):
    """``line-opacity`` giving lines thinner than a pixel the ink Qt gives
    them (see ``_THIN_LINE_FACTORS``); map-unit widths as a zoom curve."""
    if ex.is_number(width):
        factor = _thin_line_factor(width)
        return opacity if factor >= 1.0 else ex.mul(opacity, round(factor, 4))
    if not ex.is_zoom_curve(width) or ex.is_zoom_curve(opacity):
        return opacity
    try:
        zooms = [z / 2.0 for z in range(0, 49)]
        factors = [_thin_line_factor(ex.evaluate_zoom_curve(width, z)) for z in zooms]
    except ex.ExpressionError:
        return opacity
    if min(factors) >= 1.0:
        return opacity
    curve: List[Any] = ["interpolate", ["linear"], ["zoom"]]
    for zoom, factor in zip(zooms, factors):
        value = ex.mul(opacity, round(factor, 4)) if factor < 1.0 else opacity
        curve += [zoom, value]
    return curve


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
        # Oversampling of map-unit sprites drawn for one zoom band (None:
        # one sprite for the component's whole zoom range).
        self.sprite_oversampling = None
        # Screen-size icons drawn from an image this many times larger (None:
        # 1:1), see QgisMapLibreStyleExporter.PATTERN_MARKER_OVERSAMPLING.
        self.icon_oversampling = None

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
                return ex.to_boolean(field_expr, value if isinstance(value, bool) else False)
            return ex.to_string(field_expr, value if isinstance(value, str) else "")
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
        if len(pattern) % 2:
            pattern = pattern + pattern
        pattern = LinePropertyExtractor._without_empty_dashes(pattern)
        # Qt draws square and round caps on every dash (one line width longer,
        # gaps one width shorter); MapLibre does so for round caps only
        # (measured), so square-capped dashes are lengthened here.
        if _enum_int(symbol_layer.penCapStyle()) == 0x10:  # Qt::SquareCap
            pattern = [value + 1.0 if i % 2 == 0 and value > 0 else
                       value if i % 2 == 0 else max(0.0, value - 1.0)
                       for i, value in enumerate(pattern)]
        return [round(v, 4) for v in pattern]

    @staticmethod
    def _without_empty_dashes(pattern: List[float]) -> List[float]:
        """A dash of length 0 draws nothing in QGIS, cap or not: it is left
        out and the gaps around it join (MapLibre drew it as a dot, with the
        cap added). Dash-gap pairs; the last gap wraps to the first."""
        pairs = [[pattern[i], pattern[i + 1]] for i in range(0, len(pattern), 2)]
        if all(dash <= 0 for dash, _ in pairs):
            return [0.0, sum(gap for _, gap in pairs)]
        while pairs[0][0] <= 0:  # start on a dash: the leading gap goes last
            pairs = pairs[1:] + pairs[:1]
        kept = []
        for dash, gap in pairs:
            if dash <= 0:
                kept[-1][1] += gap
            else:
                kept.append([dash, gap])
        return [value for pair in kept for value in pair]

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
        uses_map = IconPropertyExtractor.uses_map_units(symbol)
        if unit not in ("map", "m"):
            # Screen size: the icon keeps its size; map-unit details (e.g. a
            # map-unit outline) are drawn at the reference zoom's scale.
            context = PropertyExtractor.context
            icon_size = IconPropertyExtractor.get_icon_size(symbol_layer, 1.0)
            if uses_map and IconPropertyExtractor.map_growth(symbol)[0] > 1.7:
                # Its extent is in map units after all (e.g. an ellipse's
                # width and height): the icon grows with the map.
                reference = max(PropertyExtractor.static_pixels(1.0, "map", context.reference_zoom),
                                1e-12)
                grow = ex.mul(PropertyExtractor.length(1.0, "map"), 1.0 / reference)
                return ex.mul(icon_size, grow), 1.0 / reference, \
                    context.sprite_oversampling or float(_SPRITE_QUALITY)
            if not uses_map:
                # Drawn 1:1 like QGIS draws it: an oversampled image shrunk
                # by the GPU (no mipmaps) breaks thin outlines into dots.
                static = ex.is_number(icon_size) and not IconPropertyExtractor._rotated(symbol)
                return icon_size, 1.0, \
                    (getattr(context, "icon_oversampling", None) or 1.0) if static else None
            mupp = 1.0 / max(PropertyExtractor.static_pixels(1.0, "map", context.reference_zoom),
                             1e-12)
            return icon_size, mupp, context.sprite_oversampling or float(_SPRITE_QUALITY)
        # (screen-size markers do not grow with the zoom)
        layer_units = {normalize_unit(symbol.symbolLayer(i).outputUnit())
                       for i in range(symbol.symbolLayerCount())} if symbol else set()
        if layer_units - {"map", "m"}:
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
        if context.sprite_oversampling:
            # Very large markers far into overzoom: a smaller image, scaled up.
            oversampling = min(context.sprite_oversampling, 256.0 / reference_px)
        px = PropertyExtractor.length(value, unit_name, None, symbol.sizeMapUnitScale())
        return ex.clamp(ex.mul(px, 1.0 / reference_px), 0, None), \
            map_units_per_pixel, oversampling

    @staticmethod
    def uses_map_units(symbol: QgsSymbol) -> bool:
        """Whether any size, width or offset of the marker is in map units."""
        if symbol is None:
            return False
        if normalize_unit(symbol.sizeUnit()) in ("map", "m"):
            return True
        for index in range(symbol.symbolLayerCount()):
            layer = symbol.symbolLayer(index)
            try:
                if layer.usesMapUnits():
                    return True
            except AttributeError:
                if normalize_unit(layer.outputUnit()) in ("map", "m"):
                    return True
        return False

    @staticmethod
    def map_growth(symbol: QgsSymbol):
        """``(growth, extent_px)``: how much the marker's rendered extent
        grows when the map units per pixel halve (~2: sized in map units,
        ~1: screen size), and its extent in px at one map unit per pixel."""
        from qgis.core import QgsMapToPixel, QgsRenderContext  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtCore import QPointF  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtGui import QImage, QPainter  # pylint: disable=import-outside-toplevel
        extents = []
        for mupp in (1.0, 0.5):
            image = QImage(1, 1, QImage.Format.Format_ARGB32)
            painter = QPainter(image)
            try:
                context = QgsRenderContext.fromQPainter(painter)
                context.setScaleFactor(96.0 / 25.4)
                context.setMapToPixel(QgsMapToPixel(mupp))
                probe = symbol.clone()
                probe.startRender(context)
                bounds = probe.bounds(QPointF(0, 0), context)
                probe.stopRender(context)
            finally:
                painter.end()
            extents.append(max(bounds.width(), bounds.height()))
        if extents[0] <= 0:
            return 1.0, 0.0
        return extents[1] / extents[0], extents[0]

    @staticmethod
    def _rotated(symbol: QgsSymbol) -> bool:
        for index in range(symbol.symbolLayerCount()):
            layer = symbol.symbolLayer(index)
            props = layer.dataDefinedProperties()
            if getattr(layer, "angle", lambda: 0)() or \
                    props.isActive(QgsSymbolLayer.Property.PropertyAngle):
                return True
        return False

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
            try:
                repeat = float(label_settings.repeatDistance or 0)
            except (AttributeError, TypeError, ValueError):
                repeat = 0.0
            # Without a repeat distance QGIS draws one label per line;
            # MapLibre's "line" repeats it every symbol-spacing.
            return "line" if repeat > 0 else "line-center"
        return "point"

    @staticmethod
    def get_symbol_spacing(label_settings: QgsPalLayerSettings = None) -> Union[float, List]:
        """Return ``symbol-spacing`` in pixels (MapLibre default: 250); a
        map-unit repeat distance grows with the map, as a zoom curve (one
        zoom's pixels made labels repeat end to end further in)."""
        if label_settings is None:
            return 250.0
        try:
            distance = label_settings.repeatDistance
            if distance and distance > 0:
                if normalize_unit(label_settings.repeatDistanceUnit) in ("map", "m"):
                    return ex.clamp(PropertyExtractor.length(
                        distance, label_settings.repeatDistanceUnit,
                        map_unit_scale=label_settings.repeatDistanceMapUnitScale), 1.0, None)
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

    # Map markers: QGIS draws them in feature order, a later one on top. The
    # tiles keep that order; "auto" (with a constant sort key) stacks
    # overlapping icons by their height on the screen instead.
    MARKER_Z_ORDER = "source"


class TextPropertyExtractor:
    """Extract text paint and layout properties from QGIS label settings."""

    @staticmethod
    def get_text_field(label_settings: QgsPalLayerSettings) -> Optional[List]:
        """Return ``text-field`` as a MapLibre ``["get", field]`` expression;
        HTML labels as ``format`` sections (see ``fidelity.html_labels``)."""
        if label_settings.fieldName:
            try:
                html = label_settings.format().allowHtmlFormatting()
            except AttributeError:
                html = False
            if html and label_settings.fieldName == f"{_FIELD_PREFIX}_label":
                return html_labels.format_expression()
            return ["get", label_settings.fieldName]
        return None

    @staticmethod
    def drawn_font(text_format: QgsTextFormat) -> QFont:
        """The font QGIS draws (``QgsTextFormat::scaledFont``): the B/I
        buttons (forcedBold, forcedItalic) on top of the font's own weight
        and slant."""
        font = QFont(text_format.font())
        if getattr(text_format, "forcedBold", lambda: False)():
            font.setBold(True)
        if getattr(text_format, "forcedItalic", lambda: False)():
            font.setItalic(True)
        return font

    @staticmethod
    def get_text_font(text_format: QgsTextFormat) -> str:
        """Return the fontstack name for ``text-font``.

        Uses the family/style the font actually resolves to on this system
        (fontconfig may substitute the family) and the same naming as the
        glyph generator. An unresolvable font is reported as an error, since
        the browser would otherwise render the labels without glyphs.
        """
        font = TextPropertyExtractor.drawn_font(text_format)
        info = QFontInfo(font)
        candidates = [(font.family(), font.styleName())]
        bold, italic = font.bold(), font.italic()
        if not font.styleName() and (font.weight() != QFont.Weight.Normal or italic):
            # A weight or slant without a style name: the face Qt matches it
            # to, which QGIS draws ('Open Sans' DemiBold and Medium are
            # 'Semibold', not 'Bold' or the regular face; Light is 'Light').
            face = info.styleName().lower()
            if info.family().lower() == font.family().lower() and \
                    (not italic or "italic" in face or "oblique" in face):
                stack = GlyphGenerator.resolve_fontstack(info.family(), info.styleName())
                if stack:
                    return stack
        # Qt fell back (another family, no slanted face): the plain
        # bold/italic face if one is installed, before the regular one.
        if not font.styleName() and (bold or italic):
            styles = (["Bold Italic", "Bold Oblique"] if bold and italic else
                      ["Bold"] if bold else ["Italic", "Oblique"])
            for style in styles:  # only a face that really is bold/italic (Qt may fall back)
                stack = GlyphGenerator.resolve_fontstack(font.family(), style) or ""
                face = stack[len(font.family()):].lower()
                if stack and (not bold or "bold" in face) and \
                        (not italic or "italic" in face or "oblique" in face):
                    return stack
        candidates += [
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

        QGIS strokes the text outline with a pen as wide as the buffer size,
        so the buffer reaches half its size beyond the glyphs (measured: a
        10 px buffer adds 5 px); a MapLibre halo reaches its full width.
        """
        buffer = text_format.buffer()
        if not buffer.enabled():
            return 0
        if _enum_int(buffer.sizeUnit()) == _enum_int(Qgis.RenderUnit.Percentage) \
                and label_settings is not None:
            # A percentage of the text size (a 10 % buffer was drawn 10 px wide).
            text_size = TextPropertyExtractor.get_text_size(text_format, label_settings, viewer)
            return ex.mul(text_size, buffer.size() / 200.0)
        size_prop = None
        if label_settings is not None:
            size_prop = label_settings.dataDefinedProperties().property(
                QgsPalLayerSettings.Property.BufferSize
            )
        width = PropertyExtractor.length(buffer.size(), buffer.sizeUnit(), size_prop,
                                         buffer.sizeMapUnitScale())
        return ex.div(width, 2.0 * _MAPLIBRE_LABELS_FACTOR)

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
        placement = TextPropertyExtractor.placement_name(label_settings)
        if placement in ("Line", "Curved"):
            # Above/below the line: the text box's bottom/top edge on the line.
            return {"above": "bottom", "below": "top"}.get(
                TextPropertyExtractor.line_side(label_settings), "center")
        if placement != "OverPoint":
            return "center"
        anchor_map = {
            0: "bottom-right", 1: "bottom",  2: "bottom-left",
            3: "right",        4: "center",  5: "left",
            6: "top-right",    7: "top",     8: "top-left",
        }
        return anchor_map.get(_label_enum(label_settings, "quadOffset"), "center")

    @staticmethod
    def line_side(label_settings: QgsPalLayerSettings) -> str:
        """"on", "above" or "below": where QGIS places a line label
        (``QgsLabelLineSettings.placementFlags``)."""
        try:
            flags = _enum_int(label_settings.lineSettings().placementFlags(), 1)
        except (AttributeError, TypeError):
            return "on"
        curved = TextPropertyExtractor.placement_name(label_settings) in ("Curved", "PerimeterCurved")
        return TextPropertyExtractor.side_of(flags, curved)

    @staticmethod
    def side_of(flags: int, curved: bool = False) -> str:
        """The side QGIS picks among the allowed ones (OnLine 1, AboveLine 2,
        BelowLine 4), measured on lone straight labels: beside the line wins
        over on it, above over below (a curved label allowed all three took
        either, as its candidates' costs tie)."""
        if flags & 2:
            return "above"
        return "below" if flags & 4 else "on"

    @staticmethod
    def get_line_text_offset(label_settings: QgsPalLayerSettings,
                             text_size_px: Union[float, List] = 16.0) -> List[float]:
        """``text-offset`` in ems moving an above/below line label by the
        label distance away from the line."""
        side = TextPropertyExtractor.line_side(label_settings)
        size = text_size_px if ex.is_number(text_size_px) else 16.0
        try:
            distance = float(label_settings.dist or 0)
        except (AttributeError, TypeError, ValueError):
            distance = 0.0
        if side == "on" or not distance or size <= 0:
            return [0, 0]
        em = PropertyExtractor.static_pixels(distance, label_settings.distUnits) / size
        return [0, -em if side == "above" else em]

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
            # An unreadable (unset) alignment is drawn left aligned by QGIS:
            # it is none of centre, right and justify.
            justification = _label_enum(label_settings, "multilineAlign", 0)
        except AttributeError:
            try:
                justification = label_settings.alignment
            except AttributeError:
                justification = 1
        # 3 = follow placement: MapLibre "auto" aligns by the chosen anchor.
        justify_map = {0: "left", 1: "center", 2: "right", 3: "auto", 4: "left"}
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
        """Return ``text-radial-offset`` in ems for "around point" placement:
        the QGIS label distance. Measured from the point ("From point"), the
        label touches it at distance 0; measured from the symbol's bounds,
        0.7 em stands for the symbol (its size is not known here).
        """
        from_symbol = _enum_int(getattr(label_settings, "offsetType", 0), 0) == 1
        base = 0.7 if from_symbol else 0.0
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
        """Return ``text-variable-anchor``: the candidate positions around a
        point, or, for horizontal / free polygon labels, the middle first
        and then shifted (QGIS tries positions all over the polygon), so a
        label can move aside instead of overlapping another."""
        placement = None if label_settings is None else \
            TextPropertyExtractor.placement_name(label_settings)
        if placement in ("Horizontal", "Free"):
            return ["center", "top", "bottom", "left", "right"]
        if placement is not None and placement not in ("AroundPoint", "OrderedPositionsAroundPoint"):
            return None
        if placement == "OrderedPositionsAroundPoint":
            ordered = TextPropertyExtractor.predefined_position_anchors(label_settings)
            if ordered:
                return ordered
        return list(TextPropertyExtractor.AROUND_POINT_ANCHORS)

    # QGIS "around point" (pal createCandidatesAroundPoint): the first
    # candidate is top-right (45 degrees), costs rise both ways round the
    # circle; of the 8 compass positions: TR, R, T, BR, TL, B, L, BL.
    # MapLibre names the side of the label at the point ("bottom-left" =
    # above right).
    AROUND_POINT_ANCHORS = ("bottom-left", "left", "bottom", "top-left",
                            "bottom-right", "top", "right", "top-right")
    # Cartographic placement: predefinedPositionOrder codes (label XML).
    PREDEFINED_POSITION_ANCHORS = {
        "TL": "bottom-right", "TSL": "bottom-right", "T": "bottom", "TSR": "bottom-left",
        "TR": "bottom-left", "L": "right", "R": "left", "BL": "top-right",
        "BSL": "top-right", "B": "top", "BSR": "top-left", "BR": "top-left", "O": "center"}

    @staticmethod
    def predefined_position_anchors(label_settings) -> List[str]:
        """Cartographic placement's positions in the QGIS order as MapLibre
        anchors (read from the label XML: the order is not in every API)."""
        from qgis.core import QgsReadWriteContext  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtXml import QDomDocument  # pylint: disable=import-outside-toplevel
        try:
            element = label_settings.writeXml(QDomDocument(), QgsReadWriteContext())
            codes = element.firstChildElement("placement").attribute("predefinedPositionOrder")
        except (AttributeError, RuntimeError, TypeError):
            codes = ""
        anchors = []
        for code in (codes or "TR,TL,BR,BL,R,L,TSR,BSR").split(","):
            anchor = TextPropertyExtractor.PREDEFINED_POSITION_ANCHORS.get(code.strip())
            if anchor and anchor not in anchors:
                anchors.append(anchor)
        return anchors

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
        """Return ``text-allow-overlap``: ``False``, labels avoid each other.
        QGIS labels that may overlap ("show all labels", overlap if required
        / at no cost) are drawn by the viewer even where they collide
        (``overlap_if_required``); MapLibre alone has no such mode."""
        return False

    @staticmethod
    def overlap_if_required(label_settings: QgsPalLayerSettings = None) -> bool:
        """True for QGIS labels that are drawn even if they overlap others."""
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
        """Return ``text-padding`` in pixels (MapLibre default: 2): the
        free space a label keeps around itself from other labels."""
        return 2

    @staticmethod
    def get_text_line_height() -> float:
        """Return ``text-line-height`` (MapLibre default: 1.2)."""
        return 1.2

    @staticmethod
    def get_text_letter_spacing(text_format: QgsTextFormat = None) -> float:
        """Return ``text-letter-spacing`` in ems (default 0).

        QGIS keeps a text format's letter spacing as the font's absolute
        spacing in the format's size units (QgsTextFormat::scaledFont), so
        in ems it is spacing / size; a percentage spacing (100 % = none)
        stretches every advance, about half an em per character."""
        if text_format is None:
            return 0
        font = text_format.font()
        spacing = font.letterSpacing()
        if font.letterSpacingType() == QFont.SpacingType.PercentageSpacing:
            em = (spacing - 100.0) / 100.0 * 0.5 if spacing else 0.0
        else:
            size = float(text_format.size() or 0)
            em = spacing / size if size > 0 else 0.0
        return round(em, 4) if abs(em) >= 1e-4 else 0

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
        visible_polygons: Optional[Dict[str, Tuple[str, bool]]] = None,
        heatmaps: Optional[Dict[str, dict]] = None,
        translates: Optional[Dict[str, tuple]] = None,
        effect_roles: Optional[Dict[str, str]] = None,
        inner_effects: Optional[Dict[str, dict]] = None,
        z_orders: Optional[Dict[str, str]] = None,
        feature_filters: Optional[Dict[str, list]] = None,
        label_windows: Optional[set] = None,
        layer_opacities: Optional[Dict[str, float]] = None,
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
            visible_polygons: Style name -> (polygon source layer, label per
                             part) of labels placed on the visible part of
                             their polygon by the viewer.
        """
        self.visible_polygons = visible_polygons or {}
        # Style name -> heatmap spec (fidelity.heatmap): drawn as a heatmap layer.
        self.heatmaps = heatmaps or {}
        # Style name -> (x, y, unit): shifted on screen (viewport translate).
        self.translates = translates or {}
        # Style name -> "outer" (only the line's outer effects) / "none".
        self.effect_roles = effect_roles or {}
        # Style name -> inner effect strips (SymbolMaterializer._inner_effects).
        self.inner_effects = inner_effects or {}
        # Style name -> symbol-z-order ("source": data order).
        self.z_orders = z_orders or {}
        # Style name -> filter of its feature-order stratum (fidelity.feature_order).
        self.feature_filters = feature_filters or {}
        # Style names of repeated curved line labels laid out at export time:
        # one short line per label (RulesExporter._label_windows).
        self.label_windows = label_windows or set()
        # Style name -> the QGIS layer's opacity (Layer Rendering), below 1.
        self.layer_opacities = layer_opacities or {}
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
        try:
            self._convert_style_layers(style, bounds, first)
        finally:
            opacity = self.layer_opacities.get(style.styleName())
            if opacity is not None:
                self._apply_layer_opacity(self.style["layers"][first:], opacity)

    _OPACITY_PAINT = {"fill": ("fill-opacity",), "line": ("line-opacity",),
                      "circle": ("circle-opacity", "circle-stroke-opacity"),
                      "symbol": ("icon-opacity", "text-opacity"),
                      "fill-extrusion": ("fill-extrusion-opacity",), "heatmap": ("heatmap-opacity",)}

    def _apply_layer_opacity(self, layer_defs, opacity: float) -> None:
        """QGIS draws a layer with an opacity below 1 as one image and blends
        it once; the browser can only make each of its style layers that
        transparent (where they overlap, the result is a little darker)."""
        for layer_def in layer_defs:
            if layer_def.get("metadata", {}).get("q2vt:layer-opacity"):
                continue  # a feature-order copy of layers already done
            paint = layer_def.setdefault("paint", {})
            for prop in self._OPACITY_PAINT.get(layer_def.get("type"), ()):
                paint[prop] = ex.mul(paint.get(prop, 1), float(opacity))
            layer_def.setdefault("metadata", {})["q2vt:layer-opacity"] = float(opacity)

    def _convert_style_layers(self, style, bounds, first: int) -> None:
        if style.styleName() in self.heatmaps:
            self._heatmap_layer(style, self.heatmaps[style.styleName()], bounds)
            return
        if style.styleName() in getattr(self, "inner_effects", {}):
            self._inner_effect_layers(style, self.inner_effects[style.styleName()], bounds)
            return
        feature_filter = getattr(self, "feature_filters", {}).get(style.styleName())
        sources = self.__dict__.setdefault("_stratum_sources", {})
        base = feature_order.copied_from(style.styleName())
        if feature_filter and base in sources:
            # A feature-order stratum copy (fidelity.feature_order): the style
            # layers of its rule again (with the same images), drawn higher.
            for layer_def in copy.deepcopy(sources[base]):
                suffix = layer_def["id"][len(base):] if layer_def["id"].startswith(base) \
                    else f"_{layer_def['id']}"
                layer_def["id"] = style.styleName() + suffix
                self.style["layers"].append(layer_def)
            self._apply_feature_filter(self.style["layers"][first:], feature_filter)
            return
        z_order = getattr(self, "z_orders", {}).get(style.styleName())
        # Pattern markers sit at fractional pixels, which QGIS draws
        # anti-aliased. MapLibre draws a 1:1 icon at the nearest pixel, which
        # rounds the overlaps between markers into visible bands; an image
        # twice as large, shrunk by the GPU, is anti-aliased like QGIS's.
        self.context.icon_oversampling = self.PATTERN_MARKER_OVERSAMPLING if z_order else None
        try:
            self._convert_symbol(
                style.symbol(), style.styleName(), style.layerName(),
                self.source_name, bounds[0], bounds[1],
            )
        finally:
            self.context.icon_oversampling = None
        if style.styleName() in self.ordered_styles:
            self._apply_draw_order(self.style["layers"][first:])
        if style.styleName() in self.translates:
            self._apply_translate(self.style["layers"][first:], self.translates[style.styleName()])
        if z_order:  # markers drawn in data order (QGIS's drawing order)
            for layer_def in self.style["layers"][first:]:
                if layer_def.get("type") == "symbol":
                    layer_def.setdefault("layout", {})["symbol-z-order"] = z_order
        if feature_filter:
            if base is None:  # the rule's own style layers, kept for its copies
                sources[style.styleName()] = copy.deepcopy(self.style["layers"][first:])
            self._apply_feature_filter(self.style["layers"][first:], feature_filter)

    @staticmethod
    def _apply_feature_filter(layer_defs, feature_filter) -> None:
        """Draw only the features of the style's feature-order stratum
        (fidelity.feature_order), within any filter the layer has."""
        for layer_def in layer_defs:
            old = layer_def.get("filter")
            layer_def["filter"] = ["all", old, feature_filter] if old else feature_filter

    PATTERN_MARKER_OVERSAMPLING = 2.0

    @staticmethod
    def _cached_marker_image(marker) -> bool:
        """QGIS draws every layer of ``marker`` from its cached image at
        whole pixels: simple markers without data-defined properties
        (QgsSimpleMarkerSymbolLayer::startRender / renderPoint)."""
        layers = [marker.symbolLayer(i) for i in range(marker.symbolLayerCount())]
        return bool(layers) and all(layer.layerType() == "SimpleMarker"
                                    and not layer.dataDefinedProperties().hasActiveProperties()
                                    for layer in layers)

    _TRANSLATE = {"fill": "fill", "line": "line", "circle": "circle", "symbol": "icon"}

    def _apply_translate(self, layer_defs, translate) -> None:
        """Shift the style's layers on screen like a QGIS fill offset (painter
        coordinates: x right, y down, not turned with the map)."""
        x, y, unit_name = translate
        unit, ok = QgsUnitTypes.decodeRenderUnit(unit_name)
        if not ok:
            return
        shift = [PropertyExtractor.static_pixels(x, unit), PropertyExtractor.static_pixels(y, unit)]
        for layer_def in layer_defs:
            prefix = self._TRANSLATE.get(layer_def.get("type"))
            if prefix is None:
                continue
            paint = layer_def.setdefault("paint", {})
            current = paint.get(f"{prefix}-translate")
            if isinstance(current, list) and len(current) == 2 and \
                    all(isinstance(v, (int, float)) for v in current):
                shift = [shift[0] + current[0], shift[1] + current[1]]
            paint[f"{prefix}-translate"] = shift
            paint[f"{prefix}-translate-anchor"] = "viewport"

    def _heatmap_layer(self, style, spec: dict, bounds) -> None:
        """A QGIS heatmap renderer as a MapLibre heatmap layer; the weight is
        the placeholder marker's Size (the renderer's weight expression)."""
        from .fidelity.heatmap import heatmap_paint  # pylint: disable=import-outside-toplevel
        symbol = style.symbol()
        prop = symbol.symbolLayer(0).dataDefinedProperties().property(
            QgsSymbolLayer.Property.PropertySize) if symbol.symbolLayerCount() else None
        weight = PropertyExtractor.get_value_or_expression(1.0, prop, "number")
        layer_def = self._base_layer_def("heatmap", style.styleName(), style.layerName(),
                                         self.source_name, bounds[0], bounds[1])
        layer_def["layout"]["visibility"] = "visible"
        layer_def["paint"] = heatmap_paint(spec, weight)
        self.style["layers"].append(layer_def)

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
        for key in sorted(props.propertyKeys()):
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
            # Markers are rendered by QGIS into sprites, effects included;
            # a simple line's glow and shadow become extra line layers.
            handled = {"QgsDrawSourceEffect"}
            if isinstance(symbol_layer, QgsSimpleLineSymbolLayer):
                handled |= set(self.LINE_EFFECTS)
            ignored = sorted({e.type() for e in self._effect_list(symbol_layer)
                              if type(e).__name__ not in handled})
            cap = capability(layer_type)
            if ignored and not (cap is not None and cap.family in SPRITE_FAMILIES):
                self.context.report("Q2VT_UNSUPPORTED_EFFECT",
                                    f"{layer_type}: paint effect {', '.join(ignored)} is ignored.",
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
            return self._texture_px(value, unit)

        color = QColor(line.color())
        color.setAlphaF(color.alphaF() * sub.opacity())
        spec = LinePatternSpec(
            angle_deg=float(symbol_layer.lineAngle()),
            spacing_px=screen_px(symbol_layer.distance(), symbol_layer.distanceUnit(), "spacing"),
            line_width_px=screen_px(line.width(), line.widthUnit(), "width"),
            color_rgba=(color.red(), color.green(), color.blue(), color.alpha()),
            offset_px=screen_px(symbol_layer.offset(), symbol_layer.offsetUnit(), "offset"),
        )
        if self._exact_screen_texture and \
                _enum_int(symbol_layer.clipMode()) == _enum_int(Qgis.LineClipMode.ClipPainterOnly):
            angle, spacing = qgis_image_hatch(spec.angle_deg, spec.spacing_px)
            spec = dataclasses.replace(spec, angle_deg=angle, spacing_px=spacing)
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

    @staticmethod
    def _offset_ems(dx, dy, size, baseline: float):
        """A font marker's offset in ems of its size, baseline added: [x, y],
        or a zoom step of them when offset and size do not scale alike (a
        map-unit scale limit on one of them). text-offset is laid out at the
        tile's whole zoom only: each zoom takes the ratio of its middle
        (exact there, within sqrt(2) at its ends)."""
        if all(ex.is_number(v) for v in (dx, dy, size)):
            return [ex.ratio(dx, size), ex.ratio(dy, size) + baseline]
        pairs = []
        for zoom in range(0, 25):
            den = ex.evaluate_zoom_curve(size, zoom + 0.5)
            pairs.append((zoom, [round(ex.evaluate_zoom_curve(dx, zoom + 0.5) / den, 4) if den else 0.0,
                                 round(ex.evaluate_zoom_curve(dy, zoom + 0.5) / den + baseline, 4)
                                 if den else baseline]))
        if all(pair == pairs[0][1] for _, pair in pairs):
            return pairs[0][1]
        expression = ["step", ["zoom"], ["literal", pairs[0][1]]]
        for zoom, pair in pairs[1:]:
            expression += [zoom, ["literal", pair]]
        return expression

    @staticmethod
    def _font_marker_baseline_em(symbol_layer) -> float:
        """Half the ascent of the marker's font, in ems (``QgsFontMarkerSymbolLayer``
        draws the text with its baseline this far below the point)."""
        from qgis.core import QgsApplication, QgsFontUtils  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtGui import QFontMetricsF  # pylint: disable=import-outside-toplevel
        font = QgsFontUtils.createFont(
            QgsApplication.fontManager().processFontFamilyName(symbol_layer.fontFamily()))
        if symbol_layer.fontStyle():
            font.setStyleName(QgsFontUtils.translateNamedStyle(symbol_layer.fontStyle()))
        font.setPixelSize(240)
        return QFontMetricsF(font).ascent() / 240.0 / 2.0

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
            "symbol-z-order": IconPropertyExtractor.MARKER_Z_ORDER,
            "visibility": "visible",
        }
        # QGIS puts the baseline half the font's ascent below the point;
        # MapLibre (centred anchor, our glyph metrics) 7/24 em below it.
        baseline = self._font_marker_baseline_em(symbol_layer) - 7.0 / 24.0
        ems = [0.0, baseline]
        offset = symbol_layer.offset()
        if offset.x() or offset.y():
            static_size = size if not isinstance(size, list) or ex.is_zoom_curve(size) else \
                PropertyExtractor.length(symbol_layer.size(), symbol_layer.sizeUnit())
            scale = symbol_layer.offsetMapUnitScale()
            dx = PropertyExtractor.length(offset.x(), symbol_layer.offsetUnit(), None, scale)
            dy = PropertyExtractor.length(offset.y(), symbol_layer.offsetUnit(), None, scale)
            try:
                ems = self._offset_ems(dx, dy, static_size, baseline)
            except (ex.ExpressionError, TypeError, IndexError):
                self.context.report("Q2VT_MIXED_UNITS",
                                    "Font marker offset and size use different unit families.")
                ems = [0.0, baseline]
        if not ex.is_expression(ems):
            if abs(ems[0]) > 1e-6 or abs(ems[1]) > 1e-6:
                layout["text-offset"] = [round(ems[0], 4), round(ems[1], 4)]
        else:
            layout["text-offset"] = ems
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
        for key in sorted(props.propertyKeys()):
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
        # Screen-unit parts are drawn at TEXTURE_SCREEN_SCALE, map-unit parts
        # at the reference zoom (scaling both factors keeps map pixels).
        scale = self._screen_scale()
        reference = self._reference_map_units_per_px() * scale
        one = SymbolImage(marker, "pattern-marker", scale, True, reference).img
        two = SymbolImage(marker, "pattern-marker", 2 * scale, True, reference).img
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

        MapLibre draws ``fill-pattern`` in the pixels of the tile's integer
        zoom, so a texture grows with the map until the next zoom, exactly
        like map-unit sizes in QGIS: each zoom's texture is rendered at that
        integer zoom (screen-unit parts, see ``TEXTURE_SCREEN_SCALE``, stay
        within about ±41 %); ``fill-pattern`` steps between them.
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
                self.context.reference_zoom = zoom
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
            "Pattern in map units drawn with one texture per zoom level (screen-unit "
            "parts within about ±41 % of QGIS between integer zooms).",
            strategy=Strategy.APPROXIMATE.value)
        if len(stops) == 1:
            return stops[0][1]
        expr: List[Any] = ["step", ["zoom"], stops[0][1]]
        for zoom, name in stops[1:]:
            expr += [zoom, name]
        return expr

    _pattern_zoom_bands = False

    # MapLibre draws fill-pattern in the pixels of the tile's integer zoom: a
    # texture grows 2x with the map until the next zoom, while QGIS keeps
    # screen-unit sizes. Drawn at 1/sqrt(2), they stay within 0.71x-1.41x of
    # QGIS (instead of 1x-2x), exact in the middle of every zoom.
    # Textures of patterns sized only in screen units are drawn at their true
    # size instead: their style layer carries SCREEN_PATTERN_FLAG, which the
    # patched MapLibre (tools/patch_maplibre.py) draws at the real zoom.
    TEXTURE_SCREEN_SCALE = 1.0 / math.sqrt(2.0)
    SCREEN_PATTERN_FLAG = "q2vt:screen-pattern"
    PATTERN_ANCHOR_FLAG = "q2vt:pattern-anchor"
    _exact_screen_texture = False

    def _screen_scale(self) -> float:
        return 1.0 if self._exact_screen_texture else self.TEXTURE_SCREEN_SCALE

    def _texture_px(self, value, unit) -> float:
        px = PropertyExtractor.static_pixels(value, unit)
        return px if normalize_unit(unit) in ("map", "m") else px * self._screen_scale()

    def _pattern_px(self, value, unit, what: str) -> float:
        if normalize_unit(unit) in ("map", "m") and value and not self._pattern_zoom_bands:
            self.context.report(
                "Q2VT_PATTERN_MAP_UNITS",
                f"Pattern {what} in map units is frozen at zoom {self.context.reference_zoom:g}.",
                strategy=Strategy.APPROXIMATE.value)
        return self._texture_px(value, unit) if value else 0.0

    def _textures(self, cell_1x, cell_2x, error: float, what: str) -> str:
        if error > self.profile.tolerance_rel and error > 0:
            self.context.report(
                "Q2VT_PATTERN_NONPERIODIC",
                f"{what}: spacing rounded to whole pixels changes it by {error:.1%}.",
                strategy=Strategy.APPROXIMATE.value)
        name = self._next_name("pattern")
        self.pattern_images[name] = PatternImages(cell_1x, cell_2x)
        return name

    def _register_brush_pattern(self, layer) -> Optional[str]:
        """Texture of a Qt brush pattern (dense dots, hatching, crossing):
        QGIS fills with the brush itself, an 8 px pattern (at any DPI, twice
        that on a 2x screen) starting at the corner of the view. A
        data-defined colour or style has no single texture (None)."""
        from qgis.PyQt.QtGui import QBrush, QImage, QPainter  # pylint: disable=import-outside-toplevel
        from .sprite_generator import SymbolImage  # pylint: disable=import-outside-toplevel
        style = layer.brushStyle()
        if _enum_int(style) not in range(2, 15):  # Dense1Pattern .. DiagCrossPattern
            return None
        props = layer.dataDefinedProperties()
        for key in (QgsSymbolLayer.Property.PropertyFillColor,
                    QgsSymbolLayer.Property.PropertyFillStyle):
            prop = props.property(key)
            if prop and prop.isActive():
                return None
        cells = []
        for ratio in (1, 2):
            image = QImage(16 * ratio, 16 * ratio, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            image.setDevicePixelRatio(ratio)
            painter = QPainter(image)
            painter.fillRect(0, 0, 16, 16, QBrush(layer.color(), style))
            painter.end()
            image.setDevicePixelRatio(1)
            cells.append(SymbolImage._qt_to_pil(image))  # pylint: disable=protected-access
        return self._textures(cells[0], cells[1], 0.0, "Brush pattern")

    def _register_point_pattern(self, layer) -> Optional[str]:
        """Seamless texture for a point pattern spaced in screen units."""
        from .fidelity.patterns import (apply_pattern_positions, point_pattern_cell,  # pylint: disable=import-outside-toplevel
                                        tile_markers)
        marker = layer.subSymbol()
        if marker is None:
            return None
        if layer.maximumRandomDeviationX() or layer.maximumRandomDeviationY() or layer.angle():
            self.context.report("Q2VT_PATTERN_APPROXIMATE",
                                "Random deviation or rotation of pattern markers is ignored.",
                                strategy=Strategy.APPROXIMATE.value)
        if hasattr(layer, "clipMode") and \
                _enum_int(layer.clipMode()) != _enum_int(Qgis.MarkerClipMode.Shape):
            self.context.report("Q2VT_PATTERN_APPROXIMATE",
                                "Pattern markers are cut at the polygon edge (QGIS draws only "
                                "whole markers inside it); spacing in map units keeps them whole.",
                                strategy=Strategy.APPROXIMATE.value)
        dx = self._pattern_px(layer.distanceX(), layer.distanceXUnit(), "spacing")
        dy = self._pattern_px(layer.distanceY(), layer.distanceYUnit(), "spacing")
        if dx <= 0 or dy <= 0:
            return None
        disp_x = self._pattern_px(layer.displacementX(), layer.displacementXUnit(), "displacement")
        disp_y = self._pattern_px(layer.displacementY(), layer.displacementYUnit(), "displacement")
        # QgsPointPatternFillSymbolLayer::applyPattern shifts the markers in
        # the cell by the offset (x right, y down; percentages of the cell).
        off_x, off_y = (self._pattern_offset_px(layer.offsetX(), layer.offsetXUnit(), 2 * dx),
                        self._pattern_offset_px(layer.offsetY(), layer.offsetYUnit(), 2 * dy))
        _, _, _, error = point_pattern_cell(dx, dy, disp_x, disp_y)
        if self._random_ddp(marker):
            cells = self._random_marker_cells(marker, dx, dy, disp_x, disp_y, off_x, off_y)
            return self._textures(cells[0], cells[1], error, "Point pattern")
        one, two = self._marker_images(marker)
        cells = []
        if not self._whole_markers(layer) and not layer.angle() and \
                not layer.maximumRandomDeviationX() and not layer.maximumRandomDeviationY() \
                and self._exact_screen_texture and not self._pattern_uses_map_units(layer) \
                and 1 <= 2 * dx and 1 <= 2 * dy \
                and 2 * dx <= 1000 and 2 * dy <= 1000:
            # QGIS's own texture brush (applyPattern): its truncated size and
            # drawing order, so overlapping markers stack as in QGIS. (QGIS
            # draws the markers one by one when that image would be empty or
            # over 2000 px.)
            disp_x, disp_y = (self._pattern_offset_px(layer.displacementX(),
                                                      layer.displacementXUnit(), 2 * dx),
                              self._pattern_offset_px(layer.displacementY(),
                                                      layer.displacementYUnit(), 2 * dy))
            snap = self._cached_marker_image(marker)
            for ratio, image in ((1, one), (2, two)):
                width, height, positions = apply_pattern_positions(
                    dx * ratio, dy * ratio, disp_x * ratio, disp_y * ratio,
                    off_x * ratio, off_y * ratio)
                cells.append(tile_markers(image, width, height, positions, wrap=False, snap=snap))
            return self._textures(cells[0], cells[1], 0.0, "Point pattern")
        for ratio, image in ((1, one), (2, two)):
            width, height, positions, _ = point_pattern_cell(
                dx * ratio, dy * ratio, disp_x * ratio, disp_y * ratio)
            positions = [((x + off_x * ratio) % width, (y + off_y * ratio) % height)
                         for x, y in positions]
            cells.append(tile_markers(image, width, height, positions))
        return self._textures(cells[0], cells[1], error, "Point pattern")

    @staticmethod
    def _whole_markers(layer) -> bool:
        """Point pattern drawn marker by marker in QGIS (a clip mode other
        than "Shape"), not with a texture brush."""
        return hasattr(layer, "clipMode") and \
            _enum_int(layer.clipMode()) != _enum_int(Qgis.MarkerClipMode.Shape)

    # A texture of markers with random data-defined values repeats after
    # about this many pixels (several markers per direction).
    RANDOM_CELL_PX = 256

    @staticmethod
    def _random_ddp(marker) -> List[Tuple[int, int, str]]:
        """[(symbol layer index, property key, expression)] of the marker's
        data-defined properties that only vary by chance (rand/randf)."""
        from .ddp_fetcher import is_random_only  # pylint: disable=import-outside-toplevel
        out = []
        for index in range(marker.symbolLayerCount()):
            props = marker.symbolLayer(index).dataDefinedProperties()
            for key in sorted(props.propertyKeys()):
                prop = props.property(key)
                if prop and prop.isActive() and prop.propertyType() == 3 and \
                        is_random_only(prop.expressionString()):
                    # @symbol_color is only set while QGIS draws the symbol.
                    color = "'" + QgsSymbolLayerUtils.encodeColor(marker.color()) + "'"
                    out.append((index, key, re.sub(r"@symbol_color\b", color,
                                                    prop.expressionString())))
        return out

    def _random_marker_cells(self, marker, dx, dy, disp_x, disp_y, off_x, off_y):
        """1x and 2x cells of a point pattern whose markers take random values
        (QGIS evaluates rand() for every marker it draws): several pattern
        cells in one texture, each marker drawn with its own values."""
        from PIL import Image  # pylint: disable=import-outside-toplevel
        from qgis.core import QgsExpression, QgsProperty  # pylint: disable=import-outside-toplevel
        from .fidelity.patterns import point_pattern_cell, tile_markers  # pylint: disable=import-outside-toplevel
        width, height, _, _ = point_pattern_cell(dx, dy, disp_x, disp_y)
        reps_x = max(1, min(16, round(self.RANDOM_CELL_PX / width)))
        reps_y = max(1, min(16, round(self.RANDOM_CELL_PX / height)))
        markers = []
        random_ddp = self._random_ddp(marker)
        cells = []
        for ratio in (1, 2):
            w, h, positions, _ = point_pattern_cell(dx * ratio, dy * ratio,
                                                    disp_x * ratio, disp_y * ratio)
            canvas = Image.new("RGBA", (w * reps_x, h * reps_y), (0, 0, 0, 0))
            index = 0
            for j in range(reps_y):
                for i in range(reps_x):
                    for x, y in positions:
                        if index == len(markers):  # same values at 1x and 2x
                            clone = marker.clone()
                            for layer_index, key, expression in random_ddp:
                                clone.symbolLayer(layer_index).setDataDefinedProperty(
                                    key, QgsProperty.fromValue(QgsExpression(expression).evaluate()))
                            markers.append(self._marker_images(clone))
                        image = markers[index][ratio - 1]
                        index += 1
                        point = ((x + i * w + off_x * ratio) % (w * reps_x),
                                 (y + j * h + off_y * ratio) % (h * reps_y))
                        canvas = Image.alpha_composite(
                            canvas, tile_markers(image, w * reps_x, h * reps_y, [point]))
            cells.append(canvas)
        return cells

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

    def _pattern_offset_px(self, value, unit, cell_px: float) -> float:
        if not value:
            return 0.0
        if normalize_unit(unit) in ("percentage", "percent", "%") or \
                _enum_int(unit) == _enum_int(Qgis.RenderUnit.Percentage):
            return cell_px * value / 200.0
        return self._pattern_px(value, unit, "offset")

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
        cells = []
        # As QGIS (QgsSVGFillSymbolLayer::applyPattern): the SVG drawn to
        # fill the whole tile, so its edges meet the next tile's without an
        # anti-aliased (transparent) seam; a rotated fill rotates the whole
        # texture (brush transform), in a seamless cell of rotated tiles.
        stroke = self._texture_px(layer.svgStrokeWidth(), layer.svgStrokeWidthUnit())
        per_mm = self._texture_px(1.0, Qgis.RenderUnit.Millimeters)
        lattice = None
        if layer.angle():
            from .fidelity.patterns import rotated_lattice_cell  # pylint: disable=import-outside-toplevel
            lattice = rotated_lattice_cell(width, width * aspect, layer.angle())
            if lattice is not None and lattice[4] > self.profile.tolerance_rel:
                self.context.report(
                    "Q2VT_PATTERN_APPROXIMATE",
                    f"Rotated SVG fill: tiles turned or scaled by up to {lattice[4]:.1%} so the "
                    "texture repeats without seams.", strategy=Strategy.APPROXIMATE.value)
        for ratio in (1, 2):
            tile_w = max(1, round(width * ratio))
            tile_h = max(1, round(width * aspect * ratio))
            tile = self._svg_cell(layer, tile_w, tile_h, stroke * ratio, per_mm * ratio)
            if tile is not None and lattice is not None:
                tile = self._rotated_texture(tile, lattice, ratio)
            elif layer.angle():
                tile = None
            if tile is None:
                cells = []
                break
            cells.append(tile)
        if not cells:
            one, two = self._marker_images(QgsMarkerSymbol([marker_layer]))
            for ratio, image in ((1, one), (2, two)):
                cell_w = max(1, round(width * ratio))
                cell_h = max(1, round(width * aspect * ratio))
                cells.append(tile_markers(image, cell_w, cell_h, [(cell_w / 2.0, cell_h / 2.0)]))
        error = abs(round(width) - width) / width
        return self._textures(cells[0], cells[1], error, "SVG fill")

    @staticmethod
    def _rotated_texture(tile, lattice, ratio: int):
        """``tile`` repeated along the lattice axes ``u``, ``v`` over a
        seamless ``W`` × ``H`` cell (see ``rotated_lattice_cell``)."""
        from qgis.PyQt.QtCore import QRectF  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtGui import QBrush, QImage, QPainter, QTransform  # pylint: disable=import-outside-toplevel
        from .sprite_generator import SymbolImage  # pylint: disable=import-outside-toplevel
        width, height, u, v, _ = lattice
        source = QImage(tile.tobytes("raw", "RGBA"), tile.width, tile.height,
                        QImage.Format.Format_RGBA8888).copy()
        brush = QBrush(source)
        brush.setTransform(QTransform(u[0] * ratio / tile.width, u[1] * ratio / tile.width,
                                      v[0] * ratio / tile.height, v[1] * ratio / tile.height, 0, 0))
        image = QImage(width * ratio, height * ratio, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(QRectF(0, 0, width * ratio, height * ratio), brush)
        painter.end()
        return SymbolImage._qt_to_pil(image)  # pylint: disable=protected-access

    @staticmethod
    def _svg_cell(layer, cell_w: int, cell_h: int, stroke_px: float, per_mm: float):
        """One SVG fill cell: the (parametrized) SVG stretched over the cell."""
        from qgis.core import QgsApplication  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtCore import QRectF  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtGui import QImage, QPainter  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtSvg import QSvgRenderer  # pylint: disable=import-outside-toplevel
        from .sprite_generator import SymbolImage  # pylint: disable=import-outside-toplevel
        content = QgsApplication.svgCache().svgContent(
            layer.svgFilePath(), cell_w, layer.svgFillColor(), layer.svgStrokeColor(),
            stroke_px, per_mm)
        renderer = QSvgRenderer(content)
        if not renderer.isValid():
            return None
        image = QImage(cell_w, cell_h, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        renderer.render(painter, QRectF(0, 0, cell_w, cell_h))
        painter.end()
        return SymbolImage._qt_to_pil(image)  # pylint: disable=protected-access

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

    def _register_raster_line(self, layer) -> Optional[str]:
        """``line-pattern`` image of a raster line. MapLibre stretches the
        image height to the line width and repeats it along the line, as QGIS
        does; only its proportions matter. Its width is a power of two (at
        least 64 px), which MapLibre needs for a seamless repeat."""
        from qgis.core import QgsApplication  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtCore import QSize  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtGui import QImageReader  # pylint: disable=import-outside-toplevel
        from PIL import Image  # pylint: disable=import-outside-toplevel
        from .sprite_generator import SymbolImage  # pylint: disable=import-outside-toplevel
        path = layer.path()
        source = QImageReader(path).size() if path else None
        if not path or source is None or source.isEmpty():
            return None
        line_px = max(1.0, PropertyExtractor.static_pixels(layer.width(), layer.widthUnit()))
        natural = line_px * source.width() / source.height()  # one repeat on screen
        # QGIS scales the image to whole pixels before tiling it along the
        # line: a screen-size line repeats every round(natural) px.
        ratio = source.width() / source.height()
        if normalize_unit(layer.widthUnit()) not in ("map", "m") and natural >= 1:
            ratio = round(natural) / line_px
        width = 64
        while width < 512 and (width < natural or
                               abs(width / max(1, round(width / ratio)) - ratio) / ratio > 0.003):
            width *= 2  # whole-pixel proportions within 0.3 %
        height = max(1, round(width / ratio))
        error = abs(width / height - ratio) / ratio
        if error > 0.01:
            self.context.report("Q2VT_PATTERN_NONPERIODIC",
                                f"Raster line repeat differs by {error:.1%} (image proportions "
                                "rounded to whole pixels).", strategy=Strategy.APPROXIMATE.value)
        cells = []
        for scale in (1, 2):  # sprite pixel ratio
            image, _ = QgsApplication.imageCache().pathAsImage(
                path, QSize(width * scale, height * scale), False, 1.0, True)
            if image.isNull():
                self.context.report("Q2VT_SPRITE_RENDER_FAILED",
                                    "Raster line image could not be loaded.", detail=path)
                return None
            # QGIS draws the image's top on the left of the line's direction;
            # MapLibre puts the first row on the right (see LINEBURST_*).
            cell = SymbolImage._qt_to_pil(image).transpose(Image.Transpose.FLIP_TOP_BOTTOM)  # pylint: disable=protected-access
            if _enum_int(layer.penCapStyle()) != _enum_int(Qt.PenCapStyle.FlatCap):
                # QGIS starts the image where the round / square cap starts,
                # half the line width before the line; MapLibre at the line
                # start. One repeat is the image width, so half the line
                # width is half the image height: start the image there.
                from PIL import ImageChops  # pylint: disable=import-outside-toplevel
                cell = ImageChops.offset(cell, -round(height * scale / 2), 0)
            cells.append(cell)
        name = self._next_name("pattern")
        self.pattern_images[name] = PatternImages(cells[0], cells[1])
        return name

    # Rows of the lineburst image (its gradient across the line).
    LINEBURST_ROWS = 64
    # MapLibre's line-pattern puts the image's first row on the right of the
    # line's direction (measured against QGIS, see the tests).
    LINEBURST_FIRST_ROW_LEFT = False

    def _register_lineburst(self, layer) -> Optional[str]:
        """``line-pattern`` image of a lineburst: QGIS fills the stroke with
        a gradient across it, colour 1 on the left edge (in the line's
        direction) to colour 2 on the right edge, caps and joins included.
        The image's rows are that gradient; MapLibre stretches its height to
        the line width and draws caps and joins with it as QGIS does."""
        from PIL import Image  # pylint: disable=import-outside-toplevel
        from qgis.core import QgsGradientColorRamp  # pylint: disable=import-outside-toplevel
        two_colors = _enum_int(layer.gradientColorType(), 0) == 0
        ramp = layer.colorRamp() if not two_colors and layer.colorRamp() is not None \
            else QgsGradientColorRamp(layer.color(), layer.color2())
        if getattr(layer, "blurRadius", lambda: 0)():
            self.context.report("Q2VT_GRADIENT_APPROXIMATE",
                                "Lineburst blur is not applied.", strategy=Strategy.APPROXIMATE.value)
        cells = []
        for scale in (1, 2):
            rows = self.LINEBURST_ROWS * scale
            image = Image.new("RGBA", (64 * scale, rows))
            for row in range(rows):
                t = (row + 0.5) / rows
                color = ramp.color(t if self.LINEBURST_FIRST_ROW_LEFT else 1.0 - t)
                image.paste((color.red(), color.green(), color.blue(), color.alpha()),
                            (0, row, 64 * scale, row + 1))
            cells.append(image)
        name = self._next_name("pattern")
        self.pattern_images[name] = PatternImages(cells[0], cells[1])
        return name

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

    # Map-unit sprites: one image per integer zoom, drawn at that zoom's size
    # with this oversampling, so MapLibre scales it by 1/1.5 .. 2/1.5 (the
    # atlas has no mipmaps: an image shrunk several times aliases, and thin
    # outlines break into dots). Beyond max zoom + OVERZOOM_BANDS one image
    # with SPRITE_OVERZOOM_OVERSAMPLING covers the rest.
    SPRITE_BAND_OVERSAMPLING = 1.5
    SPRITE_OVERZOOM_OVERSAMPLING = 4.0
    OVERZOOM_BANDS = 3
    # Sprite budget: past this logical size a marker gets no further per-zoom
    # images (the sheet must fit a GPU texture); the last one is scaled up.
    MAX_BAND_SPRITE_PX = 128.0

    def _sprite_bands(self, min_zoom: float, max_zoom: float):
        """``[(low, high, oversampling)]`` zoom bands of a map-unit sprite."""
        low = max(float(min_zoom), 0.0) if min_zoom is not None and min_zoom >= 0 else 0.0
        high = float(max_zoom) if max_zoom is not None and max_zoom >= 0 else 24.0
        top = min(high, float(self.maxzoom + self.OVERZOOM_BANDS))
        bands, zoom = [], low
        while zoom < top - 1e-9:
            upper = min(math.floor(zoom) + 1.0, top)
            bands.append((zoom, upper, self.SPRITE_BAND_OVERSAMPLING))
            zoom = upper
        if high > zoom + 1e-9:
            bands.append((zoom, high, self.SPRITE_OVERZOOM_OVERSAMPLING))
        return bands

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
        """Convert a QGIS marker symbol into MapLibre ``symbol`` layer(s):
        one per zoom band for markers sized in map units."""
        if not IconPropertyExtractor.uses_map_units(symbol):
            self._marker_layer(symbol_layer, symbol, style_name, source_layer_name,
                               source_name, min_zoom, max_zoom)
            return
        context = self.context
        saved = (context.reference_zoom, context.reference_zoom_span, context.sprite_oversampling)
        try:
            bands = self._sprite_bands(min_zoom, max_zoom)
            growth, extent = IconPropertyExtractor.map_growth(symbol)
            for index, (low, high, quality) in enumerate(bands):
                if growth > 1.7 and index < len(bands) - 1 and extent * \
                        PropertyExtractor.static_pixels(1.0, "map", low) > self.MAX_BAND_SPRITE_PX:
                    # Large already: one last image, scaled up from here on.
                    high, quality = bands[-1][1], self.SPRITE_BAND_OVERSAMPLING
                    bands = bands[:index + 1]
                context.reference_zoom, context.reference_zoom_span = low, high - low
                context.sprite_oversampling = quality
                self._marker_layer(symbol_layer, symbol, f"{style_name}_z{int(low)}",
                                   source_layer_name, source_name, low, high)
                if index == len(bands) - 1:
                    break
        finally:
            context.reference_zoom, context.reference_zoom_span, \
                context.sprite_oversampling = saved

    def _marker_layer(
        self,
        symbol_layer: QgsSymbolLayer,
        symbol: QgsSymbol,
        style_name: str,
        source_layer_name: str,
        source_name: str,
        min_zoom: float = -1,
        max_zoom: float = -1,
    ):
        """One MapLibre ``symbol`` layer (and its sprite) for a marker symbol."""
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
            # Map symbols never hide labels in QGIS (only a labelled layer's
            # own obstacle settings do): they must not block labels here.
            "icon-ignore-placement": True,
            "icon-optional": IconPropertyExtractor.get_icon_optional(),
            "icon-keep-upright": IconPropertyExtractor.get_icon_keep_upright(),
            "symbol-placement": IconPropertyExtractor.get_symbol_placement(),
            "symbol-spacing": IconPropertyExtractor.get_symbol_spacing(),
            "symbol-avoid-edges": IconPropertyExtractor.get_symbol_avoid_edges(),
            "symbol-sort-key": IconPropertyExtractor.get_symbol_sort_key(),
            "symbol-z-order": IconPropertyExtractor.MARKER_Z_ORDER,
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
        if oversampling == 1.0:
            oversampling = None  # rotated along the line: keep the oversampled image
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
            "symbol-z-order": IconPropertyExtractor.MARKER_Z_ORDER,
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
            layer_def["paint"]["line-opacity"] = thin_line_opacity(
                width_value, layer_def["paint"]["line-opacity"])
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
        elif symbol_layer.layerType() in ("RasterLine", "Lineburst") and \
                (raster_pattern := (self._register_raster_line(symbol_layer)
                                    if symbol_layer.layerType() == "RasterLine"
                                    else self._register_lineburst(symbol_layer))):
            # QGIS draws the image along the line, its height the line width:
            # MapLibre's line-pattern fits the image height to line-width too.
            layer_def["paint"].update({
                "line-pattern": raster_pattern,
                "line-width": LinePropertyExtractor.get_line_width(symbol_layer),
                "line-opacity": ex.mul(PropertyExtractor.opacity(symbol, symbol_layer),
                                       float(getattr(symbol_layer, "opacity", lambda: 1.0)())),
            })
            offset = LinePropertyExtractor.get_line_offset(symbol_layer)
            if isinstance(offset, list) or (ex.is_number(offset) and offset != 0):
                layer_def["paint"]["line-offset"] = offset
            layer_def["layout"].update({
                "line-cap": LinePropertyExtractor.get_line_cap(symbol_layer),
                "line-join": LinePropertyExtractor.get_line_join(symbol_layer),
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

        role = getattr(self, "effect_roles", {}).get(getattr(self.context, "component", None))
        if role == "none":
            self.style["layers"].append(layer_def)
            return
        below, above = self._line_effect_layers(symbol_layer, layer_def)
        if role == "outer":  # the line itself is drawn by another component
            self.style["layers"].extend(below + above)
            return
        self.style["layers"].extend(below + [layer_def] + above)

    def _inner_effect_layers(self, style, spec: dict, bounds) -> None:
        """Inner shadow / glow of a line as strips across it: each strip a
        line at its offset whose colour is QGIS's rendering of the line with
        its inner effects at that offset, for the run's screen direction
        (mat.DIRECTION_FIELD). Under them, the runs at full width with
        round ends take QGIS's colour of a line end of their direction, so
        line ends and sharp turns are shaded too (earlier runs on top: a
        turn shows the end of the run arriving at it)."""
        def by_direction(values):
            expression = ["match", ["to-number", ["get", mat.DIRECTION_FIELD], 0]]
            for bucket, rgba in enumerate(values[1:], start=1):
                expression += [bucket, rgba]
            return expression + [values[0]]
        if spec.get("caps"):
            layer_def = self._base_layer_def(
                "line", style.styleName(), style.layerName(), self.source_name,
                bounds[0], bounds[1])
            layer_def["id"] = f"{layer_def['id']}_ends"
            layer_def["paint"].update({"line-color": by_direction(spec["caps"]),
                                       "line-width": spec["width"],
                                       "line-opacity": spec.get("opacity", 1.0)})
            layer_def["layout"].update({
                "line-cap": spec.get("cap", "round"), "line-join": "round",
                "line-sort-key": ["-", 0, ["to-number", ["get", mat.RUN_FIELD], 0]],
                "visibility": "visible"})
            self.style["layers"].append(layer_def)
        for index in spec.get("order") or range(len(spec["strips"])):
            offset, width = spec["strips"][index]
            layer_def = self._base_layer_def(
                "line", style.styleName(), style.layerName(), self.source_name,
                bounds[0], bounds[1])
            layer_def["id"] = f"{layer_def['id']}_in{index}"
            color = by_direction([row[index] for row in spec["colors"]])
            layer_def["paint"].update({"line-color": color, "line-width": width,
                                       "line-opacity": spec.get("opacity", 1.0)})
            if abs(offset) > 1e-9:
                layer_def["paint"]["line-offset"] = offset
            layer_def["layout"].update({"line-cap": spec.get("cap", "round"),
                                        "line-join": "round", "visibility": "visible"})
            self.style["layers"].append(layer_def)

    # Paint effects a line can carry in the browser (see _line_effect_layers).
    LINE_EFFECTS = ("QgsOuterGlowEffect", "QgsDropShadowEffect")

    @staticmethod
    def _effect_list(symbol_layer) -> list:
        effect = symbol_layer.paintEffect() if hasattr(symbol_layer, "paintEffect") else None
        if effect is None or not effect.enabled() or type(effect).__name__ == "QgsDefaultPaintEffect":
            return []
        if type(effect).__name__ == "QgsEffectStack":
            return [e for e in effect.effectList() if e.enabled()]
        return [effect]

    def _line_effect_layers(self, symbol_layer, layer_def: dict):
        """A simple line's outer glow and drop shadow as extra line layers
        under (or over) it, in the effect stack's order: the glow a wider,
        blurred line of the glow colour; the shadow the same line offset and
        blurred. QGIS draws effects as images; these are close equivalents."""
        if not isinstance(symbol_layer, QgsSimpleLineSymbolLayer):
            return [], []
        below, above = [], []
        target = below
        for index, effect in enumerate(self._effect_list(symbol_layer)):
            name = type(effect).__name__
            if name == "QgsDrawSourceEffect":
                target = above
                continue
            if name not in self.LINE_EFFECTS or _enum_int(effect.drawMode()) == 1:  # modifier only
                continue
            extra = copy.deepcopy(layer_def)
            extra["id"] = f"{layer_def['id']}_fx{index}"
            paint = extra["paint"]
            paint.pop("line-dasharray", None)
            blur = PropertyExtractor.static_pixels(effect.blurLevel(), effect.blurUnit())
            paint["line-opacity"] = ex.mul(paint.get("line-opacity", 1), float(effect.opacity()))
            if name == "QgsOuterGlowEffect":
                spread = PropertyExtractor.static_pixels(effect.spread(), effect.spreadUnit())
                color = effect.color() if _enum_int(effect.colorType()) == 0 or effect.ramp() is None \
                    else effect.ramp().color(0.0)
                paint["line-color"] = color.name()
                paint["line-width"] = ex.add(paint["line-width"], 2 * spread)
                paint["line-blur"] = spread + blur
            else:
                angle = math.radians(effect.offsetAngle())
                distance = PropertyExtractor.static_pixels(effect.offsetDistance(), effect.offsetUnit())
                # QgsShadowEffect::draw: offset (-d sin(a + 90°), -d cos(a + 90°)), y down.
                paint["line-translate"] = [round(-distance * math.sin(angle + math.pi / 2), 3),
                                           round(-distance * math.cos(angle + math.pi / 2), 3)]
                paint["line-translate-anchor"] = "viewport"
                paint["line-color"] = effect.color().name()
                paint["line-blur"] = blur
            target.append(extra)
        return below, above

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
                brush = self._register_brush_pattern(symbol_layer)
                if brush is None and _enum_int(symbol_layer.brushStyle()) != 1:  # not Qt.SolidPattern
                    self.context.report(
                        "Q2VT_PATTERN_APPROXIMATE",
                        "Qt brush pattern with a data-defined colour or style is drawn as a "
                        "solid fill.", strategy=Strategy.APPROXIMATE.value)
                layer_def["paint"].update({
                    "fill-color": FillPropertyExtractor.get_fill_color(symbol_layer),
                    "fill-opacity": FillPropertyExtractor.get_fill_opacity(symbol_layer, symbol),
                    "fill-antialias": FillPropertyExtractor.get_fill_antialias(),
                    "fill-translate": FillPropertyExtractor.get_fill_translate(),
                    "fill-translate-anchor": FillPropertyExtractor.get_fill_translate_anchor(),
                })
                offset = symbol_layer.offset()
                if (abs(offset.x()) > 1e-9 or abs(offset.y()) > 1e-9) and \
                        normalize_unit(symbol_layer.offsetUnit()) not in ("map", "m"):
                    # QGIS shifts the fill on screen (x right, y down).
                    layer_def["paint"]["fill-translate"] = [
                        PropertyExtractor.static_pixels(offset.x(), symbol_layer.offsetUnit()),
                        PropertyExtractor.static_pixels(offset.y(), symbol_layer.offsetUnit())]
                    layer_def["paint"]["fill-translate-anchor"] = "viewport"
                if brush is not None:
                    # The pattern image carries the colour; it starts at the
                    # corner of the view, as QGIS's brush.
                    del layer_def["paint"]["fill-color"]
                    layer_def["paint"]["fill-pattern"] = brush
                    metadata = layer_def.setdefault("metadata", {})
                    metadata[self.SCREEN_PATTERN_FLAG] = True
                    metadata[self.PATTERN_ANCHOR_FLAG] = "viewport"
                color_prop = symbol_layer.dataDefinedProperties().property(
                    QgsSymbolLayer.Property.PropertyFillColor)
                if color_prop and color_prop.isActive():
                    # Materialized colour bands (gradient / shapeburst fills) are
                    # drawn in band order; other features have no band (key 0).
                    layer_def["layout"]["fill-sort-key"] = ["to-number", ["get", mat.BAND_FIELD], 0]

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
            map_units = self._pattern_uses_map_units(symbol_layer)
            viewport = pattern_in_viewport(symbol_layer)
            if map_units and (kind != "LinePatternFill" or viewport):
                pattern_name = self._per_zoom_pattern(register, min_zoom, max_zoom)
            elif map_units:
                pattern_name = register()
            else:
                self._exact_screen_texture = True
                try:
                    pattern_name = register()
                finally:
                    self._exact_screen_texture = False
                if pattern_name is not None:
                    layer_def.setdefault("metadata", {})[self.SCREEN_PATTERN_FLAG] = True
            if viewport:
                # Starts at the corner of the view, as in QGIS (patched
                # MapLibre); otherwise patterns are anchored to the map.
                layer_def.setdefault("metadata", {})[self.PATTERN_ANCHOR_FLAG] = "viewport"
            elif pattern_anchor_kind(symbol_layer):
                # Starts at each feature's anchor (mat.PATTERN_ANCHOR_*_FIELD;
                # features without one keep the map-anchored pattern).
                layer_def.setdefault("metadata", {})[self.PATTERN_ANCHOR_FLAG] = \
                    pattern_anchor_kind(symbol_layer)
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
            "text-letter-spacing": TextPropertyExtractor.get_text_letter_spacing(text_format),
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
        elif variable_anchor and placement in ("Horizontal", "Free"):
            # Shifted candidates 1 em off the point: half a label (offset 0)
            # never clears a label centred on the same point (a zone and a
            # parcel that coincide have the same visible centroid).
            layer_def["layout"]["text-variable-anchor"] = variable_anchor
            layer_def["layout"]["text-radial-offset"] = 1
        elif variable_anchor:
            layer_def["layout"]["text-variable-anchor"] = variable_anchor
            layer_def["layout"]["text-radial-offset"] = \
                TextPropertyExtractor.get_text_radial_offset(label_settings, em_size)
        elif placement == "OverPoint":
            offset = TextPropertyExtractor.get_text_offset(label_settings, em_size)
            if offset != [0, 0]:
                layer_def["layout"]["text-offset"] = offset
        elif placement in ("Line", "Curved"):
            offset = TextPropertyExtractor.get_line_text_offset(label_settings, em_size)
            if offset != [0, 0]:
                layer_def["layout"]["text-offset"] = offset
        if style_name in self.label_windows and not pinned:
            # Each feature is the window of one label, laid out as QGIS lays
            # it out: the label is centred on it ("line" would place its own
            # anchors along it, half a label + 2 em from its start).
            layer_def["layout"]["symbol-placement"] = "line-center"
            layer_def["layout"].pop("symbol-spacing", None)

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

        if not pinned and TextPropertyExtractor.overlap_if_required(label_settings):
            # Avoids other labels; the viewer still draws it where it cannot
            # (resources/ml_viewer/visible_labels.mjs, enableOverlapFallback).
            layer_def.setdefault("metadata", {})["q2vt:overlap"] = "if-required"
        polygons = self.visible_polygons.get(style_name)
        if polygons:
            # The viewer moves these labels to the visible part of their
            # polygon (QGIS "Centroid: visible polygon"); other clients draw
            # the static whole-polygon centroids.
            layer_def.setdefault("metadata", {}).update({
                "q2vt:visible-polygons": polygons[0],
                "q2vt:label-per-part": polygons[1],
                # Horizontal / Free: QGIS puts the label where it has the most
                # room (pole of inaccessibility); around / over point: centroid.
                "q2vt:label-anchor": "pole" if placement in ("Horizontal", "Free") else "centroid",
                # Who gets the best spot when labels compete (QGIS priority,
                # then z-index): the viewer places these labels in that order.
                "q2vt:label-rank": [label_settings.priority, label_settings.zIndex],
                # Free (angled): horizontal where the label fits inside its
                # polygon, else turned along the polygon (QGIS); the viewer
                # computes the angle unless a rotation is set in QGIS.
                "q2vt:label-orient": "free" if placement == "Free" and not self._label_rotated(
                    label_settings) else "horizontal",
                # "line": a line label put at the middle of the line's visible part.
                "q2vt:visible-kind": polygons[2] if len(polygons) > 2 else "polygon",
            })
            around = self._label_around(label_settings, placement)
            if around:
                layer_def["metadata"]["q2vt:label-around"] = around
            char_width = self._char_width(label_settings)
            if char_width:
                # The font's mean advance per character (em): the viewer's
                # label boxes (a generic 0.6 em made narrow fonts too wide).
                layer_def["metadata"]["q2vt:char-width"] = char_width
            if font and self._register_font_metrics(text_format, font):
                # Each character's own advance: a label of narrow letters
                # ("t_felirat") is up to a fifth shorter than the mean says,
                # which decides whether it fits in its polygon.
                layer_def["metadata"]["q2vt:font"] = font
        self.style["layers"].extend(self._line_label_zoom_split(layer_def))

    # MapLibre checks that a line label fits along its line with the
    # text-size evaluated at zoom 18 (symbol_layout.ts, textMaxSize), whatever
    # the tile zoom; map-unit text doubles per zoom, so below z18 labels that
    # fit are dropped.
    LINE_LABEL_FIT_ZOOM = 18

    @staticmethod
    def _label_around(label_settings: QgsPalLayerSettings, placement: str) -> Optional[dict]:
        """For an "around point" polygon label placed by the viewer: the
        candidate positions (MapLibre anchor names, in QGIS's order) and the
        QGIS label distance from the point, in pixels at ``zoom`` (map-unit
        distances scale with the zoom; otherwise the size is fixed)."""
        if placement not in ("AroundPoint", "OrderedPositionsAroundPoint"):
            return None
        anchors = TextPropertyExtractor.get_text_variable_anchor(label_settings)
        try:
            distance = float(label_settings.dist)
        except (AttributeError, TypeError, ValueError):
            distance = 0.0
        pixels = PropertyExtractor.length(distance, label_settings.distUnits) if distance else 0.0
        around = {"anchors": anchors}
        if ex.is_number(pixels):
            around["px"] = round(max(0.0, float(pixels)), 3)
        else:
            zoom = float(PropertyExtractor.context.reference_zoom)
            around["px"] = round(max(0.0, float(ex.evaluate_zoom_curve(pixels, zoom))), 3)
            around["zoom"] = zoom  # map units: doubles with each zoom level
        return around

    def _line_label_zoom_split(self, layer_def: dict) -> list:
        """A label with a zoom-curve ``text-size`` as one style layer per
        integer zoom where MapLibre needs it (the sizes it draws are
        unchanged):

        * line placement below zoom 18: the curve gets a stop at zoom 18 back
          at the tile-zoom size, for MapLibre's line fit check;
        * a text-fitted frame: ``icon-size`` 0.5 -> 1 over the layer's zoom
          (MapLibre fits the frame to text shaped at tile zoom + 1, and reads
          a size curve only at the stops covering [tile zoom, +1], so one
          sawtooth curve for all zooms stays at 0.5 and the frame did not
          grow between integer zooms).
        """
        frame = layer_def.pop("_q2vt_frame", None)  # not JSON (and not deep-copyable)
        layout = layer_def["layout"]
        size = layout.get("text-size")
        if not ex.is_zoom_curve(size) or size[0] != "interpolate":
            return [layer_def]
        line = layout.get("symbol-placement") in ("line", "line-center")
        framed = "icon-text-fit" in layout
        if not line and not framed:
            return [layer_def]
        try:
            values = {z: ex.evaluate_zoom_curve(size, z) for z in range(0, 25)}
        except ex.ExpressionError:
            if line:
                return [layer_def]
            # A data-defined size: the frame still needs its zoom's image and
            # icon-size ramp (a map-unit border was lost in one image).
            values = None
        low = layer_def.get("minzoom", 0)
        high = layer_def.get("maxzoom", 24)
        fit = self.LINE_LABEL_FIT_ZOOM
        top = fit if not framed else min(24, max(fit, int(self.maxzoom) + 4))
        if low >= top:
            return [layer_def]
        out = []
        for zoom in range(int(math.floor(low)), min(int(math.ceil(high)), top)):
            part = copy.deepcopy(layer_def)
            part["id"] = f"{layer_def['id']}_z{zoom}"
            part["minzoom"] = max(low, zoom)
            part["maxzoom"] = min(high, zoom + 1)
            if line:
                curve = ["interpolate", list(size[1]), ["zoom"],
                         zoom, values[zoom], zoom + 1, values[zoom + 1]]
                if zoom + 1 < fit:
                    # Drawing reads only the stops covering [tile zoom, +1]
                    # (symbol_size.ts); the zoom-18 fit check then sees the
                    # size drawn at the tile zoom, so a label that fits the
                    # (tile-clipped) line is kept.
                    curve += [fit, values[zoom]]
                part["layout"]["text-size"] = curve
            if framed:
                part["layout"]["icon-size"] = ["interpolate", ["exponential", 2], ["zoom"],
                                               zoom, 0.5, zoom + 1, 1.0]
                if frame is not None:
                    part["layout"]["icon-image"] = self._map_unit_frame(frame, zoom)
            out.append(part)
        if high > top:
            rest = copy.deepcopy(layer_def)
            rest["minzoom"] = max(low, top)
            if frame is not None:
                rest["layout"]["icon-image"] = self._map_unit_frame(frame, top)
            out.append(rest)
        return out

    def _map_unit_frame(self, background, zoom: int) -> Optional[str]:
        """Frame image with the map-unit stroke for [zoom, zoom + 1).

        A text-fitted rectangle's border lies in the image's fixed (not
        stretched) margin, which MapLibre draws in image pixels whatever the
        icon-size: the border cannot grow within the zoom, so it is drawn at
        the stroke of zoom + 0.5, within sqrt(2) of QGIS's at either end (a
        doubled stroke was twice QGIS's at the whole zoom). An ellipse is
        scaled whole with the icon (icon-size 0.5 -> 1): its stroke stays
        doubled."""
        ellipse = _enum_int(background.type(), 0) in (2, 3)
        stroke = PropertyExtractor.static_pixels(background.strokeWidth(),
                                                 background.strokeWidthUnit(),
                                                 reference_zoom=zoom if ellipse else zoom + 0.5)
        return self._background_image(background, stroke_px=2.0 * stroke if ellipse else stroke)

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

    def _background_image(self, background, stroke_px: Optional[float] = None) -> Optional[str]:
        """Sprite for a label background shape (rectangle/ellipse/SVG/marker).
        ``stroke_px`` overrides the frame stroke (per-zoom map-unit frames)."""
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
        if stroke_px is None:
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

    @staticmethod
    def _scaled_padding(padding, factor: float):
        """``icon-text-fit-padding`` (list or zoom curve of literals) × factor."""
        if isinstance(padding, list) and padding and padding[0] == "literal":
            return ["literal", [round(v * factor, 4) for v in padding[1]]]
        if ex.is_zoom_curve(padding):
            out = list(padding[:3])
            for zoom, value in zip(padding[3::2], padding[4::2]):
                out += [zoom, QgisMapLibreStyleExporter._scaled_padding(value, factor)]
            return out
        if isinstance(padding, list) and all(ex.is_number(v) for v in padding):
            return [round(v * factor, 4) for v in padding]
        return padding

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
        if text_fit_padding and label_settings is not None:
            text_fit_padding = self._frame_fit_padding(text_fit_padding, label_settings.format(),
                                                       layer_def["layout"])
        if text_fit_padding:
            layer_def["layout"]["icon-text-fit-padding"] = text_fit_padding

        layer_def["layout"].update({
            "icon-anchor": IconPropertyExtractor.get_icon_anchor(),
            "icon-rotate": self._background_rotate(background, layer_def["layout"], layer_def),
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
            if isinstance(image, str) and _enum_int(background.strokeWidthUnit(), 0) == 1 \
                    and background.strokeWidth() > 0:
                # Map-unit frame stroke: one frame image per zoom
                # (_line_label_zoom_split); one image drew a hairline.
                from qgis.core import QgsTextBackgroundSettings  # pylint: disable=import-outside-toplevel
                layer_def["_q2vt_frame"] = QgsTextBackgroundSettings(background)
            if ex.is_zoom_curve(layer_def["layout"].get("text-size")):
                layer_def["layout"]["icon-size"] = self._text_fit_icon_size()
                padding = self._text_fit_padding_curve(background)
                if padding is None:
                    padding = layer_def["layout"].get("icon-text-fit-padding")
                if padding is not None:
                    # MapLibre fits the frame to the text shaped at tile
                    # zoom + 1 but reads the padding at the tile zoom; the
                    # frame is then halved by icon-size: double the padding.
                    layer_def["layout"]["icon-text-fit-padding"] = self._scaled_padding(padding, 2.0)
        layer_def["paint"].update({
            "icon-opacity": IconPropertyExtractor.get_icon_opacity(background),
            "icon-halo-blur": IconPropertyExtractor.get_icon_halo_blur(),
            "icon-translate": IconPropertyExtractor.get_icon_translate(),
            "icon-translate-anchor": IconPropertyExtractor.get_icon_translate_anchor(),
        })

    # Text whose mean character width stands for label text (lower case,
    # Hungarian accents, digits, spaces and punctuation).
    _CHAR_SAMPLE = "the quick brown fox jumps over the lazy dog, árvíztűrő tükörfúrógép 1203/4"

    @classmethod
    def _char_width(cls, label_settings) -> Optional[float]:
        """Mean advance per character of the label font, in em (Qt's font
        metrics, as QGIS measures labels), rounded to 0.01."""
        try:
            font = QFont(label_settings.format().font())
            font.setPixelSize(100)
            from qgis.PyQt.QtGui import QFontMetricsF  # pylint: disable=import-outside-toplevel
            advance = QFontMetricsF(font).horizontalAdvance(cls._CHAR_SAMPLE)
        except (AttributeError, RuntimeError, TypeError):
            return None
        width = advance / 100.0 / len(cls._CHAR_SAMPLE)
        return round(width, 2) if 0.2 <= width <= 1.2 else None

    # Characters whose advances the viewer gets: ASCII, Latin-1 and Latin
    # Extended-A (Hungarian ő, ű...); any other counts as the mean width.
    _METRIC_CHARS = "".join(map(chr, range(32, 127))) + "".join(map(chr, range(160, 384)))

    def _register_font_metrics(self, text_format, font_name: str) -> bool:
        """The label font's advance per character and its line height, in
        em (Qt's font metrics, as QGIS measures labels), stored once per
        font in the style's metadata["q2vt:font-metrics"][font_name]:
        {"chars", "advances" (thousandths of an em, one per char), "height"}."""
        fonts = self.style.setdefault("metadata", {}).setdefault("q2vt:font-metrics", {})
        if font_name in fonts:
            return True
        try:
            from qgis.PyQt.QtGui import QFontMetricsF  # pylint: disable=import-outside-toplevel
            metrics = QFontMetricsF(self._metrics_font(text_format))
            advances = [round(metrics.horizontalAdvance(char)) for char in self._METRIC_CHARS]
            height = metrics.height() / 1000.0
        except (AttributeError, RuntimeError, TypeError):
            return False
        if not 0.5 <= height <= 3 or not any(advances):
            return False
        fonts[font_name] = {"chars": self._METRIC_CHARS, "advances": advances, "height": round(height, 3)}
        return True

    @staticmethod
    def _metrics_font(text_format) -> QFont:
        """The label font QGIS draws (its named style, the B/I buttons on
        top) at 1000 px: Qt's font metrics in thousandths of an em."""
        from qgis.core import QgsFontUtils  # pylint: disable=import-outside-toplevel
        named = QgsTextFormat(text_format)
        if text_format.namedStyle():
            font = QFont(text_format.font())
            QgsFontUtils.updateFontViaStyle(font, text_format.namedStyle())
            named.setFont(font)
        font = TextPropertyExtractor.drawn_font(named)  # the B/I buttons on top
        font.setPixelSize(1000)
        return font

    def _frame_fit_padding(self, padding, text_format, layout):
        """``icon-text-fit-padding`` around QGIS's text box. MapLibre fits a
        frame to its own line box (text-line-height ems, the baseline 7/24 em
        below its middle); QGIS's frame wraps the font's ascent and descent.
        The difference goes into the top and bottom padding. Map-unit text
        (a text-size zoom curve) keeps the bare buffer."""
        size = layout.get("text-size")
        if not ex.is_number(size) or not (isinstance(padding, list) and len(padding) == 4
                                          and all(ex.is_number(v) for v in padding)):
            return padding
        try:
            from qgis.PyQt.QtGui import QFontMetricsF  # pylint: disable=import-outside-toplevel
            metrics = QFontMetricsF(self._metrics_font(text_format))
            ascent, descent = metrics.ascent() / 1000.0, metrics.descent() / 1000.0
        except (AttributeError, RuntimeError, TypeError):
            return padding
        if not 0.5 <= ascent + descent <= 3:
            return padding
        line_height = layout.get("text-line-height", 1.2)
        half = (line_height if ex.is_number(line_height) else 1.2) / 2
        top = (ascent - half - _MAPLIBRE_BASELINE_BELOW_MIDDLE_EM) * size
        bottom = (descent - half + _MAPLIBRE_BASELINE_BELOW_MIDDLE_EM) * size
        return [round(padding[0] + top, 4), padding[1], round(padding[2] + bottom, 4), padding[3]]

    @staticmethod
    def _background_rotate(background, layout: dict, layer_def: dict):
        """``icon-rotate`` of a label background: QGIS turns it with the
        label (*Sync with label*), by its own angle on top (*Offset of
        label*) or only by its own angle (*Fixed*). A turning background
        is marked for the viewer, which sets the angle of Free labels."""
        own = IconPropertyExtractor.get_icon_rotate(background=background)
        kind = _enum_int(background.rotationType(), 0)
        if kind == 2:
            return own
        layer_def.setdefault("metadata", {})["q2vt:icon-follows-text"] = \
            own if kind == 1 and ex.is_number(own) else 0
        text = layout.get("text-rotate", 0)
        if kind == 0 or not own:
            return text
        if ex.is_number(text):
            return text + own
        return ["+", text, own]

    @staticmethod
    def _label_rotated(label_settings) -> bool:
        """A fixed or data-defined label rotation set in QGIS (it wins over
        the Free placement's own angle)."""
        if label_settings.angleOffset:
            return True
        prop = label_settings.dataDefinedProperties().property(
            QgsPalLayerSettings.Property.LabelRotation)
        return bool(prop and prop.isActive())

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
        def names(value):
            # Image names, also inside match/step/case expressions.
            if isinstance(value, str):
                return {value}
            if isinstance(value, list):
                return set().union(*(names(v) for v in value)) if value else set()
            return set()
        kept = []
        for layer_def in self.style["layers"]:
            images = names([(layer_def.get("layout") or {}).get("icon-image"),
                            (layer_def.get("paint") or {}).get("fill-pattern"),
                            (layer_def.get("paint") or {}).get("line-pattern")])
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
        self._unique_layer_ids()
        rounded_style = self.round_numeric_values(self.style)
        self.style = rounded_style
        filepath = os.path.join(style_dir, filename)
        with open(filepath, "w", encoding="utf8") as f:
            json.dump(rounded_style, f, indent=indent, ensure_ascii=False)
        return filepath

    def _unique_layer_ids(self) -> None:
        """MapLibre rejects a whole style with a repeated layer id (no map at
        all): a repeat is renamed with a suffix and reported."""
        seen = set()
        for layer_def in self.style["layers"]:
            base = layer_def["id"]
            new, count = base, 2
            while new in seen:
                new, count = f"{base}_{count}", count + 1
            if new != base:
                self.diagnostics.add("Q2VT_STYLE_DUPLICATE_ID",
                                     f"Style layer id '{base}' repeated; renamed '{new}'.")
                layer_def["id"] = new
            seen.add(new)

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
