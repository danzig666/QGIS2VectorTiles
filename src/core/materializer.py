"""
materializer.py

SymbolMaterializer — rewrites flattened rules whose symbol component needs
materialized geometry (see ``fidelity/materialize.py``) or a conversion to
a simpler, natively supported symbol:

* Marker lines with vertex / first / last / inner-vertex / central-point /
  segment-centre placements → point features at the exact QGIS positions,
  rendered with the marker sub-symbol rotated by the line azimuth. Interval
  placement stays a native repeated line symbol.
* Hashed lines → an equivalent marker line of short line markers.
* Arrows → a native line body plus arrow-head markers at the line ends.
* Filled lines with a simple fill → a native line.
* Line-pattern fills spaced in map units → the hatch lines themselves,
  clipped to each polygon (exact at every zoom).

Each rewrite reports what is approximated. The flattened rule's ``m``
attribute keeps output datasets of derived components apart.
"""

import bisect
import math
from typing import Dict, List, Optional, Tuple

from qgis.core import (
    Qgis,
    QgsLineSymbol,
    QgsLineSymbolLayer,
    QgsMarkerLineSymbolLayer,
    QgsMarkerSymbol,
    QgsProject,
    QgsProperty,
    QgsPropertyCollection,
    QgsSimpleLineSymbolLayer,
    QgsSimpleMarkerSymbolLayer,
    QgsSimpleMarkerSymbolLayerBase,
    QgsSymbol,
    QgsSymbolLayer,
    QgsSymbolLayerUtils,
    QgsUnitTypes,
)
from qgis.PyQt.QtCore import QPointF
from qgis.PyQt.QtGui import QColor

from ..utils.config import Qt
from ..utils.flattened_rule import FlattenedRule
from ..utils.zoom_levels import ZoomLevels
from .fidelity.model import MAX_STYLE_ZOOM, ZoomInterval
from .fidelity import zoom as fidelity_zoom
from .fidelity.qgis_expr import SCALE_VARIABLES, uses_map_scale
from .fidelity import materialize as mat
from .fidelity.diagnostics import DiagnosticCollector
from .fidelity.units import MERCATOR_UNITS_PER_DEGREE, physical_factor, normalize_unit, MM, PERCENT
from .fidelity.zoom import WEB_MERCATOR_TOP_SCALE


def replace_symbol_layer(symbol, layer, index: int = 0) -> None:
    """``symbol.changeSymbolLayer(index, layer)`` without a dangling wrapper.

    changeSymbolLayer() deletes the old layer in C++ while Python may still
    hold its wrapper (the flattener's clone layer). SIP then hands that
    stale wrapper, of the old class, back for the next object allocated at
    the same address. Taking the layer out gives it to Python instead: it
    is deleted with its last wrapper."""
    old = symbol.takeSymbolLayer(index)
    symbol.insertSymbolLayer(index, layer)
    del old


# QgsSymbol opacity property key: "PropertyOpacity" up to 3.3x, "Opacity" later.
_SYMBOL_OPACITY_KEY = getattr(QgsSymbol.Property, "Opacity",
                              getattr(QgsSymbol.Property, "PropertyOpacity", None))


def inherit_opacity(parent, symbol):
    """``symbol`` drawn as the sub-symbol of ``parent``: QGIS multiplies the
    parent's opacity (its data-defined Opacity when set, 0-100) into the
    sub-symbol's (QgsMarkerLineSymbolLayer::renderPolyline,
    QgsPointPatternFillSymbolLayer, pattern images...). Returns ``symbol``."""
    if parent is None or symbol is None:
        return symbol
    props = (parent.dataDefinedProperties().property(_SYMBOL_OPACITY_KEY),
             symbol.dataDefinedProperties().property(_SYMBOL_OPACITY_KEY))
    if not any(prop is not None and prop.isActive() for prop in props):
        symbol.setOpacity(symbol.opacity() * parent.opacity())
        return symbol
    # A data-defined value replaces the symbol's own opacity (a NULL keeps
    # it): one expression for both, the static opacity left at 1.
    terms = [f"coalesce(({prop.asExpression()}), {round(owner.opacity() * 100.0, 6)!r})"
             if prop is not None and prop.isActive()
             else repr(round(owner.opacity() * 100.0, 6))
             for prop, owner in zip(props, (parent, symbol))]
    symbol.setDataDefinedProperty(_SYMBOL_OPACITY_KEY, QgsProperty.fromExpression(
        f"{terms[1]} * {terms[0]} / 100"))
    symbol.setOpacity(1.0)
    return symbol


def _flag_names(flags) -> set:
    try:
        value = int(flags)
    except (TypeError, ValueError):
        value = getattr(flags, "value", 0)
    return {m.name for m in Qgis.MarkerLinePlacement if value & int(m)}


def pattern_in_viewport(layer) -> bool:
    """Whether a pattern fill starts at the corner of the view ("Align
    pattern to: Viewport"; raster fills: "Coordinate mode: Viewport") rather
    than at each feature's corner (the default)."""
    getter = "coordinateMode" if layer.layerType() == "RasterFill" else "coordinateReference"
    try:
        return getattr(layer, getter)() == Qgis.SymbolCoordinateReference.Viewport
    except AttributeError:
        return False


def _to_mm(value: float, unit) -> Optional[float]:
    """Physical length in mm, or None for map units / unknown units."""
    factor = physical_factor(normalize_unit(unit))
    if factor is None:
        return None
    return value * factor / physical_factor(MM)


def _limited(scale) -> bool:
    """Whether a QgsMapUnitScale limits its map-unit length (min/max size in
    mm or a scale range)."""
    return bool(scale.minScale or scale.maxScale or scale.minSizeMMEnabled
                or scale.maxSizeMMEnabled)


def _enum_value(value) -> int:
    return int(getattr(value, "value", value))


def _ring_filter(layer) -> int:
    """QgsLineSymbolLayer ring filter: 0 all rings, 1 exterior, 2 interior."""
    try:
        return int(getattr(layer.ringFilter(), "value", layer.ringFilter()))
    except (AttributeError, TypeError, ValueError):
        return 0


def pattern_anchor_kind(layer) -> str:
    """Where QGIS starts a feature-aligned pattern fill (measured in QGIS
    3.34, rounded to whole pixels): point, line and SVG patterns at the
    bottom-left of the feature's bounding box ("feature"), raster image
    fills at the top-left of each part, clipped to the view grown by 10 %
    ("feature-clip"); "" for viewport-aligned and other fills."""
    if layer.layerType() not in ("LinePatternFill", "PointPatternFill", "SVGFill", "RasterFill") \
            or pattern_in_viewport(layer):
        return ""
    return "feature-clip" if layer.layerType() == "RasterFill" else "feature"


def svg_fill_draws(layer) -> bool:
    """Whether QGIS paints an SVG fill: like ``QgsSVGFillSymbolLayer::
    storeViewBox`` the SVG data must parse (an empty path has no data; a
    missing file yields QGIS' placeholder, which it does draw)."""
    from qgis.core import QgsApplication  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtSvg import QSvgRenderer  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtCore import QByteArray  # pylint: disable=import-outside-toplevel
    path = layer.svgFilePath()
    try:
        if path:
            data = QgsApplication.svgCache().getImageData(path)
        else:  # built from embedded data (QgsSVGFillSymbolLayer::create)
            data = QByteArray.fromHex(str(layer.properties().get("data", "")).encode())
    except (AttributeError, TypeError):
        return True
    return bool(data) and QSvgRenderer(data).isValid()


class SymbolMaterializer:
    """Rewrite flattened rules into materialized or simplified components."""

    # The browser's default zoom limit (MapLibre's maxZoom; the Publish
    # window's default too): overzoomed tiles are shown up to it.
    VIEW_MAX_ZOOM = 22

    def __init__(self, diagnostics: DiagnosticCollector, max_zoom: int = 24,
                 fast_markers: bool = False, min_zoom: int = 0,
                 overzoom_top: Optional[int] = None, overzoom_to: Optional[int] = None):
        self.diagnostics = diagnostics
        self.max_zoom = max_zoom
        # The last zoom the map is viewed at (the browser overzooms the last
        # tiles up to it; max_zoom when the map stops there): rules evaluated
        # per zoom (scale-dependent values, screen sizes held in map units)
        # go on past max_zoom on the last tiles' data up to it, so they keep
        # their size while overzooming. ``overzoom_to`` is the same value.
        top = overzoom_top if overzoom_top is not None else overzoom_to
        top = self.VIEW_MAX_ZOOM if top is None else top
        self.overzoom_top = int(min(max(max_zoom, top), MAX_STYLE_ZOOM))
        # The export's first zoom: rules are materialized before the flattener
        # clamps their zoom range, so a rule without a scale limit still
        # starts at zoom 0 here (see _zoom_range).
        self.min_zoom = min_zoom
        # Screen-size interval markers placed by the browser (no per-zoom
        # positions): the publishing "fast marker lines" option.
        self.fast_markers = fast_markers
        crs = QgsProject.instance().crs()
        self.project_crs = crs.authid() if crs.isValid() else ""

    # -- helpers -----------------------------------------------------------
    def _zoom_range(self, rule: FlattenedRule) -> Tuple[int, int]:
        """(first, last) zoom of a rule within the export's zooms (first >
        last: none of them)."""
        return max(rule.get_attr("o"), self.min_zoom), min(rule.get_attr("i"), self.max_zoom)

    @staticmethod
    def distinguish(rules: List[FlattenedRule]) -> None:
        """Rules split from one rule that share a name (pieces with the same
        tile zooms, beyond the last zoom) get distinct ones: attribute "z",
        the hundredths of the zoom they start at. Kept from export to export
        (the export cache finds their tiles by name), whatever other layers
        hold."""
        seen = set()
        for rule in rules:
            name = rule.rule.description()
            if name in seen:
                start = rule.visibility.min_zoom if rule.visibility is not None else 0.0
                value = int(round(start * 100))
                rule.set_attr("z", value)
                while rule.rule.description() in seen:
                    value += 1
                    rule.set_attr("z", value)
                name = rule.rule.description()
            seen.add(name)

    def _report(self, code: str, message: str, flat_rule: FlattenedRule, **extra):
        self.diagnostics.add(code, message, layer_id=flat_rule.layer.id(),
                             rule_id=flat_rule.rule_id, **extra)

    @staticmethod
    def _with_symbol(flat_rule: FlattenedRule, symbol, geometry_type: int, index: int,
                     recipe=None) -> FlattenedRule:
        derived = flat_rule.derive()
        derived.rule.setSymbol(symbol)
        derived.set_attr("c", geometry_type)
        derived.set_attr("m", index)
        derived.recipe = recipe
        return derived

    # -- entry point -------------------------------------------------------
    def materialize(self, flat_rule: FlattenedRule, layer: QgsSymbolLayer) -> Optional[List[FlattenedRule]]:
        """Return replacement rules for ``flat_rule`` (whose symbol holds only
        ``layer``), or None when the component needs no rewrite."""
        kind = layer.layerType()
        limited = self._split_map_unit_limits(flat_rule, layer)
        if limited is not None:
            return limited
        if kind in ("MarkerLine", "HashLine"):
            per_zoom = self._split_scale_dependent(flat_rule, layer)
            if per_zoom is not None:
                return per_zoom
            # Constant expressions (no field, no variable) become static.
            self._fold_constant_ddp(layer, QgsSymbolLayer.Property.PropertyInterval,
                                    layer.setInterval)
            self._fold_constant_ddp(layer, QgsSymbolLayer.Property.PropertyOffsetAlongLine,
                                    layer.setOffsetAlongLine)
        if kind == "SVGFill":
            return self._svg_fill(flat_rule, layer)
        if kind == "FontMarker":
            return self._glyph_marker(flat_rule, layer)
        if kind == "SimpleLine":
            inside = self._inside_polygon(flat_rule, layer)
            if inside is not None:
                return inside
            effects = self._line_effects(flat_rule, layer)
            if effects is not None:
                return effects
            dashes = self._dash_segments(flat_rule, layer)
            if dashes is not None:
                return dashes
            if flat_rule.get_attr("g") == 1 and abs(layer.offset()) > 1e-9 \
                    and normalize_unit(layer.offsetUnit()) == "map":
                return self._offset_line(flat_rule, layer)
            if flat_rule.get_attr("g") == 1 and abs(layer.offset()) > 1e-9 \
                    and _to_mm(layer.offset(), layer.offsetUnit()) is not None:
                offsets = self._screen_offset_line(flat_rule, layer)
                if offsets is not None:
                    return offsets
        if kind in ("SimpleLine", "MarkerLine", "HashLine") and flat_rule.get_attr("g") == 2 \
                and (abs(layer.offset()) > 1e-9 or _ring_filter(layer)):
            outline = self._polygon_outline_offset(flat_rule, layer)
            if outline is not None:
                return outline
        if kind == "HashLine":
            layer = self._hash_as_marker_line(layer, flat_rule)
            replace_symbol_layer(flat_rule.rule.symbol(), layer)
            kind = "MarkerLine"
        if kind == "MarkerLine":
            return self._marker_line(flat_rule, layer)
        if kind == "ArrowLine":
            return self._arrow(flat_rule, layer)
        if kind == "FilledLine":
            return self._filled_line(flat_rule, layer)
        if kind == "InterpolatedLine":
            return self._interpolated_line(flat_rule, layer)
        if kind == "LinePatternFill" and normalize_unit(layer.distanceUnit()) in ("map", "m") \
                and not pattern_in_viewport(layer):
            # Hatch lines start at the feature; a viewport-aligned hatch stays
            # a texture, anchored at the view's corner in the browser.
            return self._hatch(flat_rule, layer)
        if kind == "RandomMarkerFill":
            return self._random_fill(flat_rule, layer)
        if kind == "GradientFill":
            return self._gradient(flat_rule, layer)
        if kind == "ShapeburstFill":
            return self._shapeburst(flat_rule, layer)
        if kind == "PointPatternFill" and self._whole_marker_pattern(layer):
            grid = self._screen_point_grid(flat_rule, layer)
            if grid is not None:
                return grid
        if kind == "PointPatternFill" and normalize_unit(layer.distanceXUnit()) == "map" \
                and normalize_unit(layer.distanceYUnit()) == "map" \
                and not self._tiling_pattern(layer):
            return self._dense_split(flat_rule, min(layer.distanceX(), layer.distanceY()),
                                     lambda rule: self._point_grid(rule, layer),
                                     elements=self._grid_elements(flat_rule, layer),
                                     what="Point pattern")
        return None

    _SCALE_FOLDED = (QgsSymbolLayer.Property.PropertyInterval,
                     QgsSymbolLayer.Property.PropertyOffsetAlongLine)

    def _split_scale_dependent(self, flat_rule: FlattenedRule, layer):
        """Marker-line interval / offset along the line that depend on the
        map scale only (``CASE WHEN @map_scale > 3000 THEN 10 ELSE 3 END``):
        one rule per zoom, with the value at that zoom's scale, so the
        markers can still be placed exactly (and per zoom beyond the last
        tile zoom while the value changes). None when not applicable."""
        from qgis.core import QgsExpression  # pylint: disable=import-outside-toplevel
        props = layer.dataDefinedProperties()
        scale_only = []
        for key in self._SCALE_FOLDED:
            prop = props.property(key)
            if prop is None or not prop.isActive():
                continue
            expression = QgsExpression(prop.asExpression())
            if expression.referencedColumns() - {""} or \
                    set(expression.referencedVariables()) - set(SCALE_VARIABLES):
                return None  # feature-dependent: not placed exactly
            if set(expression.referencedVariables()) & set(SCALE_VARIABLES):
                scale_only.append(key)
        low, high = self._zoom_range(flat_rule)
        if not scale_only:
            return None
        # Every zoom, beyond the last tile zoom too (its value was kept
        # there: a spacing switched at 1:3000 never switched in overzoom);
        # zooms beyond it valued alike are drawn by the same rule.
        groups = []  # [(rule, folded symbol layer, values)]
        for rule in self._per_zoom(flat_rule, overzoom=True):
            clone = rule.rule.symbol().symbolLayer(0)
            for key in scale_only:
                self._fold_constant_ddp(clone, key, self._setter(clone, key),
                                        fidelity_zoom.zoom_to_scale(rule.evaluation_zoom()))
            values = (clone.interval(), clone.offsetAlongLine(),
                      tuple(clone.dataDefinedProperties().isActive(key) for key in scale_only))
            if groups and rule.scale_zoom is not None and groups[-1][2] == values:
                groups[-1][0].visibility = self._visibility_hull(groups[-1][0].visibility,
                                                                 rule.visibility)
                continue
            groups.append((rule, clone, values))
        if len(groups) == 1 and low >= high:
            for key in scale_only:
                self._fold_constant_ddp(layer, key, self._setter(layer, key),
                                        ZoomLevels.zoom_to_scale(low))
            return None
        parts = []
        for rule, clone, _ in groups:
            result = self.materialize(rule, clone)
            parts.extend(result if result is not None else [rule])
        return parts

    # Map-unit lengths of materialized patterns and intervals whose
    # QgsMapUnitScale (min/max size in mm, scale range) QGIS applies at
    # every scale: (getter, data-defined property that overrides it).
    _LIMITED_LENGTHS = {
        "LinePatternFill": (("distance", "PropertyLineDistance"), ("offset", "PropertyOffset")),
        "PointPatternFill": (("distanceX", "PropertyDistanceX"), ("distanceY", "PropertyDistanceY"),
                             ("displacementX", "PropertyDisplacementX"),
                             ("displacementY", "PropertyDisplacementY"),
                             ("offsetX", "PropertyOffsetX"), ("offsetY", "PropertyOffsetY")),
        "MarkerLine": (("interval", "PropertyInterval"), ("offsetAlongLine", "PropertyOffsetAlongLine"),
                       ("offset", "PropertyOffset")),
        "HashLine": (("interval", "PropertyInterval"), ("offsetAlongLine", "PropertyOffsetAlongLine"),
                     ("offset", "PropertyOffset")),
        "SimpleLine": (("offset", "PropertyOffset"),),
    }

    @staticmethod
    def _brush_point_pattern(layer) -> bool:
        """Whether QGIS draws a point pattern with its pattern image brush
        (``QgsPointPatternFillSymbolLayer::startRender`` on screen), not
        marker by marker."""
        props = layer.dataDefinedProperties()
        P = QgsSymbolLayer.Property
        marker = layer.subSymbol()
        return not (
            (marker is not None and marker.hasDataDefinedProperties())
            or any(props.isActive(k) for k in (P.PropertyMarkerClipping, P.PropertyRandomOffsetX,
                                               P.PropertyRandomOffsetY, P.PropertyAngle))
            or int(layer.clipMode()) != int(Qgis.MarkerClipMode.Shape)
            or layer.maximumRandomDeviationX() or layer.maximumRandomDeviationY()
            or layer.angle())

    def _split_map_unit_limits(self, flat_rule: FlattenedRule, layer):
        """Map-unit lengths limited by their QgsMapUnitScale
        (``QgsRenderContext::convertToPainterUnits``: the scale range clamps
        the map units per pixel, then the size is clamped to min/max mm):
        between the limits a length follows the map, beyond them it is
        constant on screen. The rule is split at the zooms where a limit
        starts or ends; each part holds the plain map length (no limits) or
        the constant screen length in millimetres, and is materialized like
        any other. None when no such length."""
        from qgis.core import QgsMapUnitScale  # pylint: disable=import-outside-toplevel
        props = layer.dataDefinedProperties()
        limits = {}
        for name, key in self._LIMITED_LENGTHS.get(layer.layerType(), ()):
            try:
                value = float(getattr(layer, name)())
                unit = getattr(layer, f"{name}Unit")()
                scale = getattr(layer, f"{name}MapUnitScale")()
                if name == "distanceY" and self._brush_point_pattern(layer):
                    # QgsPointPatternFillSymbolLayer::applyPattern converts
                    # the row distance with the Y displacement's scale.
                    scale = layer.displacementYMapUnitScale()
            except (AttributeError, TypeError):
                continue
            if abs(value) <= 1e-12 or normalize_unit(unit) != "map" or not _limited(scale) or \
                    props.isActive(getattr(QgsSymbolLayer.Property, key)):
                continue
            limits[name] = (value, scale)
        low, high = self._zoom_range(flat_rule)
        if not limits or low > high:
            return None
        top_scale = ZoomLevels.zoom_to_scale(0) or 0.0
        px_per_unit0 = 1.0 / (self._map_units_per_mm(flat_rule, 0.0) * 25.4 / 96.0)
        px_per_mm = 96.0 / 25.4

        def bounds(value, scale):
            """(first zoom, last zoom, min px, max px) of a length's limits."""
            first = math.log2(top_scale / scale.minScale) if scale.minScale and top_scale \
                else -math.inf
            last = math.log2(top_scale / scale.maxScale) if scale.maxScale and top_scale \
                else math.inf
            return (first, last, scale.minSizeMM * px_per_mm if scale.minSizeMMEnabled else None,
                    scale.maxSizeMM * px_per_mm if scale.maxSizeMMEnabled else None)

        def regime(value, scale, zoom):
            """("map",): the length follows the map at ``zoom``; ("mm", v):
            QGIS draws it v millimetres long whatever the zoom nearby."""
            first, last, min_px, max_px = bounds(value, scale)
            px = value * px_per_unit0 * 2.0 ** min(max(zoom, first), last)
            clamped = px
            if min_px is not None:
                clamped = max(clamped, min_px)
            if max_px is not None:
                clamped = min(clamped, max_px)
            if first < zoom < last and clamped == px:
                return ("map",)
            return ("mm", round(clamped / px_per_mm, 9))

        visible = (flat_rule.visibility or ZoomInterval(float(low), None)).intersect(
            ZoomInterval(float(low), None))
        start, end = visible.min_zoom, visible.max_zoom
        cuts = {start}
        for value, scale in limits.values():
            first, last, min_px, max_px = bounds(value, scale)
            cuts |= {first, last}
            for px in (min_px, max_px):
                if px and value > 0:
                    cuts.add(math.log2(px / (value * px_per_unit0)))
        cuts = sorted(z for z in cuts if math.isfinite(z) and z >= start - 1e-9
                      and (end is None or z < end - 1e-9))
        pieces = []
        for index, zoom in enumerate(cuts):
            stop = cuts[index + 1] if index + 1 < len(cuts) else end
            middle = (zoom + stop) / 2.0 if stop is not None else zoom + 0.5
            state = tuple(regime(value, scale, middle) for value, scale in limits.values())
            if pieces and pieces[-1][2] == state:
                pieces[-1][1] = stop
            else:
                pieces.append([zoom, stop, state])
        parts = []
        for index, (zoom, stop, state) in enumerate(pieces):
            rule = flat_rule.derive()
            first = min(max(low, int(math.floor(zoom + 1e-9))), high)
            last = high if stop is None else max(first, min(high, int(math.ceil(stop - 1e-9)) - 1))
            rule.set_attr("o", first)
            rule.set_attr("i", last)
            rule.visibility = visible.intersect(ZoomInterval(zoom, stop))
            if len(pieces) > 1:
                rule.set_attr("u", index)
            clone = rule.rule.symbol().symbolLayer(0)
            for name, kind in zip(limits, state):
                setter = f"set{name[:1].upper()}{name[1:]}"
                getattr(clone, f"{setter}MapUnitScale")(QgsMapUnitScale())
                if kind[0] == "mm":
                    getattr(clone, setter)(kind[1])
                    getattr(clone, f"{setter}Unit")(Qgis.RenderUnit.Millimeters)
            result = self.materialize(rule, clone)
            parts.extend(result if result is not None else [rule])
        return parts

    @staticmethod
    def _visibility_hull(first: Optional[ZoomInterval], second: Optional[ZoomInterval]):
        """The interval covering two touching intervals."""
        if first is None or second is None:
            return None
        high = None if first.max_zoom is None or second.max_zoom is None \
            else max(first.max_zoom, second.max_zoom)
        return ZoomInterval(min(first.min_zoom, second.min_zoom), high)

    @property
    def overzoom_to(self) -> int:
        """The last zoom the map is viewed at (see ``overzoom_top``)."""
        return self.overzoom_top

    def _per_zoom(self, flat_rule: FlattenedRule, overzoom: bool = False) -> List[FlattenedRule]:
        """One rule per zoom of ``flat_rule``; the last keeps its overzoom.

        ``overzoom``: also one per zoom beyond the archive's last zoom, up to
        ``overzoom_top`` (then open), drawn from that zoom's tiles: attribute
        "v" (a dataset of its own) and ``scale_zoom`` are the zoom they are
        shown at (see rule_zoom), for scale-dependent values and screen
        sizes held in map units."""
        low, high = self._zoom_range(flat_rule)
        if low > high:
            return [flat_rule]
        over = self._overzoom_zooms(flat_rule) if overzoom else []
        if low == high and (flat_rule.get_attr("o"), flat_rule.get_attr("i")) == (low, high):
            rules = [flat_rule]
        else:
            rules = []
            for zoom in range(low, high + 1):
                rule = flat_rule.derive()
                rule.set_attr("o", zoom)
                rule.set_attr("i", zoom)
                if flat_rule.visibility is not None:
                    rule.visibility = flat_rule.visibility.intersect(
                        ZoomInterval(float(zoom), float(zoom + 1) if zoom < high else None))
                rules.append(rule)
        if not over:
            return rules
        visible = rules[-1].visibility or ZoomInterval(float(high), None)
        beyond = []
        for zoom in over:
            interval = visible.intersect(ZoomInterval(
                float(zoom), None if zoom == over[-1] else float(zoom + 1)))
            if interval.is_empty:
                continue
            rule = flat_rule.derive()
            rule.set_attr("o", high)
            rule.set_attr("i", high)
            rule.set_attr("v", zoom)
            rule.visibility = interval
            rule.scale_zoom = float(zoom)
            beyond.append(rule)
        if not beyond:
            return rules
        if rules[-1] is flat_rule:
            rules[-1] = flat_rule.derive()
        rules[-1].visibility = visible.intersect(ZoomInterval(visible.min_zoom, float(high + 1)))
        if rules[-1].visibility.is_empty:
            rules.pop()
        rules.extend(beyond)
        self.distinguish(rules)
        return rules

    def _overzoom_zooms(self, flat_rule: FlattenedRule) -> List[int]:
        """Whole zooms past the export's last tile zoom (up to overzoom_to)
        where ``flat_rule`` is visible: the browser overzooms the last tiles
        there."""
        low, high = self._zoom_range(flat_rule)
        visible = flat_rule.visibility
        if low > high or high < self.max_zoom or visible is None:
            return []  # no exact visibility: drawn up to its last tile zoom
        return [zoom for zoom in range(high + 1, self.overzoom_to + 1)
                if not visible.intersect(ZoomInterval(float(zoom), float(zoom + 1))).is_empty]

    @staticmethod
    def rule_zoom(rule: FlattenedRule) -> int:
        """The zoom a per-zoom rule is drawn at: its tile zoom, or ("v") the
        overzoom it was made for (its data are in the last tiles)."""
        view = rule.get_attr("v")
        if view is not None:
            return view
        if rule.scale_zoom is not None:  # a piece beyond the last tiles (scale split)
            return int(math.floor(rule.scale_zoom + 1e-9))
        return rule.get_attr("o")

    @staticmethod
    def _setter(layer, key):
        return layer.setInterval if key == QgsSymbolLayer.Property.PropertyInterval \
            else layer.setOffsetAlongLine

    @staticmethod
    def _fold_constant_ddp(layer, key, setter, scale=None) -> None:
        """Replace an active data-defined property whose expression reads no
        field or variable (``@map_scale`` taken as ``scale`` for a one-zoom
        rule) by its value."""
        from qgis.core import QgsExpression, QgsExpressionContext, QgsExpressionContextUtils  # pylint: disable=import-outside-toplevel
        from .fidelity.qgis_expr import with_map_scale  # pylint: disable=import-outside-toplevel
        props = layer.dataDefinedProperties()
        prop = props.property(key)
        if prop is None or not prop.isActive():
            return
        text = prop.asExpression()
        original = QgsExpression(text)
        allowed = set(SCALE_VARIABLES) if scale is not None else set()
        if original.hasParserError() or original.referencedColumns() - {""} or \
                set(original.referencedVariables()) - allowed:
            return
        if scale is not None and uses_map_scale(text):
            text = with_map_scale(text, scale)
        expression = QgsExpression(text)
        value = expression.evaluate(QgsExpressionContext([QgsExpressionContextUtils.globalScope()]))
        if expression.hasEvalError():
            return
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        setter(number)
        prop.setActive(False)
        props.setProperty(key, prop)
        layer.setDataDefinedProperties(props)

    def _grid_elements(self, flat_rule: FlattenedRule, layer) -> float:
        """Estimated features of a point-pattern grid (line-work markers
        count per segment)."""
        cells = self._layer_totals(flat_rule.layer)[0] / max(
            layer.distanceX() * layer.distanceY(), 1e-12)
        marker = layer.subSymbol()
        shape = self._stroke_marker(marker) if marker is not None else None
        return cells * (len(mat.STROKE_MARKER_PATHS.get(shape, ((),))) if shape else 1)

    @classmethod
    def _tiling_pattern(cls, layer) -> bool:
        """A point pattern whose image markers fill their cells and are
        clipped to the polygon ("shape" clip mode) is a texture: exported as
        points, the edge markers would be drawn whole (sprites cannot be
        clipped), so it stays a clipped browser pattern. Data-defined
        markers stay points (a texture has one appearance)."""
        try:
            if int(layer.clipMode()) != int(Qgis.MarkerClipMode.Shape):
                return False
        except AttributeError:
            return False
        marker = layer.subSymbol()
        if marker is None or cls._stroke_marker(marker) is not None or \
                normalize_unit(marker.sizeUnit()) != "map":
            return False
        for index in range(marker.symbolLayerCount()):
            props = marker.symbolLayer(index).dataDefinedProperties()
            if any(props.isActive(key) for key in props.propertyKeys()):
                return False
        return marker.size() >= 0.9 * min(layer.distanceX(), layer.distanceY())

    def _svg_fill(self, flat_rule: FlattenedRule, layer) -> List[FlattenedRule]:
        """``QgsSVGFillSymbolLayer::renderPolygon``: the SVG texture (only when
        the SVG data parses), then its stroke sub-symbol along every ring."""
        parts: List[FlattenedRule] = []
        if svg_fill_draws(layer):
            if normalize_unit(layer.patternWidthUnit()) == "map":
                width = max(layer.patternWidth(), 1e-9)
                split = self._dense_split(flat_rule, layer.patternWidth(),
                                          lambda rule: self._svg_grid(rule, layer),
                                          elements=self._layer_totals(flat_rule.layer)[0] /
                                          (width * width), what="SVG fill")
                parts.extend(split if split is not None else [flat_rule.derive()])
            else:
                parts.append(flat_rule.derive())
        stroke = layer.subSymbol()
        if stroke is not None and stroke.symbolLayerCount():
            outline = self._with_symbol(flat_rule, stroke.clone(), 1, 9)
            outline.order = flat_rule.order + (1,) if flat_rule.order else ()
            parts.append(outline)
        return parts

    def _polygon_outline_offset(self, flat_rule: FlattenedRule, layer):
        """Offset polygon outlines like QGIS (``QgsSymbolLayerUtils::offsetLine``):
        every ring is buffered as its own polygon, so a positive offset moves
        exterior and holes towards the feature's interior whatever the ring
        orientation. Map-unit offsets are materialized exactly, and so are
        static screen-unit offsets of simple lines, per zoom band (see
        _screen_polygon_offset); other screen-unit offsets keep a native
        offset on right-hand-rule rings (exterior clockwise, holes
        counter-clockwise), where MapLibre's right-hand side is the feature's
        interior. Placed markers (vertex, centre...) are handled by the
        marker-line materialization."""
        if layer.layerType() != "SimpleLine":
            placements = _flag_names(layer.placements()) if hasattr(layer, "placements") else set()
            if placements & mat.POINT_PLACEMENTS:
                return None
            if layer.layerType() == "MarkerLine" and (self._exact_interval(layer)
                                                      or self._zoom_interval(layer)):
                return None  # exact interval markers (see _marker_line)
        crs = self.project_crs or flat_rule.layer.crs().authid()
        rule = flat_rule.derive()
        clone = rule.rule.symbol().symbolLayer(0)
        params = [("ring_filter", _ring_filter(layer)), ("crs", crs)]
        if abs(layer.offset()) <= 1e-9:
            clone.setOffset(0.0)
        elif normalize_unit(layer.offsetUnit()) == "map":
            params.append(("offset", float(layer.offset())))
            clone.setOffset(0.0)
        else:
            if layer.layerType() == "SimpleLine":
                bands = self._screen_polygon_offset(flat_rule, layer, params)
                if bands is not None:
                    return bands
            params.append(("rhr", True))
        rule.recipe = mat.Recipe("polygon_offset", params=tuple(params))
        rule.set_attr("m", 1)
        if layer.layerType() == "HashLine":
            converted = self._hash_as_marker_line(clone, rule)
            replace_symbol_layer(rule.rule.symbol(), converted)
        return [rule]

    # Band attribute ("b") of an overzoom rule next to sub-zoom bands (0-7).
    OVERZOOM_BAND = 9

    def _screen_polygon_offset(self, flat_rule: FlattenedRule, layer,
                               params) -> Optional[List[FlattenedRule]]:
        """A screen-unit polygon outline offset as QGIS draws it: every ring
        buffered as its own polygon in painter pixels (offsetLine: mitred,
        limit 2) and stroked once. MapLibre's line-offset overlaps segments
        and joins at every vertex (a translucent band blends twice there) and
        does not bridge inlets narrower than twice the offset. Per eighth of
        a zoom of the export's zooms (offset within +-4.5 %), converted at the
        band's middle; native (right-hand-rule rings) when overzooming, for
        data-defined offsets and over the output budget."""
        offset_mm = _to_mm(layer.offset(), layer.offsetUnit())
        props = layer.dataDefinedProperties()
        if offset_mm is None or (props is not None and props.isActive(QgsSymbolLayer.Property.PropertyOffset)):
            return None
        low, high = self._zoom_range(flat_rule)
        if low > high:
            return None
        zoom_rules = self._per_zoom(flat_rule)
        costs = {rule.get_attr("o"): self._polygon_offset_cost(flat_rule, layer, offset_mm,
                                                                rule.get_attr("o"))
                 for rule in zoom_rules}
        steps = self._zoom_steps(costs, (8, 4, 2, 1))
        # No outline at all where every ring's offset is empty (an inward
        # offset wider than the features, as at low zooms): QGIS draws
        # nothing there, the browser's line offset would.
        drawn = [rule for rule in zoom_rules if costs[rule.get_attr("o")]]
        exact = [rule for rule in drawn if steps[rule.get_attr("o")]]
        native_zooms = [rule.get_attr("o") for rule in drawn if not steps[rule.get_attr("o")]]
        rules = []
        visible = flat_rule.visibility or ZoomInterval(float(low), None)
        overzoom = visible.intersect(ZoomInterval(float(high + 1), None))
        if native_zooms:  # the budget ends below the last zooms: native there
            cut = max(native_zooms) + 1
            if exact:
                self._report("Q2VT_PATTERN_BUDGET",
                             f"Polygon outline offsets below zoom {cut} would exceed the output "
                             "budget; drawn there with the browser's line offset.", flat_rule)
            else:
                self._report("Q2VT_PATTERN_BUDGET",
                             "Polygon outline offsets would exceed the output budget; drawn "
                             "with the browser's line offset.", flat_rule)
            native = flat_rule.derive()
            native.set_attr("o", min(native_zooms))
            native.set_attr("i", max(native_zooms))
            if cut > high:
                # Native up to the last zoom: the same rule overzooms (a
                # separate overzoom rule would share its tiles and name).
                native.visibility = visible.intersect(ZoomInterval(float(min(native_zooms)), None))
                overzoom = None
            else:
                native.visibility = visible.intersect(
                    ZoomInterval(float(min(native_zooms)), float(cut)))
            native.recipe = mat.Recipe("polygon_offset", params=tuple(params) + (("rhr", True),))
            native.set_attr("m", 1)
            rules.append(native)
        for rule in exact:
            start = float(rule.get_attr("o"))
            for band, zoom in self._sub_zoom_bands(rule, steps[rule.get_attr("o")], False):
                derived = band.derive()
                derived.visibility = (band.visibility or ZoomInterval(start, start + 1.0)).intersect(
                    ZoomInterval(start, start + 1.0))
                derived.rule.symbol().symbolLayer(0).setOffset(0.0)
                derived.recipe = mat.Recipe("polygon_offset", params=tuple(params) + (
                    ("offset", offset_mm * self._map_units_per_mm(flat_rule, zoom)),))
                derived.set_attr("m", 1)
                rules.append(derived)
        if overzoom is not None and not overzoom.is_empty:
            # Past the last tiles: native, right-hand-rule rings.
            native = flat_rule.derive()
            native.set_attr("o", high)
            native.visibility = overzoom
            native.recipe = mat.Recipe("polygon_offset", params=tuple(params) + (("rhr", True),))
            native.set_attr("m", 1)
            # Its own dataset: the last zoom's exact rule has the same zooms
            # and, in one sub-zoom step, no band ("b" 0-7) to tell them apart.
            native.set_attr("b", self.OVERZOOM_BAND)
            rules.append(native)
        return rules

    def _polygon_offset_cost(self, flat_rule: FlattenedRule, layer, offset_mm: float,
                             zoom: int) -> int:
        """Vertices of the offset rings one sub-zoom step of ``zoom`` outputs:
        a ring buffered inwards by more than its inradius is empty (an
        inward offset at the zoom's smallest, i.e. at ``zoom + 1``)."""
        if flat_rule.pre_generator or flat_rule.merge:
            return self._layer_vertices(flat_rule.layer)  # not the source polygons' rings
        inset = offset_mm * self._map_units_per_mm(flat_rule, zoom + 1.0)
        ring_filter = _ring_filter(layer)  # 1: exteriors only, 2: holes only
        total = 0
        for exterior, (radii, vertices_from) in self._layer_rings(flat_rule.layer).items():
            if ring_filter == (2 if exterior else 1):
                continue
            # A positive offset shrinks exteriors and grows holes: the rings
            # with a larger radius are drawn.
            total += vertices_from[bisect.bisect_right(radii, inset if exterior else -inset)]
        return total

    def _layer_rings(self, layer) -> Dict[bool, Tuple[List[float], List[int]]]:
        """Exterior (True) and interior rings of a source layer (cached):
        their radii ascending, each at least the inradius of the ring as its
        own polygon (project map units: no inset deeper than it is drawn),
        and the vertices of the rings from each index on."""
        from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,  # pylint: disable=import-outside-toplevel
                               QgsFeatureRequest, QgsGeometry, QgsPolygon)
        cache = self.__dict__.setdefault("_rings", {})
        if layer.id() in cache:
            return cache[layer.id()]
        transform = None
        if self.project_crs and layer.crs().authid() != self.project_crs:
            transform = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem(self.project_crs),
                                               QgsProject.instance())
        rings = []
        for feature in layer.getFeatures(QgsFeatureRequest().setNoAttributes()):
            geometry = feature.geometry()
            if geometry.isEmpty():
                continue
            if transform is not None:
                geometry.transform(transform)
            for part in geometry.constParts():
                if not hasattr(part, "exteriorRing"):
                    continue
                part = part.segmentize() if part.hasCurvedSegments() else part
                for index in range(-1, part.numInteriorRings()):
                    ring = part.exteriorRing() if index < 0 else part.interiorRing(index)
                    if ring is None:
                        continue
                    bounds = ring.boundingBox()
                    # A disk inside the ring fits inside its bounding box.
                    radius = min(bounds.width(), bounds.height()) / 2.0
                    polygon = QgsPolygon()
                    polygon.setExteriorRing(ring.clone())
                    precision = radius / 50.0
                    if precision > 0:
                        pole, distance = QgsGeometry(polygon).poleOfInaccessibility(precision)
                        if not pole.isNull() and math.isfinite(distance):
                            # Within ``precision`` of the largest inscribed circle.
                            radius = min(radius, distance + precision)
                    rings.append((index < 0, radius, ring.numPoints()))
        result = {}
        for exterior in (True, False):
            rows = sorted((radius, count) for kind, radius, count in rings if kind == exterior)
            vertices_from = [0] * (len(rows) + 1)
            for i in range(len(rows) - 1, -1, -1):
                vertices_from[i] = vertices_from[i + 1] + rows[i][1]
            result[exterior] = ([radius for radius, _ in rows], vertices_from)
        cache[layer.id()] = result
        return result


    # Qt's dash patterns in multiples of the pen width.
    _QT_DASHES = {Qt.PenStyle.DashLine: (4, 2), Qt.PenStyle.DotLine: (1, 2),
                  Qt.PenStyle.DashDotLine: (4, 2, 1, 2), Qt.PenStyle.DashDotDotLine: (4, 2, 1, 2, 1, 2)}

    def _inside_polygon(self, flat_rule: FlattenedRule, layer) -> Optional[List[FlattenedRule]]:
        """"Draw line only inside polygon": QGIS clips the outline stroke to
        the polygon, holes left out (renderPolygonStroke; not for interior
        rings only), so of a stroke of width w at offset o (inwards) only
        what lies inside the polygon shows. A solid stroke is those polygons
        (fidelity inside_band_expression: each ring's offset ring stroked
        with the pen's join, then clipped), per eighth of a zoom for screen
        units (the offset and the width converted each in its own unit);
        the browser draws the band as a line of its width at its middle
        beyond the last tiles, for dashes and over the output budget. None
        when the option does not apply."""
        if flat_rule.get_attr("g") != 2 or not layer.drawInsidePolygon() or _ring_filter(layer) == 2:
            return None
        P = QgsSymbolLayer.Property
        props = layer.dataDefinedProperties()
        width, unit = float(layer.width()), layer.widthUnit()
        offset = float(layer.offset()) if abs(layer.offset()) > 1e-9 else 0.0
        offset_unit = layer.offsetUnit() if offset else unit
        if normalize_unit(offset_unit) != normalize_unit(unit):
            offset_mm, unit_mm = _to_mm(offset, offset_unit), _to_mm(1.0, unit)
            if offset_mm is not None and unit_mm:
                offset, offset_unit = offset_mm / unit_mm, unit
        mixed = normalize_unit(offset_unit) != normalize_unit(unit)

        def in_map(value, value_unit, zoom):
            if normalize_unit(value_unit) in ("map", "m"):
                return value
            mm = _to_mm(value, value_unit)
            return None if mm is None else mm * self._map_units_per_mm(flat_rule, zoom)

        if mixed and (in_map(1.0, unit, 0.0) is None or in_map(1.0, offset_unit, 0.0) is None):
            self._report("Q2VT_SYMBOL_APPROXIMATE",
                         "Draw line only inside polygon: the offset's unit cannot be converted; "
                         "the whole stroke is drawn.", flat_rule)
            return None
        if width <= 0 or layer.penStyle() == Qt.PenStyle.NoPen:
            return None  # a hairline (or nothing): the clip is not visible
        if any(props.isActive(k) for k in (P.PropertyStrokeWidth, P.PropertyOffset)):
            self._report("Q2VT_SYMBOL_APPROXIMATE",
                         "Draw line only inside polygon: the band uses the static width and "
                         "offset, not the data-defined ones.", flat_rule)
        if not mixed and offset + width / 2.0 <= 0:
            return []  # the whole stroke lies outside the polygon
        solid = layer.penStyle() == Qt.PenStyle.SolidLine and not layer.useCustomDashPattern()
        effect = layer.paintEffect()
        native_only = not solid or (effect is not None and effect.enabled())
        crs = self.project_crs or flat_rule.layer.crs().authid()
        if mixed:
            # The band as a line needs one unit: the whole stroke.
            line = layer.clone()
            line.setDrawInsidePolygon(False)
        else:
            # The band as a line, for the browser: its width at its middle,
            # mitred (the clip leaves the polygon's corners sharp; MapLibre's
            # offset bevel and round joins overlap, darker where translucent);
            # Qt's dash patterns keep their lengths in units of the full width.
            low_edge = max(offset - width / 2.0, 0.0)
            line = layer.clone()
            line.setDrawInsidePolygon(False)
            line.setWidth(offset + width / 2.0 - low_edge)
            line.setOffset((offset + width / 2.0 + low_edge) / 2.0)
            line.setOffsetUnit(unit)
            line.setOffsetMapUnitScale(layer.widthMapUnitScale())
            line.setPenJoinStyle(Qt.PenJoinStyle.MiterJoin)
            if not layer.useCustomDashPattern() and layer.penStyle() in self._QT_DASHES:
                line.setUseCustomDashPattern(True)
                line.setCustomDashVector([m * width for m in self._QT_DASHES[layer.penStyle()]])
                line.setCustomDashPatternUnit(unit)
                line.setCustomDashPatternMapUnitScale(layer.widthMapUnitScale())

        def report_line(where: str):
            if mixed or offset > 0:
                self._report("Q2VT_SYMBOL_APPROXIMATE",
                             f"Draw line only inside polygon: {where}the stroke is drawn as "
                             + ("an unclipped line (offset and width in different units)."
                                if mixed else "a line inside the rings; its corners and parts "
                                "narrower than twice the offset differ from QGIS."), flat_rule)

        def as_line(rule: FlattenedRule) -> List[FlattenedRule]:
            derived = rule.derive()
            replace_symbol_layer(derived.rule.symbol(), line.clone())
            result = self.materialize(derived, derived.rule.symbol().symbolLayer(0))
            return result if result is not None else [derived]

        if native_only:
            report_line("dashed or with effects, ")
            return as_line(flat_rule)
        from qgis.core import QgsFillSymbol, QgsSimpleFillSymbolLayer  # pylint: disable=import-outside-toplevel
        fill = QgsSimpleFillSymbolLayer(layer.color(), Qt.BrushStyle.SolidPattern,
                                        layer.color(), Qt.PenStyle.NoPen)
        if props.isActive(P.PropertyStrokeColor):
            fill.setDataDefinedProperty(P.PropertyFillColor, QgsProperty(props.property(P.PropertyStrokeColor)))
        symbol = QgsFillSymbol([fill])
        symbol.setOpacity(flat_rule.rule.symbol().opacity())
        join = {Qt.PenJoinStyle.BevelJoin: "bevel", Qt.PenJoinStyle.RoundJoin: "round"}.get(
            layer.penJoinStyle(), "miter")

        def band(rule: FlattenedRule, zoom: float) -> Optional[FlattenedRule]:
            w, o = in_map(width, unit, zoom), in_map(offset, offset_unit, zoom)
            if o + w / 2.0 <= 0:
                return None  # the whole stroke lies outside the polygon
            return self._with_symbol(rule, symbol.clone(), 2, 1, mat.inside_band(
                o - w / 2.0, o + w / 2.0, _ring_filter(layer), join, crs))

        if all(normalize_unit(u) in ("map", "m") for u in (unit, offset_unit)):
            derived = band(flat_rule, 0.0)
            return [derived] if derived is not None else []
        low, high = self._zoom_range(flat_rule)
        if low > high:
            report_line("")
            return as_line(flat_rule)
        zoom_rules = self._per_zoom(flat_rule)
        vertices = self._layer_vertices(flat_rule.layer)
        steps = next((n for n in (8, 4, 2, 1)
                      if vertices * len(zoom_rules) * n <= self.MAX_PATTERN_ELEMENTS), 0)
        if not steps:
            self._report("Q2VT_PATTERN_BUDGET",
                         "Lines drawn only inside polygons would exceed the output budget; drawn "
                         "as a line of the visible band's width.", flat_rule)
            report_line("over the output budget, ")
            return as_line(flat_rule)
        # Zooms where the whole layer is under a pixel get no band dataset:
        # their tiles would be empty.
        extent = self._layer_extent(flat_rule.layer)
        rules = []
        for rule in zoom_rules:
            start = float(rule.get_attr("o"))
            if extent < self._map_units_per_mm(flat_rule, start + 1.0) * 25.4 / 96.0:
                continue
            for sub, zoom in self._sub_zoom_bands(rule, steps, False):
                derived = band(sub, zoom)
                if derived is None:
                    continue
                derived.visibility = (sub.visibility or ZoomInterval(start, start + 1.0)).intersect(
                    ZoomInterval(start, start + 1.0))
                rules.append(derived)
        visible = flat_rule.visibility or ZoomInterval(float(low), None)
        overzoom = visible.intersect(ZoomInterval(float(high + 1), None))
        if not overzoom.is_empty:  # past the last tiles: the band as a line
            report_line(f"beyond zoom {high + 1}, ")
            native = flat_rule.derive()
            native.set_attr("o", high)
            native.visibility = overzoom
            replace_symbol_layer(native.rule.symbol(), line.clone())
            native.recipe = mat.Recipe("polygon_offset", params=(
                ("ring_filter", _ring_filter(layer)), ("crs", crs), ("rhr", True)))
            native.set_attr("m", 1)
            rules.append(native)
        return rules

    def _layer_extent(self, layer) -> float:
        """The longer side of a source layer's extent in project map units."""
        from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,  # pylint: disable=import-outside-toplevel
                               QgsCsException)
        extent = layer.extent()
        if self.project_crs and layer.crs().authid() != self.project_crs:
            try:
                extent = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem(self.project_crs),
                                                QgsProject.instance()).transformBoundingBox(extent)
            except QgsCsException:
                return math.inf
        return max(extent.width(), extent.height())

    def _layer_vertices(self, layer) -> int:
        """Vertices of a source layer (cached): the size of per-zoom copies."""
        from qgis.core import QgsFeatureRequest  # pylint: disable=import-outside-toplevel
        cache = self.__dict__.setdefault("_vertices", {})
        if layer.id() not in cache:
            cache[layer.id()] = sum(
                feature.geometry().constGet().nCoordinates()
                for feature in layer.getFeatures(QgsFeatureRequest().setNoAttributes())
                if feature.hasGeometry())
        return cache[layer.id()]

    def _offset_line(self, flat_rule: FlattenedRule, layer) -> List[FlattenedRule]:
        """A map-unit line offset as the offset line itself
        (``QgsSymbolLayerUtils::offsetLine``: mitred offset curve): MapLibre's
        ``line-offset`` crosses itself and bunches up at sharp corners
        (Gyorsforgalmi út, +-15 m of a 10 m wide line)."""
        crs = self.project_crs or flat_rule.layer.crs().authid()
        rule = flat_rule.derive()
        rule.rule.symbol().symbolLayer(0).setOffset(0.0)
        rule.recipe = mat.Recipe("line_offset", params=(
            ("offset", float(layer.offset())), ("crs", crs)))
        rule.set_attr("m", 1)
        return [rule]

    # Vertices whose offset loops are tolerated before an offset is drawn
    # natively (MapLibre line-offset) at a zoom.
    OFFSET_LOOP_QUANTILE = 0.995

    def _offset_loop_zooms(self, layer, right: bool) -> List[float]:
        """Per vertex of a line layer, the zoom below which an offset of one
        CSS pixel to the ``right`` (else left) is longer than the vertex's
        corner allows (the offset segments meet beyond the shorter adjacent
        segment: MapLibre's line-offset loops there, a GEOS offset curve
        does not): ``log2(px0 / d_crit)`` with ``d_crit = min(l1, l2) /
        tan(turn / 2)``; add log2(offset in px) for a given offset. Project
        CRS, cached per layer and side."""
        from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,  # pylint: disable=import-outside-toplevel
                               QgsFeatureRequest)
        cache = self.__dict__.setdefault("_loop_zooms", {})
        key = (layer.id(), right)
        if key in cache:
            return cache[key]
        transform = None
        if self.project_crs and layer.crs().authid() != self.project_crs:
            transform = QgsCoordinateTransform(layer.crs(), QgsCoordinateReferenceSystem(self.project_crs),
                                               QgsProject.instance())
        px0 = self._map_units_per_mm(None, 0.0, layer) * 25.4 / 96.0
        zooms = []
        request = QgsFeatureRequest().setNoAttributes()
        for feature in layer.getFeatures(request):
            geometry = feature.geometry()
            if geometry.isEmpty():
                continue
            if transform is not None:
                geometry.transform(transform)
            for part in geometry.constParts():
                line = part.curveToLine() if part.hasCurvedSegments() else part
                points = [(line.xAt(i), line.yAt(i)) for i in range(line.numPoints())]
                points = [p for i, p in enumerate(points) if i == 0 or p != points[i - 1]]
                for a, b, c in zip(points, points[1:], points[2:]):
                    d1 = (b[0] - a[0], b[1] - a[1])
                    d2 = (c[0] - b[0], c[1] - b[1])
                    cross = d1[0] * d2[1] - d1[1] * d2[0]
                    if (cross < 0) != right or cross == 0:
                        continue  # turning away from the offset side (or straight)
                    l1, l2 = math.hypot(*d1), math.hypot(*d2)
                    turn = math.atan2(abs(cross), d1[0] * d2[0] + d1[1] * d2[1])
                    tangent = math.tan(turn / 2.0)
                    if tangent > 1e9:
                        zooms.append(math.inf)
                        continue
                    critical = min(l1, l2) / tangent
                    zooms.append(math.inf if critical <= 0 else math.log2(px0 / critical))
        zooms.sort()
        cache[key] = zooms
        return zooms

    def _screen_offset_line(self, flat_rule: FlattenedRule, layer) -> Optional[List[FlattenedRule]]:
        """A screen-unit line offset as QGIS draws it, a mitred offset curve
        of the line in painter pixels (QgsSymbolLayerUtils::offsetLine), for
        the zooms where MapLibre's line-offset would loop at the layer's
        corners: per eighth of a zoom (the offset within +-4.5 %), the offset
        converted at the band's middle; native above them."""
        offset_px = _to_mm(layer.offset(), layer.offsetUnit()) * 96.0 / 25.4
        low, high = self._zoom_range(flat_rule)
        if low > high:
            return None
        zooms = self._offset_loop_zooms(flat_rule.layer, offset_px > 0)
        if not zooms:
            return None
        quantile = zooms[min(len(zooms) - 1, int(len(zooms) * self.OFFSET_LOOP_QUANTILE))]
        loop_zoom = quantile + math.log2(abs(offset_px))
        cut = min(high + 1, int(math.ceil(loop_zoom)))  # native from this zoom
        if cut <= low:
            return None
        vertices = len(zooms) * 2 + self._layer_totals(flat_rule.layer)[2] * 2
        zoom_rules = [r for r in self._per_zoom(flat_rule) if r.get_attr("o") < cut]
        steps = self._zoom_steps({rule.get_attr("o"): vertices for rule in zoom_rules},
                                 (8, 4, 2, 1))
        exact = [rule for rule in zoom_rules if steps[rule.get_attr("o")]]
        if not exact:
            self._report("Q2VT_PATTERN_BUDGET",
                         "Line offset curves would exceed the output budget; drawn with the "
                         "browser's line offset.", flat_rule)
            return None
        crs = self.project_crs or flat_rule.layer.crs().authid()
        offset_mm = _to_mm(layer.offset(), layer.offsetUnit())
        rules = []
        first = exact[0].get_attr("o")
        if first > low:  # the budget ends below the last zooms: native there
            self._report("Q2VT_PATTERN_BUDGET",
                         f"Line offset curves below zoom {first} would exceed the output "
                         "budget; drawn there with the browser's line offset.", flat_rule)
            below = flat_rule.derive()
            below.set_attr("i", first - 1)
            below.visibility = (flat_rule.visibility or ZoomInterval(float(low), None)).intersect(
                ZoomInterval(0.0, float(first)))
            rules.append(below)
        for rule in exact:
            start = float(rule.get_attr("o"))
            for band, zoom in self._sub_zoom_bands(rule, steps[rule.get_attr("o")], False):
                derived = band.derive()
                derived.visibility = (band.visibility or ZoomInterval(start, start + 1.0)).intersect(
                    ZoomInterval(start, start + 1.0))
                derived.rule.symbol().symbolLayer(0).setOffset(0.0)
                derived.recipe = mat.Recipe("line_offset", params=(
                    ("offset", offset_mm * self._map_units_per_mm(flat_rule, zoom)), ("crs", crs)))
                derived.set_attr("m", 1)
                rules.append(derived)
        visible = flat_rule.visibility or ZoomInterval(float(low), None)
        native_part = visible.intersect(ZoomInterval(float(cut), None))
        if not native_part.is_empty:
            native = flat_rule.derive()
            native.set_attr("o", min(cut, high))
            native.visibility = native_part
            rules.append(native)
        return rules

    # QgsFontMarkerSymbolLayer: pixel sizes above this are drawn scaled up.
    GLYPH_PIXEL_SIZE = 500

    def glyph_outline(self, layer) -> Optional[str]:
        """WKT of a font marker's character outlines in map units around its
        point (y up), with its offset and anchor: the path QGIS draws
        (``QPainterPath::addText`` centred on the advance width, baseline
        half the ascent below the point)."""
        from qgis.core import QgsApplication, QgsFontUtils, QgsGeometry, QgsPointXY  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtGui import QFontMetricsF, QPainterPath  # pylint: disable=import-outside-toplevel
        font = QgsFontUtils.createFont(
            QgsApplication.fontManager().processFontFamilyName(layer.fontFamily()))
        if layer.fontStyle():
            font.setStyleName(QgsFontUtils.translateNamedStyle(layer.fontStyle()))
        font.setPixelSize(self.GLYPH_PIXEL_SIZE)
        metrics = QFontMetricsF(font)
        text = layer.character()
        path = QPainterPath()
        path.addText(-metrics.horizontalAdvance(text) / 2.0, metrics.ascent() / 2.0, font, text)
        size = layer.size()
        scale = size / self.GLYPH_PIXEL_SIZE
        # QgsMarkerSymbolLayer::markerOffset (painter axes, y down).
        off_x, off_y = layer.offset().x(), layer.offset().y()
        h_anchor = _enum_value(layer.horizontalAnchorPoint())
        v_anchor = _enum_value(layer.verticalAnchorPoint())
        off_x += {0: size / 2.0, 2: -size / 2.0}.get(h_anchor, 0.0)
        off_y += {0: size / 2.0, 2: -size / 2.0}.get(v_anchor, 0.0)
        shape = None
        for polygon in path.toSubpathPolygons():
            ring = [QgsPointXY(p.x() * scale + off_x, -(p.y() * scale + off_y)) for p in polygon]
            if len(ring) < 4:
                continue
            part = QgsGeometry.fromPolygonXY([ring]).makeValid()
            # Counters (holes) are drawn as the XOR of overlapping outlines.
            shape = part if shape is None else shape.symDifference(part)
        if shape is None or shape.isEmpty():
            return None
        return shape.simplify(size * 1e-4).asWkt(6)

    def _glyph_marker(self, flat_rule: FlattenedRule, layer) -> Optional[List[FlattenedRule]]:
        """A font marker sized in map units on a point layer as its glyph
        outlines: browser text is a 24 px distance field that turns blobby
        when scaled up several times, while the outlines are exact at every
        zoom."""
        from qgis.core import QgsFillSymbol, QgsSimpleFillSymbolLayer  # pylint: disable=import-outside-toplevel
        if flat_rule.get_attr("g") != 0 or normalize_unit(layer.sizeUnit()) != "map" or \
                not layer.character():
            return None
        props = layer.dataDefinedProperties()
        P = QgsSymbolLayer.Property
        if any(props.isActive(k) for k in (
                P.PropertyCharacter, P.PropertySize, P.PropertyFontFamily, P.PropertyFontStyle,
                P.PropertyOffset, P.PropertyHorizontalAnchor, P.PropertyVerticalAnchor)):
            return None
        if (layer.offset().x() or layer.offset().y()) and \
                normalize_unit(layer.offsetUnit()) != "map":
            return None
        if _limited(layer.sizeMapUnitScale()) or ((layer.offset().x() or layer.offset().y())
                                                  and _limited(layer.offsetMapUnitScale())):
            # Min/max size or a scale range: the glyph is not proportional
            # to the map; the sprite marker applies the limits per zoom.
            return None
        wkt = self.glyph_outline(layer)
        if wkt is None:
            return None
        angle = repr(float(layer.angle()))
        if props.isActive(P.PropertyAngle):
            angle = f"coalesce(({props.property(P.PropertyAngle).asExpression()}), {angle})"
        fill = QgsSimpleFillSymbolLayer(layer.color())
        if layer.strokeWidth() > 0:
            fill.setStrokeColor(layer.strokeColor())
            fill.setStrokeWidth(layer.strokeWidth())
            fill.setStrokeWidthUnit(layer.strokeWidthUnit())
            fill.setStrokeWidthMapUnitScale(layer.strokeWidthMapUnitScale())
            fill.setPenJoinStyle(layer.penJoinStyle())
        else:
            fill.setStrokeStyle(Qt.PenStyle.NoPen)
        for key in (P.PropertyFillColor, P.PropertyStrokeColor, P.PropertyStrokeWidth):
            if props.isActive(key):
                fill.setDataDefinedProperty(key, QgsProperty(props.property(key)))
        symbol = QgsFillSymbol([fill])
        symbol.setOpacity(flat_rule.rule.symbol().opacity())
        recipe = mat.glyph_recipe(wkt, angle, self.project_crs or flat_rule.layer.crs().authid())
        return [self._with_symbol(flat_rule, symbol, 2, 3, recipe)]

    # Map-unit dashes become line features from the zoom where the pattern
    # period reaches this many CSS px (below that MapLibre dashes are used).
    DASH_MIN_PERIOD_PX = 6.0

    @staticmethod
    def _merge_empty_dashes(pattern):
        """Zero-length dashes merged into the gap before them: Qt draws
        nothing for them (Felszín alatti vízbázis védőidom: "6;4;...;6;4;0;20"
        leaves a 24 m gap for its text). A pattern starting with one is kept
        as is."""
        if len(pattern) < 4 or len(pattern) % 2 or not pattern[0]:
            return pattern
        merged = pattern[:2]
        for dash, gap in zip(pattern[2::2], pattern[3::2]):
            if dash == 0:
                merged[-1] += gap
            else:
                merged += [dash, gap]
        return merged

    def _dash_segments(self, flat_rule: FlattenedRule, layer):
        """Map-unit custom dashes as their dashes: Qt starts the pattern on
        every line and ring and runs it across vertices, while MapLibre
        restarts it wherever a tile clips the line, which misplaces dashes
        against markers drawn in the gaps. Solid lines with the layer's cap
        are drawn along the dashes."""
        if not layer.useCustomDashPattern() or \
                normalize_unit(layer.customDashPatternUnit()) != "map" or \
                layer.penStyle() == Qt.PenStyle.NoPen:
            return None
        props = layer.dataDefinedProperties()
        P = QgsSymbolLayer.Property
        if any(props.isActive(k) for k in (P.PropertyCustomDash, P.PropertyOffset,
                                           P.PropertyDashPatternOffset,
                                           P.PropertyTrimStart, P.PropertyTrimEnd)):
            return None
        if layer.alignDashPattern() or layer.tweakDashPatternOnCorners() or \
                layer.trimDistanceStart() or layer.trimDistanceEnd():
            return None
        pattern = [float(v) for v in layer.customDashVector()]
        dash_offset = 0.0
        if layer.dashPatternOffset():
            if normalize_unit(layer.dashPatternOffsetUnit()) != "map":
                return None
            dash_offset = float(layer.dashPatternOffset())
        # Qt draws nothing for a zero-length dash (measured, square caps too):
        # merged into the gap before it.
        pattern = self._merge_empty_dashes(pattern)
        if not pattern or min(pattern) < 0 or sum(pattern[0::2]) <= 0 or \
                any(v <= 0 for v in pattern[0::2]):
            return None
        offset = 0.0
        if abs(layer.offset()) > 1e-9:
            if normalize_unit(layer.offsetUnit()) != "map":
                return None
            offset = float(layer.offset())
        polygon = flat_rule.get_attr("g") == 2
        crs = self.project_crs or flat_rule.layer.crs().authid()
        recipe = mat.dash_recipe(pattern, dash_offset, crs, offset,
                                 _ring_filter(layer) if polygon else 0)

        def dashes(rule):
            derived = rule.derive()
            clone = derived.rule.symbol().symbolLayer(0)
            clone.setUseCustomDashPattern(False)
            clone.setPenStyle(Qt.PenStyle.SolidLine)
            clone.setOffset(0.0)
            if hasattr(clone, "setRingFilter"):
                clone.setRingFilter(QgsLineSymbolLayer.RenderRingFilter.AllRings)
            derived.recipe = recipe
            derived.set_attr("m", 2)
            return [derived]
        period = sum(pattern) * (2 if len(pattern) % 2 else 1)
        length = self._layer_totals(flat_rule.layer)[1]  # lines, or polygon perimeters
        return self._dense_split(flat_rule, period, dashes, min_px=self.DASH_MIN_PERIOD_PX,
                                 elements=length / period * max(1, len(pattern) // 2),
                                 what="Dash pattern", overzoom=True)

    # A map-unit grid becomes point features only from the zoom where its
    # spacing reaches this many CSS px; below that it is a per-zoom texture
    # (identical look, a fraction of the features).
    GRID_MIN_SPACING_PX = 8.0

    @staticmethod
    def _average_angle_length(layer, rule: FlattenedRule) -> float:
        """``averageAngleLength`` in map units. A screen length shrinks in map
        units as the map zooms in, while one point dataset serves a range of
        zooms: it is converted at the middle of the first three zooms."""
        try:
            length, unit = float(layer.averageAngleLength()), layer.averageAngleUnit()
        except AttributeError:
            return 0.0
        if length <= 0:
            return 0.0
        if normalize_unit(unit) == "map":
            return length
        mm = _to_mm(length, unit)
        if mm is None:
            return 0.0
        low = rule.get_attr("o")
        span = max(0, min(rule.get_attr("i"), low + 3) - low) + 1
        if rule.get_attr("v") is not None:  # an overzoom's rule: that zoom
            low, span = SymbolMaterializer.rule_zoom(rule), 1
        return mm * ZoomLevels.map_units_per_mm(low + span / 2.0)

    # Interval markers along a line are few per tile even when close together.
    INTERVAL_MIN_SPACING_PX = 2.0

    # Budget of generated pattern features per rule (markers, dashes, line
    # work): past it the pattern is a texture at every zoom (reported), so
    # an export never stalls or drops a pattern silently on large layers.
    MAX_PATTERN_ELEMENTS = 2_000_000

    def _layer_totals(self, layer):
        """``(area, length, features, diagonals)`` of a source layer in project
        map units (cached; ``diagonals``: the sum of the features' bounding
        box diagonals): the basis of pattern size estimates."""
        from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,  # pylint: disable=import-outside-toplevel
                               QgsFeatureRequest)
        cache = self.__dict__.setdefault("_totals", {})
        key = layer.id()
        if key in cache:
            return cache[key]
        transform = None
        if self.project_crs and layer.crs().authid() != self.project_crs:
            transform = QgsCoordinateTransform(layer.crs(),
                                               QgsCoordinateReferenceSystem(self.project_crs),
                                               QgsProject.instance())
        area = length = diagonals = 0.0
        count = 0
        radii = []
        request = QgsFeatureRequest().setNoAttributes()
        for feature in layer.getFeatures(request):
            geometry = feature.geometry()
            if geometry is None or geometry.isEmpty():
                continue
            if transform is not None:
                geometry.transform(transform)
            count += 1
            area += geometry.area()
            length += geometry.length()
            bounds = geometry.boundingBox()
            diagonals += math.hypot(bounds.width(), bounds.height())
            # 2 * area / perimeter: the inradius of a convex polygon or more.
            radii.append(2.0 * geometry.area() / max(geometry.length(), 1e-12))
        cache[key] = (area, length, count, diagonals)
        self.__dict__.setdefault("_radii", {})[key] = radii
        return cache[key]

    # The last zooms of an export share the output budget first: an export
    # from zoom 0 spends it on the zooms where a layer is drawn large, not on
    # the zooms where it is a few pixels (where per-zoom copies cost as much).
    BUDGET_TOP_ZOOMS = 4

    def _zoom_steps(self, costs: Dict[int, float], choices: Tuple[int, ...],
                    floor: int = 0) -> Dict[int, int]:
        """Sub-zoom steps per zoom within the output budget; ``costs`` maps
        each zoom to its output size per step, ``choices`` the step counts
        from fine to coarse. The last BUDGET_TOP_ZOOMS zooms get the finest
        step count that fits them together, then the zooms below, highest
        first, the finest that still fits (coarser and coarser). No zoom gets
        fewer steps than one count for all zooms would give (the finest that
        fits them all). Zooms past the budget get ``floor`` (0: not
        materialized)."""
        budget = self.MAX_PATTERN_ELEMENTS
        zooms = sorted(costs, reverse=True)
        coarsest = choices[-1]
        reserve = next((n for n in choices if sum(costs.values()) * n <= budget), 0)
        below, total = {}, 0.0  # cost of the zooms below each zoom
        for zoom in reversed(zooms):
            below[zoom] = total
            total += costs[zoom]
        top = zooms[:self.BUDGET_TOP_ZOOMS]
        top_cost = sum(costs[zoom] for zoom in top)
        level = next((n for n in choices
                      if top and top_cost * n + below[top[-1]] * reserve <= budget), coarsest)
        steps = {zoom: floor for zoom in zooms}
        for zoom in zooms:
            while level and costs[zoom] * level + below[zoom] * reserve > budget:
                level = next((n for n in choices if n < level), 0)
            if not level:
                break
            steps[zoom] = level
            budget -= costs[zoom] * level
        return steps

    def _layer_radii(self, layer) -> List[float]:
        """Per feature of a polygon layer, about the largest inset that is not
        empty (project map units, cached with _layer_totals)."""
        self._layer_totals(layer)
        return self.__dict__["_radii"][layer.id()]

    def _over_budget(self, flat_rule: FlattenedRule, elements: float, what: str) -> bool:
        if elements <= self.MAX_PATTERN_ELEMENTS:
            return False
        self._report("Q2VT_PATTERN_BUDGET",
                     f"{what} would need about {int(elements):,} features (budget "
                     f"{self.MAX_PATTERN_ELEMENTS:,}); it is drawn as a texture at every "
                     "zoom.", flat_rule)
        return True

    def _dense_split(self, flat_rule: FlattenedRule, spacing: float, materialize,
                     min_px: Optional[float] = None, elements: float = 0.0,
                     what: str = "Pattern", overzoom: bool = False):
        """Texture for the zooms where a map-unit grid is dense, materialized
        points for the zooms where its spacing is large on screen. ``elements``
        estimates the features the materialized part would create.
        ``overzoom``: a pattern along lines (a tile holds a few of them per
        line) is also materialized when it is large enough only past the last
        tile zoom, drawn from the last tiles."""
        if self._over_budget(flat_rule, elements, what):
            return None
        low, high = self._zoom_range(flat_rule)
        switch = None
        zooms = list(range(low, high + 1)) + (self._overzoom_zooms(flat_rule) if overzoom else [])
        for zoom in zooms:
            px = spacing * 96.0 / (25.4 * ZoomLevels.map_units_per_mm(zoom))
            if px >= (self.GRID_MIN_SPACING_PX if min_px is None else min_px):
                switch = zoom
                break
        if switch == low:
            return materialize(flat_rule)
        texture = flat_rule.derive()
        if switch is None:
            return None  # dense at every zoom it is viewed at: texture only
        grid = flat_rule.derive()
        if switch > high:  # on the last tiles, from an overzoom on
            grid.set_attr("o", high)
            grid.set_attr("v", switch)
        else:
            texture.set_attr("i", switch - 1)
            grid.set_attr("o", switch)
        if flat_rule.visibility is not None:
            texture.visibility = flat_rule.visibility.intersect(
                ZoomInterval(0.0, float(switch)))
            grid.visibility = flat_rule.visibility.intersect(ZoomInterval(float(switch), None))
        return [texture] + materialize(grid)

    def _random_fill(self, flat_rule: FlattenedRule, layer) -> Optional[List[FlattenedRule]]:
        """``QgsRandomMarkerFillSymbolLayer::render``: an absolute count per
        feature, or a density per map-unit area, becomes random points
        (texture where they are dense on screen). A density per screen area
        stays a texture: QGIS draws it at a constant screen density."""
        marker = layer.subSymbol()
        if marker is None:
            return None
        props = layer.dataDefinedProperties()
        P = QgsSymbolLayer.Property
        if any(props.isActive(k) for k in (P.PropertyPointCount, P.PropertyDensityArea,
                                           P.PropertyRandomSeed, P.PropertyClipPoints)):
            self._report("Q2VT_PATTERN_APPROXIMATE", "Data-defined count, density, seed or "
                         "clipping of a random marker fill uses the static value.", flat_rule)
        count = int(layer.pointCount())
        density = 0.0
        if _enum_value(layer.countMethod()) == _enum_value(Qgis.PointCountMethod.DensityBased):
            if normalize_unit(layer.densityAreaUnit()) != "map":
                return None
            density = float(layer.densityArea())
            if density <= 0:
                return None
        if count <= 0:
            return []
        if layer.clipPoints():
            self._report("Q2VT_PATTERN_APPROXIMATE", "Random markers crossing the polygon "
                         "edge are drawn whole (QGIS clips them to the shape).", flat_rule)
        self._report("Q2VT_PATTERN_APPROXIMATE", "Random marker positions differ from QGIS "
                     "(QGIS draws them in screen coordinates, so they change with the view); "
                     "their number and density follow QGIS.", flat_rule)
        recipe = mat.random_points_recipe(count, density, int(layer.seed()),
                                          self.project_crs or flat_rule.layer.crs().authid())

        def points(rule):
            return [self._with_symbol(rule, inherit_opacity(flat_rule.rule.symbol(), marker.clone()),
                                      0, 1, recipe)]
        if not density:
            if self._over_budget(flat_rule, count * self._layer_totals(flat_rule.layer)[2],
                                 "Random marker fill"):
                return None
            return points(flat_rule)
        return self._dense_split(flat_rule, math.sqrt(density / count), points,
                                 elements=count * self._layer_totals(flat_rule.layer)[0] / density,
                                 what="Random marker fill")

    # -- pattern grids (map units) --------------------------------------------
    def _anchor(self, layer, flat_rule) -> str:
        try:
            if layer.coordinateReference() != Qgis.SymbolCoordinateReference.Feature:
                self._report("Q2VT_PATTERN_APPROXIMATE",
                             "Viewport-anchored pattern is anchored to the map origin.",
                             flat_rule)
                return "world"
        except AttributeError:
            pass
        return "feature"

    def _map_units(self, value: float, unit, what: str, flat_rule, cell: float,
                   wrap: float = 0.0) -> float:
        """Offset or displacement of a map-unit grid in map units; percentages
        are of the grid ``cell`` (QgsPointPatternFillSymbolLayer). A non-zero
        ``wrap`` keeps the value within that period (QGIS: std::fmod)."""
        if not value:
            return 0.0
        unit = normalize_unit(unit)
        if unit == "map":
            pass
        elif unit == PERCENT:
            value = value * cell / 100.0
        else:
            self._report("Q2VT_PATTERN_APPROXIMATE",
                         f"Pattern {what} in screen units is ignored for a map-unit grid.",
                         flat_rule)
            return 0.0
        return math.fmod(value, wrap) if wrap else value

    def _grid_shift(self, layer, flat_rule, shape: bool):
        """(displacement x, y, offset x, y) of a map-unit point grid. QGIS
        wraps the offset within one cell (renderPolygon) or two cells for the
        clipped "Shape" texture (applyPattern)."""
        dx, dy = layer.distanceX(), layer.distanceY()
        period = 2.0 if shape else 1.0
        return (
            self._map_units(layer.displacementX(), layer.displacementXUnit(), "displacement",
                            flat_rule, dx),
            self._map_units(layer.displacementY(), layer.displacementYUnit(), "displacement",
                            flat_rule, dy),
            self._map_units(layer.offsetX(), layer.offsetXUnit(), "offset", flat_rule, dx,
                            period * dx),
            self._map_units(layer.offsetY(), layer.offsetYUnit(), "offset", flat_rule, dy,
                            period * dy))

    def _point_grid(self, flat_rule: FlattenedRule, layer) -> List[FlattenedRule]:
        marker = layer.subSymbol()
        if marker is None:
            return []
        inset = 0.0
        clip = int(layer.clipMode()) if hasattr(layer, "clipMode") else 1
        strokes = self._stroke_marker(marker)
        if strokes is not None and not layer.angle():
            return self._stroke_grid(flat_rule, layer, marker, strokes, clip)
        closed = self._closed_marker(marker)
        if closed is not None and not layer.angle() and clip == int(Qgis.MarkerClipMode.Shape):
            return self._shape_grid(flat_rule, layer, marker, closed)
        if clip == int(Qgis.MarkerClipMode.CompletelyWithin):
            if normalize_unit(marker.sizeUnit()) == "map":
                inset = marker.size() / 2.0
            else:
                self._report("Q2VT_PATTERN_APPROXIMATE", "Screen-size markers are kept "
                             "when their centre is inside the polygon.", flat_rule)
        elif clip != int(Qgis.MarkerClipMode.CentroidWithin):
            self._report("Q2VT_PATTERN_APPROXIMATE",
                         "Pattern markers crossing the polygon edge are drawn whole when their "
                         "centre is inside (QGIS clips them to the shape).", flat_rule)
        if layer.angle():
            self._report("Q2VT_PATTERN_APPROXIMATE", "Rotated point patterns are exported "
                         "unrotated.", flat_rule)
        recipe = mat.grid_recipe(
            layer.distanceX(), layer.distanceY(),
            *self._grid_shift(layer, flat_rule, clip == int(Qgis.MarkerClipMode.Shape)),
            self.project_crs or flat_rule.layer.crs().authid(), self._anchor(layer, flat_rule),
            inset, rows_from_top=clip != int(Qgis.MarkerClipMode.Shape),
            deviation=self._deviation(layer, flat_rule), seed=layer.seed())
        return [self._with_symbol(flat_rule, inherit_opacity(flat_rule.rule.symbol(), marker.clone()),
                                  0, 1, recipe)]

    @staticmethod
    def _whole_marker_pattern(layer) -> bool:
        """A point pattern spaced in screen units that QGIS draws marker by
        marker (clip mode other than "Shape": only whole markers, chosen by
        their centre or bounds), not as a clipped texture."""
        try:
            clip = int(layer.clipMode())
        except AttributeError:
            return False
        return clip != int(Qgis.MarkerClipMode.Shape) and layer.subSymbol() is not None \
            and not layer.angle() \
            and _to_mm(layer.distanceX(), layer.distanceXUnit()) is not None \
            and _to_mm(layer.distanceY(), layer.distanceYUnit()) is not None

    # Screen-unit point grids: positions per eighth of a zoom (spacing within
    # +-4.5 % of QGIS's); fewer bands when over the output budget.
    SCREEN_GRID_STEPS = (8, 4, 2, 1)

    def _screen_point_grid(self, flat_rule: FlattenedRule, layer) -> Optional[List[FlattenedRule]]:
        """QgsPointPatternFillSymbolLayer::renderPolygon for clip modes other
        than "Shape": whole markers on a grid starting at the top-left of the
        feature's bounding box (or the view's corner), kept when their centre
        is inside ("centroid within"), their bounds inside ("completely
        within") or touching it ("no clipping"). Screen spacing becomes map
        units per eighth of a zoom; the markers keep their screen size."""
        marker = layer.subSymbol()
        dx_mm = _to_mm(layer.distanceX(), layer.distanceXUnit())
        dy_mm = _to_mm(layer.distanceY(), layer.distanceYUnit())
        if not dx_mm or not dy_mm or dx_mm <= 0 or dy_mm <= 0 \
                or dx_mm * 96.0 / 25.4 < self.GRID_MIN_SPACING_PX:
            return None
        low, high = self._zoom_range(flat_rule)
        if low > high:
            return None

        def mm(value, unit, cell_mm):
            if not value:
                return 0.0
            if normalize_unit(unit) == PERCENT:
                return value * cell_mm / 100.0
            converted = _to_mm(value, unit)
            if converted is None:
                self._report("Q2VT_PATTERN_APPROXIMATE",
                             "Map-unit offsets of a screen-unit point pattern are ignored.",
                             flat_rule)
            return converted or 0.0
        disp_x = mm(layer.displacementX(), layer.displacementXUnit(), dx_mm)
        disp_y = mm(layer.displacementY(), layer.displacementYUnit(), dy_mm)
        # QGIS: the offset wraps within one cell (std::fmod).
        off_x = math.fmod(mm(layer.offsetX(), layer.offsetXUnit(), dx_mm), dx_mm)
        off_y = math.fmod(mm(layer.offsetY(), layer.offsetYUnit(), dy_mm), dy_mm)
        size_mm = _to_mm(marker.size(), marker.sizeUnit()) or 0.0
        clip = int(layer.clipMode())
        # Marker centre in the polygon shrunk (completely within) or grown
        # (no clipping) by the marker's half size.
        reach = {int(Qgis.MarkerClipMode.CompletelyWithin): size_mm / 2.0,
                 int(Qgis.MarkerClipMode.NoClipping): -size_mm / 2.0}.get(clip, 0.0)
        if layer.maximumRandomDeviationX() or layer.maximumRandomDeviationY():
            self._report("Q2VT_PATTERN_APPROXIMATE", "Random deviation of pattern markers "
                         "in screen units is not reproduced.", flat_rule)
        anchor = self._anchor(layer, flat_rule)
        crs = self.project_crs or flat_rule.layer.crs().authid()
        zoom_rules = self._per_zoom(flat_rule)
        area = self._layer_totals(flat_rule.layer)[0]
        elements = sum(area / (dx_mm * dy_mm * self._map_units_per_mm(flat_rule, z + 0.5) ** 2)
                       for z in range(low, high + 1))
        steps = next((n for n in self.SCREEN_GRID_STEPS
                      if elements * n <= self.MAX_PATTERN_ELEMENTS), 0)
        if not steps:
            self._over_budget(flat_rule, elements, "Point pattern")
            return None
        symbol = inherit_opacity(flat_rule.rule.symbol(), marker.clone())
        rules = []
        visibility = flat_rule.visibility
        if self._overzoom_zooms(flat_rule):
            # Beyond the archive's last zoom the pattern stays a (true size)
            # texture: the last band's map spacing spread the markers apart
            # as the map overzoomed.
            over = flat_rule.derive()
            over.set_attr("o", high)
            over.set_attr("i", high)
            over.visibility = (visibility or ZoomInterval(float(low), None)).intersect(
                ZoomInterval(float(high + 1), None))
            last = zoom_rules[-1]
            last.visibility = (last.visibility or ZoomInterval(float(high), None)).intersect(
                ZoomInterval(float(high), float(high + 1)))
            if not over.visibility.is_empty:
                rules.append(over)
        for index, rule in enumerate(zoom_rules):
            for band, zoom in self._sub_zoom_bands(rule, steps, index == len(zoom_rules) - 1):
                per_mm = self._map_units_per_mm(flat_rule, zoom)
                recipe = mat.grid_recipe(
                    dx_mm * per_mm, dy_mm * per_mm, disp_x * per_mm, disp_y * per_mm,
                    off_x * per_mm, off_y * per_mm, crs, anchor, reach * per_mm,
                    rows_from_top=True, ordered=True)
                derived = self._with_symbol(band, symbol.clone(), 0, 1, recipe)
                derived.z_order = "source"  # QGIS draws column by column
                rules.append(derived)
        return rules

    def _deviation(self, layer, flat_rule):
        """Maximum random deviation (map units) of point-pattern markers."""
        values = []
        for value, unit in ((layer.maximumRandomDeviationX(), layer.randomDeviationXUnit()),
                            (layer.maximumRandomDeviationY(), layer.randomDeviationYUnit())):
            if value and normalize_unit(unit) != "map":
                self._report("Q2VT_PATTERN_APPROXIMATE", "Random deviation of pattern markers "
                             "in screen units is not reproduced.", flat_rule)
                value = 0.0
            values.append(float(value or 0.0))
        if values[0] or values[1]:
            self._report("Q2VT_PATTERN_APPROXIMATE", "Randomly deviated pattern markers: the "
                         "deviations follow QGIS's range but not its random sequence.", flat_rule)
        return tuple(values)

    @staticmethod
    def _stroke_marker(marker):
        """Shape name of a single stroke-only simple marker sized in map units
        (its drawing is pure line work), or None."""
        if marker.symbolLayerCount() != 1:
            return None
        layer = marker.symbolLayer(0)
        if layer.layerType() != "SimpleMarker" or layer.paintEffect() is not None and \
                layer.paintEffect().enabled() and \
                type(layer.paintEffect()).__name__ not in ("QgsEffectStack", "QgsDefaultPaintEffect"):
            return None
        names = {getattr(Qgis.MarkerShape, n): n for n in mat.STROKE_MARKER_PATHS}
        shape = names.get(layer.shape())
        if shape is None or normalize_unit(layer.sizeUnit()) != "map":
            return None
        offset = layer.offset()
        if (offset.x() or offset.y()) and normalize_unit(layer.offsetUnit()) != "map":
            return None
        active = {k for k in layer.dataDefinedProperties().propertyKeys()
                  if layer.dataDefinedProperties().property(k).isActive()}
        # A data-defined stroke colour carries over to the exported line work
        # (the pieces keep their feature's attributes); anything else would
        # change the geometry.
        if active - {int(QgsSymbolLayer.Property.PropertyStrokeColor)}:
            return None
        return shape

    @staticmethod
    def _closed_marker(marker):
        """Shape name of a single closed simple marker (square, diamond,
        triangle, circle...) sized in map units without data-defined
        properties; None otherwise."""
        if marker.symbolLayerCount() != 1:
            return None
        layer = marker.symbolLayer(0)
        if layer.layerType() != "SimpleMarker" or normalize_unit(layer.sizeUnit()) != "map":
            return None
        props = layer.dataDefinedProperties()
        if any(props.isActive(key) for key in props.propertyKeys()):
            return None
        if (layer.offset().x() or layer.offset().y()) and \
                normalize_unit(layer.offsetUnit()) != "map":
            return None
        name = getattr(layer.shape(), "name", None) or \
            QgsSimpleMarkerSymbolLayerBase.encodeShape(layer.shape())
        return name if name in mat.MARKER_SHAPE_POLYGONS else None

    def _shape_grid(self, flat_rule, layer, marker, shape) -> List[FlattenedRule]:
        """Point pattern of closed simple markers clipped to the polygon
        ("Shape" clip mode): QGIS draws the pattern clipped to the feature,
        so edge markers are cut; sprites cannot be, so the markers' fill
        (polygons) and outline (closed lines) are exported as geometry,
        clipped to the polygon."""
        from qgis.core import QgsFillSymbol, QgsSimpleFillSymbolLayer  # pylint: disable=import-outside-toplevel
        simple = marker.symbolLayer(0)
        offset = simple.offset()
        paths = mat.marker_paths((mat.MARKER_SHAPE_POLYGONS[shape],), simple.size(),
                                 simple.angle(), offset.x(), offset.y())
        crs = self.project_crs or flat_rule.layer.crs().authid()

        def recipe(fill):
            return mat.grid_recipe(
                layer.distanceX(), layer.distanceY(),
                *self._grid_shift(layer, flat_rule, True),
                crs, self._anchor(layer, flat_rule), 0.0, rows_from_top=False,
                clip_shape=True, clip_mode="shape", deviation=self._deviation(layer, flat_rule),
                seed=layer.seed(), paths=paths, fill=fill)
        parts = []
        if simple.color().alpha() > 0:
            fill = QgsSimpleFillSymbolLayer(simple.color())
            fill.setStrokeStyle(Qt.PenStyle.NoPen)
            symbol = QgsFillSymbol([fill])
            symbol.setOpacity(marker.opacity())
            inherit_opacity(flat_rule.rule.symbol(), symbol)
            parts.append(self._with_symbol(flat_rule, symbol, 2, 1, recipe(True)))
        if simple.strokeStyle() != Qt.PenStyle.NoPen and simple.strokeColor().alpha() > 0:
            stroke = QgsSimpleLineSymbolLayer(simple.strokeColor(), simple.strokeWidth())
            stroke.setWidthUnit(simple.strokeWidthUnit())
            stroke.setWidthMapUnitScale(simple.strokeWidthMapUnitScale())
            stroke.setPenStyle(simple.strokeStyle())
            stroke.setPenJoinStyle(simple.penJoinStyle())
            symbol = QgsLineSymbol([stroke])
            symbol.setOpacity(marker.opacity())
            inherit_opacity(flat_rule.rule.symbol(), symbol)
            outline = self._with_symbol(flat_rule, symbol, 1, 3, recipe(False))
            outline.order = flat_rule.order + (1,) if flat_rule.order else ()
            parts.append(outline)
        return parts

    def _stroke_grid(self, flat_rule, layer, marker, shape, clip) -> List[FlattenedRule]:
        """Point pattern of stroke-only markers (lines, crosses...) sized in map
        units: the markers' line work is exported as line features, clipped to
        the polygon for "Shape" clipping, and drawn with the marker's stroke
        (width in its own unit, as QGIS draws it at every scale)."""
        simple = marker.symbolLayer(0)
        if simple.strokeStyle() == Qt.PenStyle.NoPen:
            return []  # nothing is drawn
        offset = simple.offset()
        segments = mat.marker_segments(shape, simple.size(), simple.angle(),
                                       offset.x(), offset.y())
        from qgis.core import QgsFillSymbol, QgsSimpleFillSymbolLayer  # pylint: disable=import-outside-toplevel
        # A solid stroke in map units becomes polygons (its outline), so
        # "Shape" clipping cuts it at the polygon edge exactly.
        polygons = None
        # Only wide strokes cut by the shape need it (a hairline's overshoot
        # is invisible, and line work stays cheaper).
        if normalize_unit(simple.strokeWidthUnit()) == "map" and simple.strokeWidth() > 0 \
                and simple.strokeStyle() == Qt.PenStyle.SolidLine \
                and clip == int(Qgis.MarkerClipMode.Shape) \
                and simple.strokeWidth() >= 0.2 * min(layer.distanceX(), layer.distanceY()):
            cap = {Qt.PenCapStyle.FlatCap: "flat", Qt.PenCapStyle.RoundCap: "round"}.get(
                simple.penCapStyle(), "square")
            join = {Qt.PenJoinStyle.BevelJoin: "bevel", Qt.PenJoinStyle.RoundJoin: "round"}.get(
                simple.penJoinStyle(), "miter")
            polygons = (simple.strokeWidth() / 2.0, cap, join)
            fill = QgsSimpleFillSymbolLayer(simple.strokeColor(), Qt.BrushStyle.SolidPattern,
                                            simple.strokeColor(), Qt.PenStyle.NoPen)
            colour = simple.dataDefinedProperties().property(QgsSymbolLayer.Property.PropertyStrokeColor)
            if colour and colour.isActive():
                fill.dataDefinedProperties().setProperty(QgsSymbolLayer.Property.PropertyFillColor,
                                                         QgsProperty(colour))
            symbol = QgsFillSymbol([fill])
        else:
            stroke = QgsSimpleLineSymbolLayer(simple.strokeColor(), simple.strokeWidth())
            stroke.setWidthUnit(simple.strokeWidthUnit())
            stroke.setWidthMapUnitScale(simple.strokeWidthMapUnitScale())
            stroke.setPenStyle(simple.strokeStyle())
            stroke.setPenCapStyle(simple.penCapStyle())
            stroke.setPenJoinStyle(simple.penJoinStyle())
            colour = simple.dataDefinedProperties().property(QgsSymbolLayer.Property.PropertyStrokeColor)
            if colour and colour.isActive():
                stroke.dataDefinedProperties().setProperty(QgsSymbolLayer.Property.PropertyStrokeColor,
                                                           QgsProperty(colour))
            symbol = QgsLineSymbol([stroke])
        symbol.setOpacity(marker.opacity())
        inherit_opacity(flat_rule.rule.symbol(), symbol)
        recipe = mat.grid_recipe(
            layer.distanceX(), layer.distanceY(),
            *self._grid_shift(layer, flat_rule, clip == int(Qgis.MarkerClipMode.Shape)),
            self.project_crs or flat_rule.layer.crs().authid(), self._anchor(layer, flat_rule),
            0.0, rows_from_top=clip != int(Qgis.MarkerClipMode.Shape), segments=segments,
            clip_shape=clip == int(Qgis.MarkerClipMode.Shape), clip_mode={
                int(Qgis.MarkerClipMode.Shape): "shape",
                int(Qgis.MarkerClipMode.CentroidWithin): "centroid",
                int(Qgis.MarkerClipMode.CompletelyWithin): "within",
                int(Qgis.MarkerClipMode.NoClipping): "none"}.get(clip, "shape"),
            deviation=self._deviation(layer, flat_rule), seed=layer.seed(), stroke=polygons)
        return [self._with_symbol(flat_rule, symbol, 2 if polygons else 1, 1, recipe)]

    def _svg_grid(self, flat_rule: FlattenedRule, layer) -> List[FlattenedRule]:
        from qgis.core import QgsApplication, QgsSvgMarkerSymbolLayer  # pylint: disable=import-outside-toplevel
        width = layer.patternWidth()
        if width <= 0:
            return []
        size = QgsApplication.svgCache().svgViewboxSize(
            layer.svgFilePath(), width, layer.svgFillColor(), layer.svgStrokeColor(),
            layer.svgStrokeWidth(), 1.0)
        height = width * (size.height() / size.width()) if size.width() > 0 else width
        marker = QgsSvgMarkerSymbolLayer(layer.svgFilePath(), width, layer.angle())
        marker.setSizeUnit(layer.patternWidthUnit())
        marker.setFillColor(layer.svgFillColor())
        marker.setStrokeColor(layer.svgStrokeColor())
        marker.setStrokeWidth(layer.svgStrokeWidth())
        marker.setStrokeWidthUnit(layer.svgStrokeWidthUnit())
        self._report("Q2VT_PATTERN_APPROXIMATE",
                     "SVG fill tiles crossing the polygon edge are drawn whole when their "
                     "centre is inside (QGIS clips the texture).", flat_rule)
        if abs(layer.angle() % 360.0) > 1e-9:
            self._report("Q2VT_PATTERN_APPROXIMATE",
                         "Rotated SVG fill in map units: each SVG is turned, but the grid is not "
                         "(QGIS turns the whole texture).", flat_rule)
        recipe = mat.grid_recipe(width, height, 0.0, 0.0, width / 2.0, -height / 2.0,
                                 self.project_crs or flat_rule.layer.crs().authid(),
                                 self._anchor(layer, flat_rule))
        # QGIS multiplies the fill symbol's opacity into the SVG texture.
        return [self._with_symbol(flat_rule, inherit_opacity(flat_rule.rule.symbol(),
                                                             QgsMarkerSymbol([marker])), 0, 1, recipe)]

    # -- marker lines --------------------------------------------------------
    def _marker_points_symbol(self, sub_symbol: QgsMarkerSymbol, rotate: bool,
                              extra_angle: float, offset: float, offset_unit,
                              flat_rule: FlattenedRule) -> QgsMarkerSymbol:
        symbol = inherit_opacity(flat_rule.rule.symbol(), sub_symbol.clone())
        for index in range(symbol.symbolLayerCount()):
            marker = symbol.symbolLayer(index)
            if rotate:
                # QGIS adds the line's angle to the marker's own angle, a
                # data-defined one included (hash angles from a field).
                own = marker.dataDefinedProperties().property(QgsSymbolLayer.Property.PropertyAngle)
                own_text = own.asExpression() if own is not None and own.isActive() else ""
                rotation = mat.marker_rotation_expression(marker.angle() + extra_angle)
                if own_text:
                    rotation = (f"{mat.marker_rotation_expression(extra_angle)} + "
                                f"coalesce(({own_text}), {float(marker.angle())!r})")
                elif own is not None and own.isActive():
                    self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                                 "Data-defined marker angle on a marker line: the static "
                                 "angle is used.", flat_rule)
                marker.setDataDefinedProperty(
                    QgsSymbolLayer.Property.PropertyAngle, QgsProperty.fromExpression(rotation))
            elif extra_angle:
                marker.setAngle(marker.angle() + extra_angle)
            if offset:
                # Perpendicular offset = local +y (right of the line direction).
                if normalize_unit(marker.offsetUnit()) == normalize_unit(offset_unit):
                    delta = offset
                else:
                    own_mm = _to_mm(1.0, marker.offsetUnit())
                    off_mm = _to_mm(offset, offset_unit)
                    if own_mm and off_mm is not None:
                        delta = off_mm / own_mm
                    else:
                        self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                                     "Marker-line offset in map units is ignored for "
                                     "markers sized in other units.", flat_rule)
                        continue
                # QGIS turns a marker's offset with the marker's own angle
                # (dimension arrows at -90 degrees): turn it back, so the
                # offset stays perpendicular to the line (exact at every zoom,
                # MapLibre applies it in screen units in the rotated frame).
                own = math.radians(marker.angle() + extra_angle)
                current = marker.offset()
                marker.setOffset(QPointF(current.x() + delta * math.sin(own),
                                         current.y() + delta * math.cos(own)))
        return symbol

    @staticmethod
    def _exact_interval(layer) -> bool:
        """Interval markers whose positions do not depend on the zoom (map-unit
        interval and offset along the line) can be placed exactly."""
        if "Interval" not in _flag_names(layer.placements()):
            return False
        try:
            along, along_unit = float(layer.offsetAlongLine()), layer.offsetAlongLineUnit()
        except AttributeError:
            along, along_unit = 0.0, None
        if layer.dataDefinedProperties().isActive(QgsSymbolLayer.Property.PropertyInterval):
            return False
        return normalize_unit(layer.intervalUnit()) == "map" and layer.interval() > 0 and (
            not along or normalize_unit(along_unit) == "map")

    @staticmethod
    def _zoom_interval(layer) -> bool:
        """Interval markers with a screen-unit interval (or offset along the
        line): their map positions change with the zoom, so they are placed
        exactly once per zoom (see ``_screen_interval_rules``)."""
        if "Interval" not in _flag_names(layer.placements()):
            return False
        if layer.dataDefinedProperties().isActive(QgsSymbolLayer.Property.PropertyInterval) \
                or layer.interval() <= 0:
            return False
        try:
            along, along_unit = float(layer.offsetAlongLine()), layer.offsetAlongLineUnit()
        except AttributeError:
            along, along_unit = 0.0, None

        def usable(value, unit):
            return not value or normalize_unit(unit) == "map" or _to_mm(value, unit) is not None
        return usable(layer.interval(), layer.intervalUnit()) and usable(along, along_unit) \
            and usable(layer.offset(), layer.offsetUnit())

    def _screen_interval_rules(self, native: FlattenedRule, layer, sub, crs: str
                               ) -> Optional[List[FlattenedRule]]:
        """QGIS keeps a screen-unit interval constant on screen, so its map
        spacing halves at every zoom in. MapLibre's own line placement drops
        markers that would overhang the end of a line piece (every tile edge
        and ring start), so the positions are materialized per zoom instead,
        converted at the middle of the zoom; beyond the archive's last zoom
        the native placement keeps the screen spacing."""
        low, high = self._zoom_range(native)
        if low > high:
            return None
        if self.fast_markers:
            self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                         "Fast marker lines: screen-size interval markers are placed by the browser "
                         "along the lines, not at QGIS's positions.", native)
            return None
        try:
            along, along_unit = float(layer.offsetAlongLine()), layer.offsetAlongLineUnit()
        except AttributeError:
            along, along_unit = 0.0, None

        def to_map(value, unit, zoom: float):
            """Map distance of a screen distance at (fractional) ``zoom``."""
            if abs(value or 0.0) <= 1e-9:
                return 0.0
            if normalize_unit(unit) == "map":
                return float(value)
            return _to_mm(value, unit) * ZoomLevels.map_units_per_mm(zoom)

        interval_mm = _to_mm(layer.interval(), layer.intervalUnit())
        if interval_mm is not None and \
                interval_mm * 96.0 / 25.4 < self.INTERVAL_MIN_SPACING_PX:
            return None
        length = self._layer_totals(native.layer)[1]
        per_zoom_elements = sum(length / max(to_map(layer.interval(), layer.intervalUnit(), zoom + 0.5),
                                             1e-9) for zoom in range(low, high + 1))
        # Positions in eighths of a zoom (spacing within +-4 % of QGIS's;
        # fewer bands when the markers would exceed the output budget).
        steps = next((n for n in (8, 4, 2) if per_zoom_elements * n <= self.MAX_PATTERN_ELEMENTS), 1)
        if self._over_budget(native, per_zoom_elements * steps, "Marker line"):
            return None
        symbol = self._marker_points_symbol(sub, layer.rotateSymbols(), 0.0, 0.0,
                                            layer.offsetUnit(), native)
        zoom_rules = self._per_zoom(native)
        rules = []
        visibility = native.visibility
        if visibility is None or visibility.max_zoom is None or visibility.max_zoom > high + 1:
            over = native.derive()
            over.set_attr("o", high)
            over.set_attr("i", high)
            over.visibility = (visibility or ZoomInterval(float(low), None)).intersect(
                ZoomInterval(float(high + 1), None))
            last = zoom_rules[-1]
            last.visibility = (last.visibility or ZoomInterval(float(high), None)).intersect(
                ZoomInterval(float(high), float(high + 1)))
            if not over.visibility.is_empty:
                rules.extend(self._native_interval_bands(over, 8))
        for index, zoom_rule in enumerate(zoom_rules):
            for rule, zoom in self._sub_zoom_bands(zoom_rule, steps, index == len(zoom_rules) - 1):
                recipe = mat.interval_points(to_map(layer.interval(), layer.intervalUnit(), zoom),
                                             to_map(along, along_unit, zoom),
                                             to_map(layer.offset(), layer.offsetUnit(), zoom), crs)
                extra = []
                if _ring_filter(layer):
                    extra.append(("ring_filter", _ring_filter(layer)))
                average = self._average_angle_length(layer, rule)
                if average:
                    extra.append(("average", average))
                recipe = mat.Recipe(recipe.kind, recipe.placements, recipe.params + tuple(extra))
                rules.append(self._with_symbol(rule, symbol.clone(), 0, 2, recipe))
        return rules

    def _native_interval_bands(self, over: FlattenedRule, steps: int) -> List[FlattenedRule]:
        """Native screen-spaced interval markers past the last tiles, one
        style per ``steps``-th of a zoom on the same dataset: MapLibre lays
        line symbols out once per whole zoom and scales them up to the next,
        so a band's spacing is laid out for its middle (the converter, see
        LinePropertyExtractor.get_marker_line_spacing), within +-4 % of
        QGIS's with eight bands. Zooms past overzoom_to (the viewer's limit)
        are not drawn."""
        visible = over.visibility
        first = int(math.floor(visible.min_zoom + 1e-9))
        steps = max(steps, 1)
        bands = []
        for zoom in range(first, max(first, self.overzoom_to) + 1):
            for step in range(steps):
                interval = visible.intersect(ZoomInterval(zoom + step / steps,
                                                          zoom + (step + 1) / steps))
                if interval.is_empty:
                    continue
                band = over.derive()
                band.set_attr("v", zoom)
                band.set_attr("b", step)
                band.visibility = interval
                band.output_dataset = over.output_dataset  # the same lines
                bands.append(band)
        return bands

    def _sub_zoom_bands(self, rule: FlattenedRule, steps: int, last: bool):
        """[(rule, middle zoom)]: ``rule`` (one zoom, see rule_zoom) split
        into ``steps`` equal zoom bands (attribute "b"); the last band of an
        open-ended last zoom stays visible when overzooming."""
        low = float(self.rule_zoom(rule))
        visible = rule.visibility or ZoomInterval(low, None if last else low + 1.0)
        if steps <= 1:
            return [(rule, low + 0.5)]
        bands = []
        for step in range(steps):
            start, end = low + step / steps, low + (step + 1) / steps
            open_end = step == steps - 1 and visible.max_zoom is None
            interval = visible.intersect(ZoomInterval(start, None if open_end else end))
            if interval.is_empty:
                continue
            band = rule.derive()
            band.visibility = interval
            band.set_attr("b", step)
            bands.append((band, (start + end) / 2))
        return bands

    def _marker_line(self, flat_rule: FlattenedRule, layer) -> Optional[List[FlattenedRule]]:
        placements = _flag_names(layer.placements())
        points = placements & mat.POINT_PLACEMENTS
        exact_interval = self._exact_interval(layer)
        zoom_interval = not exact_interval and self._zoom_interval(layer)
        if not points and not exact_interval and not zoom_interval:
            if "Interval" in placements and \
                    layer.dataDefinedProperties().isActive(QgsSymbolLayer.Property.PropertyInterval):
                self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                             "Data-defined interval: the browser spaces these markers along the lines, "
                             "not at QGIS's positions.", flat_rule)
            return None  # interval driven by data: native repeated symbol
        if "CurvePoint" in points:
            self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                         "Curve-point markers are placed on every vertex.", flat_rule)
        sub = layer.subSymbol()
        if sub is None:
            return []
        offset, offset_unit = layer.offset(), layer.offsetUnit()
        if abs(offset) <= 1e-9:
            offset = 0.0  # styles store float noise such as 5.55e-17
        crs = self.project_crs or flat_rule.layer.crs().authid()
        # QGIS offsets the line (polygons: every ring, as a buffer) before
        # measuring positions along it; vertex ends of open lines are the only
        # positions where offsetting the markers instead is equivalent.
        needs_offset_line = (points | ({"Interval"} if exact_interval else set())) \
            - {"FirstVertex", "LastVertex"}
        if flat_rule.get_attr("g") == 2:
            needs_offset_line = points | ({"Interval"} if exact_interval else set())
        line_offset = 0.0
        # Screen-unit offsets: on an open line, markers at the ends, the
        # centre or segment centres sit on a straight stretch, where offsetting
        # the marker itself is exact at every zoom (MapLibre applies it in
        # screen units). Elsewhere the markers are placed on the offset line of
        # every zoom (converted at the middle of the zoom).
        zoom_offset_mm = None
        straight = flat_rule.get_attr("g") != 2 and not (
            (points | ({"Interval"} if exact_interval else set()))
            - {"FirstVertex", "LastVertex", "CentralPoint", "SegmentCenter"})
        if offset and (points or exact_interval) and needs_offset_line:
            if normalize_unit(offset_unit) == "map":
                line_offset, offset = offset, 0.0
            elif straight:
                pass  # the marker's own (screen) offset, see _marker_points_symbol
            elif _to_mm(offset, offset_unit) is not None and not \
                    layer.dataDefinedProperties().isActive(QgsSymbolLayer.Property.PropertyOffset):
                zoom_offset_mm, offset = _to_mm(offset, offset_unit), 0.0
            else:
                self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                             "Screen-unit offset: markers are offset from the original line; "
                             "QGIS measures positions on the offset line.", flat_rule)

        def zoom_offset(rule):
            """The screen offset in map units at the middle of ``rule``'s zoom."""
            return zoom_offset_mm * ZoomLevels.map_units_per_mm(self.rule_zoom(rule)) / math.sqrt(2)

        symbol = self._marker_points_symbol(sub, layer.rotateSymbols(), 0.0, offset,
                                            offset_unit, flat_rule)
        rules = []
        if points:
            try:
                along = float(layer.offsetAlongLine())
            except AttributeError:
                along = 0.0
            if along and points - {"Interval"}:
                self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                             "Offset along the line is not applied to vertex/centre markers.",
                             flat_rule)
            def placed(rule, line_offset):
                recipe = mat.marker_points(points, offset=line_offset, crs=crs)
                if _ring_filter(layer):
                    recipe = mat.Recipe(recipe.kind, recipe.placements, recipe.params + (
                        ("ring_filter", _ring_filter(layer)), ("crs", crs)))
                return self._with_symbol(rule, symbol.clone(), 0, 1, recipe)
            if zoom_offset_mm is None:
                rules.append(placed(flat_rule, line_offset))
            else:
                rules.extend(placed(rule, zoom_offset(rule))
                             for rule in self._per_zoom(flat_rule, overzoom=True))
        if "Interval" in placements:
            native = flat_rule.derive()
            interval_layer = layer.clone()
            interval_layer.setPlacements(Qgis.MarkerLinePlacement.Interval)
            replace_symbol_layer(native.rule.symbol(), interval_layer)
            if exact_interval:
                recipe = mat.interval_points(layer.interval(), float(layer.offsetAlongLine()),
                                             line_offset, crs)
                if _ring_filter(layer):
                    recipe = mat.Recipe(recipe.kind, recipe.placements,
                                        recipe.params + (("ring_filter", _ring_filter(layer)),))
                def exact(rule, recipe=recipe):
                    def one(rule):
                        params = recipe.params
                        if zoom_offset_mm is not None:
                            params = tuple(p for p in params if p[0] != "offset") + (
                                ("offset", zoom_offset(rule)),)
                        average = self._average_angle_length(layer, rule)
                        if average:
                            params += (("average", average),)
                        return self._with_symbol(rule, symbol.clone(), 0, 2, mat.Recipe(
                            recipe.kind, recipe.placements, params))
                    if zoom_offset_mm is None and (
                            not self._average_angle_length(layer, rule)
                            or normalize_unit(layer.averageAngleUnit()) == "map"):
                        return [one(rule)]
                    # A screen averaging length covers less of the line as the
                    # map zooms in: the same positions, one angle per zoom.
                    return [one(zoom_rule) for zoom_rule in self._per_zoom(rule, overzoom=True)]
                split = self._dense_split(native, layer.interval(), exact,
                                          elements=self._layer_totals(flat_rule.layer)[1] /
                                          max(layer.interval(), 1e-9),
                                          what="Marker line",
                                          min_px=self.INTERVAL_MIN_SPACING_PX, overzoom=True)
                rules.extend(split if split is not None else [native])
            elif zoom_interval:
                per_zoom = self._screen_interval_rules(native, layer, sub, crs)
                rules.extend(per_zoom if per_zoom is not None else [native])
            else:
                rules.append(native)
        return rules

    def _hash_as_marker_line(self, layer, flat_rule: FlattenedRule) -> QgsMarkerLineSymbolLayer:
        """Equivalent marker line: a short line marker perpendicular to the line."""
        marker = QgsSimpleMarkerSymbolLayer(Qgis.MarkerShape.Line, layer.hashLength())
        marker.setSizeUnit(layer.hashLengthUnit())
        marker.setSizeMapUnitScale(layer.hashLengthMapUnitScale())
        marker.setAngle(layer.hashAngle())
        hash_symbol = layer.subSymbol()
        line = hash_symbol.symbolLayer(0) if hash_symbol and hash_symbol.symbolLayerCount() else None
        if isinstance(line, QgsSimpleLineSymbolLayer):
            marker.setStrokeColor(line.color())
            marker.setColor(line.color())
            marker.setStrokeWidth(line.width())
            marker.setStrokeWidthUnit(line.widthUnit())
            if hash_symbol.symbolLayerCount() > 1:
                self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                             "Only the first layer of a multi-layer hash symbol is kept.",
                             flat_rule)
            marker.setStrokeWidthMapUnitScale(line.widthMapUnitScale())
            # The hash line's own data-defined colour and width: the same keys
            # on a line marker.
            line_props = line.dataDefinedProperties()
            for key in (QgsSymbolLayer.Property.PropertyStrokeColor,
                        QgsSymbolLayer.Property.PropertyStrokeWidth):
                if line_props.isActive(key):
                    marker.setDataDefinedProperty(key, QgsProperty(line_props.property(key)))
        else:
            self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                         "Hash sub-symbol is not a simple line; drawn as a thin line.", flat_rule)
        # Every layer setting that QgsTemplatedLineSymbolLayerBase shares with
        # a marker line (data-defined interval, offsets, placement, averaging,
        # ring filter); the hash's own angle and length (evaluated per hash,
        # relative to the line) become the line marker's angle and size.
        props = QgsPropertyCollection(layer.dataDefinedProperties())
        for key, marker_key in ((QgsSymbolLayer.Property.PropertyLineAngle,
                                 QgsSymbolLayer.Property.PropertyAngle),
                                (QgsSymbolLayer.Property.PropertyLineDistance,
                                 QgsSymbolLayer.Property.PropertySize)):
            if props.isActive(key):
                marker.setDataDefinedProperty(marker_key, QgsProperty(props.property(key)))
            props.setProperty(key, QgsProperty())
        if marker.dataDefinedProperties().isActive(QgsSymbolLayer.Property.PropertySize):
            self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                         "Data-defined hash length: the browser scales the hash's image, so its "
                         "stroke width changes with the length.", flat_rule)
        marker_symbol = QgsMarkerSymbol([marker])
        if hash_symbol is not None:
            marker_symbol.setOpacity(hash_symbol.opacity())
        converted = QgsMarkerLineSymbolLayer(layer.rotateSymbols(), layer.interval())
        converted.setPlacements(layer.placements())
        converted.setIntervalUnit(layer.intervalUnit())
        converted.setIntervalMapUnitScale(layer.intervalMapUnitScale())
        converted.setOffset(layer.offset())
        converted.setOffsetUnit(layer.offsetUnit())
        converted.setOffsetMapUnitScale(layer.offsetMapUnitScale())
        converted.setOffsetAlongLine(layer.offsetAlongLine())
        converted.setOffsetAlongLineUnit(layer.offsetAlongLineUnit())
        converted.setOffsetAlongLineMapUnitScale(layer.offsetAlongLineMapUnitScale())
        converted.setAverageAngleLength(layer.averageAngleLength())
        converted.setAverageAngleUnit(layer.averageAngleUnit())
        converted.setAverageAngleMapUnitScale(layer.averageAngleMapUnitScale())
        converted.setPlaceOnEveryPart(layer.placeOnEveryPart())
        converted.setRingFilter(layer.ringFilter())
        converted.setDataDefinedProperties(props)
        converted.setSubSymbol(marker_symbol)
        return converted

    # -- paint effects on lines -----------------------------------------------
    def _line_effects(self, flat_rule: FlattenedRule, layer) -> Optional[List[FlattenedRule]]:
        """A solid screen-size line with a glow / shadow / inner effects as
        separate components (fidelity/line_effects.py): its outer effects
        from a copy simplified at the effect's size per zoom (a blurred wide
        line shows spikes on dense vertices), the line, and its inner
        effects as strips coloured by QGIS per screen direction."""
        from .fidelity import line_effects as fx  # pylint: disable=import-outside-toplevel
        outer, inner, other = fx.classify(layer)
        if not outer and not inner:
            return None
        width_mm = _to_mm(layer.width(), layer.widthUnit())
        props = layer.dataDefinedProperties()
        if width_mm is None or width_mm <= 0 or layer.penStyle() != Qt.PenStyle.SolidLine \
                or layer.useCustomDashPattern() or abs(layer.offset()) > 1e-9 \
                or (props is not None and props.hasActiveProperties()):
            return None

        def to_px(value, unit):
            mm = _to_mm(value or 0.0, unit)
            return None if mm is None else mm * 96.0 / 25.4
        if any(to_px(e.blurLevel(), e.blurUnit()) is None for e in fx.effect_list(layer)
               if e.type() in fx.OUTER_EFFECTS):
            return None  # map-unit blur: the converter's zoom curves
        if other:
            self._report("Q2VT_UNSUPPORTED_EFFECT",
                         f"Paint effects {', '.join(sorted(set(other)))} are not drawn.", flat_rule)
        opacity = flat_rule.rule.symbol().opacity()
        width_px = width_mm * 96.0 / 25.4
        world = 2 * math.pi * 6378137.0
        # Geometry grid of the last tiles (4096 units per 512 px tile).
        quantum = world / (512.0 * 2.0 ** self._zoom_range(flat_rule)[1]) / 8.0

        def zoom_rules(size_px):
            """[(rule, size)]: ``size_px`` in EPSG:3857 metres (MapLibre's
            512 px tiles) at the middle of every zoom the map is viewed at;
            past the last tiles one rule per zoom while the size is coarser
            than their geometry grid, then one rule from there on."""
            out = []
            for rule in self._per_zoom(flat_rule, overzoom=True):
                size = size_px * world / (512.0 * 2.0 ** (self.rule_zoom(rule) + 0.5))
                out.append((rule, size))
                if rule.get_attr("v") is not None and size < quantum:
                    rule.visibility = flat_rule.visibility.intersect(
                        ZoomInterval(rule.visibility.min_zoom, None))
                    break
            return out
        rules = []
        if outer:
            reach = fx.outer_extent_px(layer, to_px)
            tolerance_px = max(0.5, reach / 8.0)
            for rule, tolerance in zoom_rules(tolerance_px):
                symbol = QgsLineSymbol([fx.with_effects(layer, fx.OUTER_EFFECTS)])
                symbol.setOpacity(opacity)
                derived = self._with_symbol(rule, symbol, 1, 1, mat.Recipe(
                    "simplified", params=(("tolerance", tolerance),)))
                derived.effect_role = "outer"
                rules.append(derived)
        spec = fx.inner_effect_spec(layer, width_px) if inner else None
        if spec is None:  # inner effects draw the whole line (their ends layer)
            symbol = QgsLineSymbol([fx.with_effects(layer, ())])
            symbol.setOpacity(opacity)
            main = self._with_symbol(flat_rule, symbol, 1, 2)
            main.effect_role = "none"
            rules.append(main)
        else:
            spec["opacity"] = opacity
            spec["cap"] = {Qt.PenCapStyle.FlatCap: "butt", Qt.PenCapStyle.SquareCap: "square"
                           }.get(layer.penCapStyle(), "round")
            # The direction is taken over one line width at the zoom (a line
            # zigzagging within its own width is shaded as QGIS shades it: as
            # a straight band).
            for rule, window in zoom_rules(width_px):
                symbol = QgsLineSymbol([fx.with_effects(layer, ())])
                strips = self._with_symbol(rule, symbol, 1, 3, mat.Recipe(
                    "direction_runs", params=(("buckets", spec["buckets"]), ("window", window))))
                strips.inner_effect = spec
                rules.append(strips)
        return rules

    # -- arrows --------------------------------------------------------------
    def _map_units_per_mm(self, flat_rule: Optional[FlattenedRule], zoom: float,
                          layer=None) -> float:
        """Project CRS map units per screen millimetre at ``zoom``."""
        from qgis.core import QgsCoordinateReferenceSystem, QgsUnitTypes  # pylint: disable=import-outside-toplevel
        layer = layer if layer is not None else flat_rule.layer
        crs = QgsCoordinateReferenceSystem(self.project_crs or layer.crs().authid())
        if crs.isGeographic():
            # QGIS's map units per pixel in degrees: the browser's Mercator
            # metres per mm over the Mercator x-extent of a degree (the
            # scale of a geographic CRS counts haversine metres instead).
            return WEB_MERCATOR_TOP_SCALE / 2.0 ** zoom / 1000.0 / MERCATOR_UNITS_PER_DEGREE
        metres = (ZoomLevels.zoom_to_scale(0) or 0.0) / 2.0 ** zoom / 1000.0
        return metres * QgsUnitTypes.fromUnitToUnitFactor(Qgis.DistanceUnit.Meters, crs.mapUnits())

    # Arrow sizes in screen units: polygons per eighth of a zoom (sizes
    # within +-4.5 % of QGIS's), fewer bands when over the output budget.
    ARROW_BAND_STEPS = (8, 4, 2, 1)

    def _arrow(self, flat_rule: FlattenedRule, layer) -> List[FlattenedRule]:
        """A QGIS arrow as the polygons QGIS fills (fidelity/arrows.py, built
        by the exporter in painter pixels at the band's zoom), drawn with
        every layer of the arrow's fill symbol; a fill offset (drop shadow)
        shifts its copy on screen."""
        from qgis.core import QgsFillSymbol  # pylint: disable=import-outside-toplevel
        fill = layer.subSymbol()
        if fill is None:
            return []
        fill_layers = [fill.symbolLayer(i) for i in range(fill.symbolLayerCount())
                       if fill.symbolLayer(i).enabled()]
        if not fill_layers:
            return []
        props = layer.dataDefinedProperties()
        if props is not None and props.hasActiveProperties():
            self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                         "Data-defined arrow properties use their static values.", flat_rule)
        values = [(layer.arrowStartWidth(), layer.arrowStartWidthUnit()),
                  (layer.arrowWidth(), layer.arrowWidthUnit()),
                  (layer.headLength(), layer.headLengthUnit()),
                  (layer.headThickness(), layer.headThicknessUnit()),
                  (layer.offset(), layer.offsetUnit())]
        screen = any(abs(v or 0.0) > 1e-12 and normalize_unit(u) not in ("map", "m")
                     for v, u in values)
        crs = self.project_crs or flat_rule.layer.crs().authid()
        base = (("crs", crs), ("curved", bool(layer.isCurved())),
                ("repeated", bool(layer.isRepeated())), ("head_type", _enum_value(layer.headType())),
                ("arrow_type", _enum_value(layer.arrowType())))

        # QGIS fills each arrow with every layer before the next arrow: with
        # opaque, outline-free simple fills each layer keeps its visible part.
        painter = len(fill_layers) > 1 and fill.opacity() >= 1.0 and \
            flat_rule.rule.symbol().opacity() >= 1.0 and all(
            l.layerType() == "SimpleFill" and l.strokeStyle() == Qt.PenStyle.NoPen
            and l.brushStyle() == Qt.BrushStyle.SolidPattern and l.color().alpha() == 255
            and not l.dataDefinedProperties().hasActiveProperties() for l in fill_layers)

        def to_map(value, unit, per_mm):
            if normalize_unit(unit) in ("map", "m"):
                return float(value or 0.0)
            return (_to_mm(value or 0.0, unit) or 0.0) * per_mm

        def recipe(zoom: float, copy: int):
            per_mm = self._map_units_per_mm(flat_rule, zoom)
            params = base + (("sizes", tuple(to_map(v, u, per_mm) for v, u in values)),
                             ("pixel", per_mm * 25.4 / 96.0))
            if painter:
                # Painter offsets are x right, y down; map y points up.
                shifts = tuple((to_map(l.offset().x(), l.offsetUnit(), per_mm),
                                -to_map(l.offset().y(), l.offsetUnit(), per_mm)) for l in fill_layers)
                params += (("painter", shifts), ("layer", copy))
            return mat.Recipe("arrow_polygons", params=params)

        low, high = self._zoom_range(flat_rule)
        native = []  # overzoom rules drawn as a line and head markers
        if screen and low <= high:
            zoom_rules = self._per_zoom(flat_rule, overzoom=True)
            over = [rule for rule in zoom_rules if rule.get_attr("v") is not None]
            zoom_rules = zoom_rules[:len(zoom_rules) - len(over)]
            per_set = self._layer_totals(flat_rule.layer)[2] * (10 if layer.isRepeated() else 1)
            # The exported zooms get the budget first, the last ones finest
            # (_zoom_steps); the zooms past the last tiles share what is left.
            steps = self._zoom_steps({rule.get_attr("o"): per_set for rule in zoom_rules},
                                     self.ARROW_BAND_STEPS, 1)
            used = sum(per_set * n for n in steps.values())
            top = steps[zoom_rules[-1].get_attr("o")] if zoom_rules else self.ARROW_BAND_STEPS[0]
            # Past the last tiles (on their data): polygons per zoom while the
            # body is at least two units of their geometry grid wide (4096
            # units a tile: 8 px at 6 zooms over; narrower polygons
            # collapse), within what is left of the output budget, at most
            # OVERZOOM_ARROW_STEPS bands a zoom. Then a line and head markers.
            quantum = self._map_units_per_mm(flat_rule, high) * 25.4 / 96.0 / 8.0
            kept = 0
            for rule in over:
                per_mm = self._map_units_per_mm(flat_rule, self.rule_zoom(rule) + 1.0)
                if max(to_map(v, u, per_mm) for v, u in values[:2]) < 2 * quantum:
                    break
                kept += 1
            over_steps = 0
            while kept and not over_steps:
                over_steps = next((n for n in self.ARROW_BAND_STEPS
                                   if n <= min(top, self.OVERZOOM_ARROW_STEPS) and
                                   used + per_set * kept * n <= self.MAX_PATTERN_ELEMENTS), 0)
                kept -= 0 if over_steps else 1
            native = over[kept:]  # _per_zoom closed the zooms before at their end
            bands = [band for index, rule in enumerate(zoom_rules)
                     for band in self._sub_zoom_bands(rule, steps[rule.get_attr("o")],
                                                      not over and index == len(zoom_rules) - 1)]
            bands += [band for rule in over[:kept]
                      for band in self._sub_zoom_bands(rule, over_steps, False)]
        else:
            # Map units: one dataset; arcs flattened at the finest zoom it is
            # viewed at (overzoom included).
            bands = [(flat_rule, max([low, high] + self._overzoom_zooms(flat_rule)) + 0.5)]

        rules = []
        for copy, fill_layer in enumerate(fill_layers):
            fill_layer = fill_layer.clone()
            translate = outline = None
            if fill_layer.layerType() == "SimpleFill":
                offset = fill_layer.offset()
                if abs(offset.x()) > 1e-9 or abs(offset.y()) > 1e-9:
                    translate = (offset.x(), offset.y(),
                                 QgsUnitTypes.encodeUnit(fill_layer.offsetUnit()))
                    if normalize_unit(fill_layer.offsetUnit()) in ("map", "m"):
                        translate += (fill_layer.offsetMapUnitScale(),)
                    fill_layer.setOffset(QPointF())
                if fill_layer.strokeStyle() != Qt.PenStyle.NoPen:
                    # QGIS strokes the arrow centred on its edge with the
                    # stroke's width, dashes and join: a line on the polygons'
                    # rings, above the fill (a fill-outline-color is one
                    # device pixel inside the edge whatever the width).
                    outline = QgsLineSymbol([self._fill_stroke_as_line(fill_layer)])
                    outline.setOpacity(fill.opacity())
                    inherit_opacity(flat_rule.rule.symbol(), outline)
                    fill_layer.setStrokeStyle(Qt.PenStyle.NoPen)
            symbol = QgsFillSymbol([fill_layer])
            symbol.setOpacity(fill.opacity())
            inherit_opacity(flat_rule.rule.symbol(), symbol)
            brush = outline is None or fill_layer.brushStyle() != Qt.BrushStyle.NoBrush
            for band_rule, zoom in bands:
                if brush:
                    derived = self._with_symbol(band_rule, symbol.clone(), 2, 1 + copy,
                                                recipe(zoom, copy))
                    derived.translate = translate
                    rules.append(derived)
                if outline is not None:
                    stroke = self._with_symbol(band_rule, outline.clone(), 1, 1 + copy,
                                               recipe(zoom, copy))
                    stroke.translate = translate  # shifted with its fill
                    rules.append(stroke)
            for rule in native:
                for index, derived in enumerate(self._native_arrow(
                        rule, layer, fill_layers[copy], fill.opacity(), base, values, to_map)):
                    derived.set_attr("m", 1 + len(fill_layers) + 4 * copy + index)
                    derived.translate = translate
                    rules.append(derived)
        if native:
            self._report("Q2VT_SYMBOL_APPROXIMATE",
                         "Arrows narrower than the last tiles' geometry grid (far past the last "
                         "tile zoom) are drawn as a line of their mean width with head markers.",
                         flat_rule)
        return rules

    # Arrow bands per zoom past the last tile zoom (+-9 % of the width).
    OVERZOOM_ARROW_STEPS = 4

    def _native_arrow(self, rule: FlattenedRule, layer, fill_layer, opacity: float, base,
                      values, to_map) -> List[FlattenedRule]:
        """An arrow at an overzoom (``rule``, one zoom on the last tiles) as
        native screen-size parts: its body's centre line (fidelity/arrows.py
        arrow_skeletons) drawn as a line of the body's mean width, and a
        triangle marker turned along the line at every head's tip. The body
        ends at the head's base at the end of the zoom (the head covers it
        before)."""
        from qgis.core import QgsEllipseSymbolLayer  # pylint: disable=import-outside-toplevel
        zoom = self.rule_zoom(rule)
        per_mm = self._map_units_per_mm(rule, zoom + 0.5)
        start, width, head_length, thickness, offset = (to_map(v, u, per_mm) for v, u in values)
        head_end = to_map(*values[2], self._map_units_per_mm(rule, zoom + 1.0))
        params = base + (("pixel", per_mm * 25.4 / 96.0),)
        grid = self._map_units_per_mm(rule, self._zoom_range(rule)[1]) * 25.4 / 96.0 / 8.0
        body_recipe = mat.Recipe("arrow_polygons", params=params + (
            ("sizes", (start, width, head_end, thickness, offset)), ("part", "body"),
            ("grid", grid)))
        head_recipe = mat.Recipe("arrow_polygons", params=params + (
            ("sizes", (start, width, head_length, thickness, offset)), ("part", "heads")))
        half = _enum_value(layer.arrowType()) != 0
        mm = lambda value: value / per_mm  # pylint: disable=unnecessary-lambda-assignment
        body_mm = mm(start + width) / (4.0 if half else 2.0)
        color = fill_layer.color()
        stroke = None
        if fill_layer.layerType() == "SimpleFill":
            if fill_layer.brushStyle() == Qt.BrushStyle.NoBrush:
                color = QColor(0, 0, 0, 0)
            if fill_layer.strokeStyle() != Qt.PenStyle.NoPen:
                stroke_mm = _to_mm(fill_layer.strokeWidth(), fill_layer.strokeWidthUnit())
                if stroke_mm is None:
                    stroke_mm = mm(fill_layer.strokeWidth())
                stroke = (fill_layer.strokeColor(), stroke_mm or 25.4 / 96.0)
        out = []
        # QGIS strokes the polygon's edge: the stroke's width centred on it.
        for line_color, line_mm in ([(stroke[0], body_mm + stroke[1])] if stroke else []) + [
                (color, body_mm - (stroke[1] if stroke else 0.0))]:
            if line_mm <= 0 or line_color.alpha() == 0:
                continue
            line = QgsSimpleLineSymbolLayer(line_color, line_mm, Qt.PenStyle.SolidLine)
            line.setWidthUnit(Qgis.RenderUnit.Millimeters)
            line.setPenCapStyle(Qt.PenCapStyle.FlatCap)
            line.setPenJoinStyle(Qt.PenJoinStyle.RoundJoin)
            symbol = QgsLineSymbol([line])
            symbol.setOpacity(opacity)
            out.append(self._with_symbol(rule, symbol, 1, 1, body_recipe))
        shapes = {0: QgsEllipseSymbolLayer.Shape.Triangle,
                  1: QgsEllipseSymbolLayer.Shape.RightHalfTriangle,
                  2: QgsEllipseSymbolLayer.Shape.LeftHalfTriangle}
        arrow_type = _enum_value(layer.arrowType())
        # QgsArrowSymbolLayer's right half (y down: the right of the travel)
        # is the ellipse marker's left half triangle (x >= 0); a head
        # pointing back has its filled half on the other side.
        for backward, kind in ([(None, arrow_type)] if not half else
                               [(False, arrow_type), (True, 3 - arrow_type)]):
            head = QgsEllipseSymbolLayer()
            head.setShape(shapes[kind])
            head.setSymbolWidth(2 * mm(thickness))
            head.setSymbolHeight(mm(head_length))
            head.setSymbolWidthUnit(Qgis.RenderUnit.Millimeters)
            head.setSymbolHeightUnit(Qgis.RenderUnit.Millimeters)
            head.setColor(color)
            if stroke:
                head.setStrokeColor(stroke[0])
                head.setStrokeWidth(stroke[1])
                head.setStrokeWidthUnit(Qgis.RenderUnit.Millimeters)
                head.setStrokeStyle(Qt.PenStyle.SolidLine)
            else:
                head.setStrokeStyle(Qt.PenStyle.NoPen)
            # Apex up (north) at angle 0, its centre half a head behind the tip.
            head.setOffset(QPointF(0.0, mm(head_length) / 2.0))
            head.setOffsetUnit(Qgis.RenderUnit.Millimeters)
            head.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyAngle, QgsProperty.fromExpression(
                mat.marker_rotation_expression(90.0)))
            symbol = QgsMarkerSymbol([head])
            symbol.setOpacity(opacity)
            recipe = head_recipe if backward is None else mat.Recipe(
                head_recipe.kind, params=head_recipe.params + (("backward", backward),))
            out.append(self._with_symbol(rule, symbol, 0, 1, recipe))
        return out

    @staticmethod
    def _fill_stroke_as_line(fill_layer) -> QgsSimpleLineSymbolLayer:
        """A simple fill's outline as a simple line (colour, width, style,
        join and their data-defined values)."""
        line = QgsSimpleLineSymbolLayer(fill_layer.strokeColor(), fill_layer.strokeWidth())
        line.setWidthUnit(fill_layer.strokeWidthUnit())
        line.setWidthMapUnitScale(fill_layer.strokeWidthMapUnitScale())
        line.setPenStyle(fill_layer.strokeStyle())
        line.setPenJoinStyle(fill_layer.penJoinStyle())
        props = fill_layer.dataDefinedProperties()
        for key in (QgsSymbolLayer.Property.PropertyStrokeColor,
                    QgsSymbolLayer.Property.PropertyStrokeWidth,
                    QgsSymbolLayer.Property.PropertyStrokeStyle,
                    QgsSymbolLayer.Property.PropertyJoinStyle):
            prop = props.property(key)
            if prop is not None and prop.isActive():
                line.setDataDefinedProperty(key, QgsProperty(prop))
        return line

    # -- filled lines ----------------------------------------------------------
    def _filled_line(self, flat_rule: FlattenedRule, layer) -> List[FlattenedRule]:
        fill = layer.subSymbol()
        fill_layer = fill.symbolLayer(0) if fill and fill.symbolLayerCount() else None
        if fill_layer is None or fill_layer.layerType() != "SimpleFill":
            self._report("Q2VT_UNSUPPORTED_SYMBOL_LAYER",
                         "Filled line with a non-simple fill is drawn with its first colour.",
                         flat_rule)
        line = QgsSimpleLineSymbolLayer(fill_layer.color() if fill_layer else None, layer.width())
        line.setWidthUnit(layer.widthUnit())
        for getter, setter in (("penCapStyle", "setPenCapStyle"),
                               ("penJoinStyle", "setPenJoinStyle")):
            if hasattr(layer, getter):
                getattr(line, setter)(getattr(layer, getter)())
        symbol = QgsLineSymbol([line])
        symbol.setOpacity(fill.opacity() if fill else 1.0)
        return [self._with_symbol(flat_rule, symbol, 1, 1)]

    def _interpolated_line(self, flat_rule: FlattenedRule, layer) -> List[FlattenedRule]:
        """An interpolated line (colour and width varying along each line
        between per-feature start and end values) as short pieces with their
        own colour and width (mat.interpolated_line_recipe), drawn by a
        simple line reading both from the piece."""
        from qgis.core import QgsReadWriteContext  # pylint: disable=import-outside-toplevel
        from qgis.PyQt.QtXml import QDomDocument  # pylint: disable=import-outside-toplevel

        def xml(item):
            doc = QDomDocument()
            doc.appendChild(item.writeXml(doc, QgsReadWriteContext()))
            return doc.toString()
        recipe = mat.interpolated_line_recipe(
            xml(layer.interpolatedColor()), xml(layer.interpolatedWidth()),
            (layer.startValueExpressionForColor(), layer.endValueExpressionForColor()),
            (layer.startValueExpressionForWidth(), layer.endValueExpressionForWidth()))
        line = QgsSimpleLineSymbolLayer(QColor("black"), 1.0)
        line.setWidthUnit(layer.widthUnit())
        line.setWidthMapUnitScale(layer.widthMapUnitScale())
        line.setOffset(layer.offset())
        line.setOffsetUnit(layer.offsetUnit())
        # Round ends join the pieces without gaps at bends (QGIS joins them too).
        line.setPenCapStyle(Qt.PenCapStyle.RoundCap)
        line.setPenJoinStyle(Qt.PenJoinStyle.RoundJoin)
        line.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyStrokeColor,
                                    QgsProperty.fromField(mat.COLOR_FIELD))
        line.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyStrokeWidth,
                                    QgsProperty.fromField(mat.WIDTH_FIELD))
        symbol = QgsLineSymbol([line])
        symbol.setOpacity(flat_rule.rule.symbol().opacity())
        return [self._with_symbol(flat_rule, symbol, 1, 1, recipe)]

    # -- map-unit hatches ------------------------------------------------------
    # -- gradient / shapeburst fills: colour bands (fidelity/materialize.py) --------------
    # Smooth as QGIS's 8-bit gradient: neighbouring bands differ by about one
    # colour level (64 bands of ~4 levels showed as stripes on a rainbow).
    # The exporter merges bands narrower than a pixel per feature.
    MAX_BANDS = 1024
    BAND_LEVELS = 1.0

    @classmethod
    def _band_count(cls, colors) -> int:
        """Bands so neighbours differ by ~BAND_LEVELS levels (at most MAX_BANDS)."""
        spread = 0
        for a, b in zip(colors, colors[1:]):
            spread += max(abs(a.red() - b.red()), abs(a.green() - b.green()),
                          abs(a.blue() - b.blue()), abs(a.alpha() - b.alpha()))
        return max(2, min(cls.MAX_BANDS, math.ceil(spread / cls.BAND_LEVELS)))

    @staticmethod
    def _ramp_of(layer):
        """The fill's colours as a ramp: its colour ramp, or color -> color2."""
        from qgis.core import QgsGradientColorRamp  # pylint: disable=import-outside-toplevel
        two_colors = _enum_value(layer.gradientColorType() if hasattr(layer, "gradientColorType")
                                 else layer.colorType()) == 0
        if not two_colors and layer.colorRamp() is not None:
            return layer.colorRamp()
        return QgsGradientColorRamp(layer.color(), layer.color2())

    @staticmethod
    def _gradient_color(ramp):
        """``t -> QColor`` of a gradient fill: QGIS hands the ramp's stops to
        a QGradient (QgsGradientColorRamp::addStopsToGradient), which Qt
        interpolates premultiplied; ``ramp.color`` blends a translucent end
        unpremultiplied (blue to transparent white turns grey halfway)."""
        from qgis.core import QgsGradientColorRamp  # pylint: disable=import-outside-toplevel
        if not isinstance(ramp, QgsGradientColorRamp) or ramp.isDiscrete():
            return ramp.color
        stops = {0.0: ramp.color1(), 1.0: ramp.color2()}
        last = 0.0
        for stop in ramp.stops():
            stops[stop.offset] = QColor(stop.color)
            if stop.colorSpec() != QColor.Spec.Rgb:  # extra stops, as QGIS adds them
                offset = last + 0.05
                while offset < stop.offset:
                    stops[offset] = ramp.color(offset)
                    offset += 0.05
            last = stop.offset
        if ramp.colorSpec() != QColor.Spec.Rgb:
            offset = last + 0.05
            while offset < 1:
                stops[offset] = ramp.color(offset)
                offset += 0.05
        offsets = sorted(stops)
        premultiplied = [(c.alphaF(), c.redF() * c.alphaF(), c.greenF() * c.alphaF(),
                          c.blueF() * c.alphaF()) for c in (stops[o] for o in offsets)]

        def color(t):
            index = max(0, min(len(offsets) - 2, bisect.bisect_right(offsets, t) - 1))
            t0, t1 = offsets[index], offsets[index + 1]
            f = min(1.0, max(0.0, (t - t0) / (t1 - t0) if t1 > t0 else 0.0))
            a, r, g, b = (p + (q - p) * f for p, q in zip(premultiplied[index], premultiplied[index + 1]))
            if a <= 0:
                return QColor(0, 0, 0, 0)
            return QColor.fromRgbF(min(1.0, r / a), min(1.0, g / a), min(1.0, b / a), a)
        return color

    def _band_rules(self, flat_rule, layer, ramp, recipe_of, overlap_ok=True, color_of=None):
        """One rule drawing every colour band of the fill: a solid fill whose
        colour is the band's (COLOR_FIELD), from one materialized dataset.
        ``color_of``: ``t -> QColor`` (default ``ramp.color``)."""
        from qgis.core import QgsFillSymbol, QgsSimpleFillSymbolLayer  # pylint: disable=import-outside-toplevel
        color_of = color_of or ramp.color
        probe = [color_of(i / 64) for i in range(65)]
        bands = self._band_count(probe)
        colors = [color_of((i + 0.5) / bands) for i in range(bands)]
        # Nested bands only where every pixel ends up opaque: below 100 %
        # symbol or layer opacity they would show through each other.
        parent = flat_rule.rule.symbol()
        opacity = parent.dataDefinedProperties().property(_SYMBOL_OPACITY_KEY)
        overlap = overlap_ok and all(c.alpha() == 255 for c in colors) \
            and parent.opacity() >= 1.0 and flat_rule.layer.opacity() >= 1.0 \
            and not (opacity is not None and opacity.isActive())
        encoded = [QgsSymbolLayerUtils.encodeColor(c) for c in colors]  # "r,g,b,a", as QGIS reads it
        fill = QgsSimpleFillSymbolLayer(colors[0], Qt.BrushStyle.SolidPattern, colors[0],
                                        Qt.PenStyle.NoPen)
        fill.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyFillColor,
                                    QgsProperty.fromField(mat.COLOR_FIELD))
        fill.setOffset(layer.offset())
        fill.setOffsetUnit(layer.offsetUnit())
        symbol = inherit_opacity(parent, QgsFillSymbol([fill]))
        recipe = mat.color_bands_recipe([recipe_of(band, bands, overlap) for band in range(bands)], encoded)
        return [self._with_symbol(flat_rule, symbol, 2, 1, recipe)]

    def _gradient(self, flat_rule: FlattenedRule, layer) -> List[FlattenedRule]:
        """A gradient fill as solid colour bands (linear strips, radial rings,
        conical wedges) computed per feature from its bounding box."""
        gradient_type = _enum_value(layer.gradientType())
        spread = _enum_value(layer.gradientSpread())
        if _enum_value(layer.coordinateMode()) != 0:
            self._report("Q2VT_GRADIENT_APPROXIMATE",
                         "Viewport-relative gradient is drawn relative to each feature.", flat_rule)
        if gradient_type == 2 and spread:
            self._report("Q2VT_GRADIENT_APPROXIMATE",
                         "Conical gradients have no spread; drawn once around the centre.", flat_rule)
        crs = self.project_crs or flat_rule.layer.crs().authid()
        p1, p2 = layer.referencePoint1(), layer.referencePoint2()

        def recipe(band, bands, opaque):
            # Opaque bands nest (each reaches to the end of its period, under
            # the bands drawn after it): thin anti-aliased bands that only
            # touch let the background through (lighter colours).
            overlap = opaque and gradient_type != 2
            return mat.gradient_band_recipe(
                gradient_type, (p1.x(), p1.y()), (p2.x(), p2.y()),
                layer.referencePoint1IsCentroid(), layer.referencePoint2IsCentroid(),
                layer.angle(), spread, band, bands, overlap, crs,
                0.5 if opaque and not overlap else 0.0)
        ramp = self._ramp_of(layer)
        return self._band_rules(flat_rule, layer, ramp, recipe, color_of=self._gradient_color(ramp))

    # Shapeburst distances in screen units: band sets per quarter of a zoom
    # (within +-9 % of QGIS's width), fewer when over the output budget.
    SHAPEBURST_ZOOM_STEPS = (4, 2, 1)

    def _shapeburst(self, flat_rule: FlattenedRule, layer) -> List[FlattenedRule]:
        """A shapeburst fill as inset bands from the edge (colour 1) inwards.
        A distance in screen units keeps its width on screen at every scale
        in QGIS: one band set per zoom step, the distance in map units at the
        middle of the step (96 dpi CSS pixels, the project CRS's units)."""
        whole = layer.useWholeShape()
        if layer.blurRadius():
            self._report("Q2VT_GRADIENT_APPROXIMATE",
                         "Shapeburst blur is not applied (bands are already smooth steps).", flat_rule)
        crs = self.project_crs or flat_rule.layer.crs().authid()
        ramp = self._ramp_of(layer)

        def bands(rule, distance):
            def recipe(band, count, overlap):
                return mat.shapeburst_band_recipe(distance, whole, layer.ignoreRings(), band, count, crs,
                                                  overlap)
            return self._band_rules(rule, layer, ramp, recipe)

        if whole:
            return bands(flat_rule, 0.0)
        distance = layer.maxDistance()
        mm = _to_mm(distance, layer.distanceUnit())
        if mm is None:  # map units: one band set
            return bands(flat_rule, distance) if distance and distance > 0 else []
        if mm <= 0:
            return []
        if flat_rule.get_attr("o") is None or flat_rule.get_attr("i") is None:
            self._report("Q2VT_GRADIENT_APPROXIMATE",
                         "Shapeburst distance in screen units is fixed at zoom 16.", flat_rule)
            return bands(flat_rule, mm * self._map_units_per_mm(flat_rule, 16.0))
        count = self._band_count([ramp.color(i / 64) for i in range(65)])
        radii = self._layer_radii(flat_rule.layer)

        def cost(zoom):
            """Bands of the layer at ``zoom``: insets deeper than a feature
            are empty (a feature a few pixels wide has one band)."""
            width = mm * self._map_units_per_mm(flat_rule, zoom + 1.0) / count
            return sum(min(count, int(radius / width) + 1) for radius in radii)
        # Every zoom the map is viewed at, overzoom included: the exported
        # zooms get the budget first, the last ones finest (_zoom_steps); the
        # zooms past the last tiles share what is left.
        zoom_rules = self._per_zoom(flat_rule, overzoom=True)
        over = [rule for rule in zoom_rules if rule.get_attr("v") is not None]
        zoom_rules = zoom_rules[:len(zoom_rules) - len(over)]
        steps = self._zoom_steps({rule.get_attr("o"): cost(rule.get_attr("o")) for rule in zoom_rules},
                                 self.SHAPEBURST_ZOOM_STEPS, 1)
        used = sum(cost(zoom) * n for zoom, n in steps.items())
        top = steps[zoom_rules[-1].get_attr("o")] if zoom_rules else self.SHAPEBURST_ZOOM_STEPS[0]
        coarse = sorted((zoom for zoom, n in steps.items() if n < self.SHAPEBURST_ZOOM_STEPS[0]),
                        reverse=True)
        if coarse:
            parts = []
            for n, label, within in ((2, "half zoom", 19), (1, "zoom", 41)):
                zooms = [zoom for zoom in coarse if steps[zoom] == n]
                if zooms:
                    parts.append(f"per {label} at zooms {zooms[-1]}-{zooms[0]} "
                                 f"(width within about {within} %)")
            self._report("Q2VT_GRADIENT_APPROXIMATE",
                         "Shapeburst distance in screen units is set {} to stay within the "
                         "output size.".format(", ".join(parts)), flat_rule)
        # Past the last tiles (on their data): band sets per half zoom while
        # the distance is at least a unit of their geometry grid (4096 units
        # a tile), within what is left of the output budget. Narrower, the
        # bands would collapse onto the outline: the inner colour from there.
        quantum = self._map_units_per_mm(flat_rule, self._zoom_range(flat_rule)[1]) * 25.4 / 96.0 / 8.0
        kept = 0
        for rule in over:
            if mm * self._map_units_per_mm(flat_rule, self.rule_zoom(rule) + 0.5) < quantum:
                break
            kept += 1
        over_steps = 0
        while kept and not over_steps:
            over_cost = sum(cost(self.rule_zoom(rule)) for rule in over[:kept])
            over_steps = next((n for n in self.SHAPEBURST_ZOOM_STEPS
                               if n <= min(top, 2) and
                               used + over_cost * n <= self.MAX_PATTERN_ELEMENTS), 0)
            kept -= 0 if over_steps else 1
        rules = []
        for index, rule in enumerate(zoom_rules):
            for band_rule, zoom in self._sub_zoom_bands(
                    rule, steps[rule.get_attr("o")], not over and index == len(zoom_rules) - 1):
                rules.extend(bands(band_rule, mm * self._map_units_per_mm(flat_rule, zoom)))
        for rule in over[:kept]:
            for band_rule, zoom in self._sub_zoom_bands(rule, over_steps, False):
                rules.extend(bands(band_rule, mm * self._map_units_per_mm(flat_rule, zoom)))
        if kept < len(over):
            from qgis.core import QgsFillSymbol, QgsSimpleFillSymbolLayer  # pylint: disable=import-outside-toplevel
            inner = over[kept].derive()
            inner.visibility = flat_rule.visibility.intersect(
                ZoomInterval(over[kept].visibility.min_zoom, None))
            fill = QgsSimpleFillSymbolLayer(ramp.color(1.0), Qt.BrushStyle.SolidPattern,
                                            ramp.color(1.0), Qt.PenStyle.NoPen)
            fill.setOffset(layer.offset())
            fill.setOffsetUnit(layer.offsetUnit())
            symbol = QgsFillSymbol([fill])
            symbol.setOpacity(flat_rule.rule.symbol().opacity())
            rules.append(self._with_symbol(inner, symbol, 2, 1))
            self._report("Q2VT_GRADIENT_APPROXIMATE",
                         "Shapeburst bands narrower than the last tiles' geometry grid (far past "
                         "the last tile zoom) are drawn in the inner colour.", flat_rule)
        return rules

    def _hatch(self, flat_rule: FlattenedRule, layer) -> List[FlattenedRule]:
        sub = layer.subSymbol()
        if sub is None:
            return []
        offset = layer.offset()
        if offset and normalize_unit(layer.offsetUnit()) not in ("map", "m"):
            self._report("Q2VT_PATTERN_APPROXIMATE",
                         "Hatch offset in screen units is ignored for map-unit hatches.",
                         flat_rule)
            offset = 0.0
        mus = layer.distanceMapUnitScale()
        if mus.minScale or mus.maxScale or mus.minSizeMMEnabled or mus.maxSizeMMEnabled:
            self._report("Q2VT_PATTERN_APPROXIMATE",
                         "Hatch spacing scale limits are ignored.", flat_rule)
        if layer.distance() <= 0:
            return None
        # Lines generated per feature (mat.hatch_expression): at most its
        # bounding box diagonal / spacing + 5. Past the budget the hatch is a
        # texture, never dropped (a feature past mat.MAX_HATCH_LINES would
        # get no hatch at all). Counted over the whole layer, also for a rule
        # drawing some of its features: on the safe side.
        _, _, count, diagonals = self._layer_totals(flat_rule.layer)
        if self._over_budget(flat_rule, diagonals / layer.distance() + 5 * count, "Hatch"):
            return None
        crs = self.project_crs or flat_rule.layer.crs().authid()
        recipe = mat.hatch_recipe(layer.lineAngle(), layer.distance(), offset, crs, "feature")
        symbol = sub.clone()
        # QgsLinePatternFillSymbolLayer: the pattern image takes the fill
        # symbol's opacity; hatches QGIS draws line by line (clipped other
        # than at the painter, data-defined line) keep the line's own.
        by_line = sub.hasDataDefinedProperties() or \
            layer.dataDefinedProperties().isActive(QgsSymbolLayer.Property.PropertyLineClipping) or \
            _enum_value(layer.clipMode()) != _enum_value(Qgis.LineClipMode.ClipPainterOnly)
        if not by_line:
            inherit_opacity(flat_rule.rule.symbol(), symbol)
        return [self._with_symbol(flat_rule, symbol, 1, 1, recipe)]
