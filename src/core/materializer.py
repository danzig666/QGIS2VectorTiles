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

import math
from typing import List, Optional

from qgis.core import (
    Qgis,
    QgsLineSymbol,
    QgsLineSymbolLayer,
    QgsMarkerLineSymbolLayer,
    QgsMarkerSymbol,
    QgsProject,
    QgsProperty,
    QgsSimpleLineSymbolLayer,
    QgsSimpleMarkerSymbolLayer,
    QgsSimpleMarkerSymbolLayerBase,
    QgsSymbolLayer,
)
from qgis.PyQt.QtCore import QPointF

from ..utils.config import Qt
from ..utils.flattened_rule import FlattenedRule
from ..utils.zoom_levels import ZoomLevels
from .fidelity.model import ZoomInterval
from .fidelity import materialize as mat
from .fidelity.diagnostics import DiagnosticCollector
from .fidelity.units import physical_factor, normalize_unit, MM


def _flag_names(flags) -> set:
    try:
        value = int(flags)
    except (TypeError, ValueError):
        value = getattr(flags, "value", 0)
    return {m.name for m in Qgis.MarkerLinePlacement if value & int(m)}


def _to_mm(value: float, unit) -> Optional[float]:
    """Physical length in mm, or None for map units / unknown units."""
    factor = physical_factor(normalize_unit(unit))
    if factor is None:
        return None
    return value * factor / physical_factor(MM)


def _enum_value(value) -> int:
    return int(getattr(value, "value", value))


def _ring_filter(layer) -> int:
    """QgsLineSymbolLayer ring filter: 0 all rings, 1 exterior, 2 interior."""
    try:
        return int(getattr(layer.ringFilter(), "value", layer.ringFilter()))
    except (AttributeError, TypeError, ValueError):
        return 0


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

    def __init__(self, diagnostics: DiagnosticCollector, max_zoom: int = 24):
        self.diagnostics = diagnostics
        self.max_zoom = max_zoom
        crs = QgsProject.instance().crs()
        self.project_crs = crs.authid() if crs.isValid() else ""

    # -- helpers -----------------------------------------------------------
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
            dashes = self._dash_segments(flat_rule, layer)
            if dashes is not None:
                return dashes
        if kind in ("SimpleLine", "MarkerLine", "HashLine") and flat_rule.get_attr("g") == 2 \
                and (abs(layer.offset()) > 1e-9 or _ring_filter(layer)):
            outline = self._polygon_outline_offset(flat_rule, layer)
            if outline is not None:
                return outline
        if kind == "HashLine":
            layer = self._hash_as_marker_line(layer, flat_rule)
            flat_rule.rule.symbol().changeSymbolLayer(0, layer)
            kind = "MarkerLine"
        if kind == "MarkerLine":
            return self._marker_line(flat_rule, layer)
        if kind == "ArrowLine":
            return self._arrow(flat_rule, layer)
        if kind == "FilledLine":
            return self._filled_line(flat_rule, layer)
        if kind == "LinePatternFill" and normalize_unit(layer.distanceUnit()) in ("map", "m"):
            return self._hatch(flat_rule, layer)
        if kind == "RandomMarkerFill":
            return self._random_fill(flat_rule, layer)
        if kind == "PointPatternFill" and normalize_unit(layer.distanceXUnit()) == "map" \
                and normalize_unit(layer.distanceYUnit()) == "map" \
                and not self._tiling_pattern(layer):
            return self._dense_split(flat_rule, min(layer.distanceX(), layer.distanceY()),
                                     lambda rule: self._point_grid(rule, layer))
        return None

    _SCALE_FOLDED = (QgsSymbolLayer.Property.PropertyInterval,
                     QgsSymbolLayer.Property.PropertyOffsetAlongLine)

    def _split_scale_dependent(self, flat_rule: FlattenedRule, layer):
        """Marker-line interval / offset along the line that depend on the
        map scale only (``CASE WHEN @map_scale > 3000 THEN 10 ELSE 3 END``):
        one rule per zoom, with the value at that zoom's scale, so the
        markers can still be placed exactly. None when not applicable."""
        from qgis.core import QgsExpression  # pylint: disable=import-outside-toplevel
        props = layer.dataDefinedProperties()
        scale_only = []
        for key in self._SCALE_FOLDED:
            prop = props.property(key)
            if prop is None or not prop.isActive():
                continue
            expression = QgsExpression(prop.asExpression())
            if expression.referencedColumns() - {""} or \
                    set(expression.referencedVariables()) - {"map_scale"}:
                return None  # feature-dependent: not placed exactly
            if "map_scale" in expression.referencedVariables():
                scale_only.append(key)
        low, high = flat_rule.get_attr("o"), min(flat_rule.get_attr("i"), self.max_zoom)
        if not scale_only or low >= high:
            if scale_only:
                for key in scale_only:
                    self._fold_constant_ddp(layer, key, self._setter(layer, key),
                                            ZoomLevels.zoom_to_scale(low))
            return None
        parts = []
        for zoom in range(low, high + 1):
            rule = flat_rule.derive()
            rule.set_attr("o", zoom)
            rule.set_attr("i", zoom)
            if flat_rule.visibility is not None:
                rule.visibility = flat_rule.visibility.intersect(
                    ZoomInterval(float(zoom), float(zoom + 1) if zoom < high else None))
            clone = rule.rule.symbol().symbolLayer(0)
            for key in scale_only:
                self._fold_constant_ddp(clone, key, self._setter(clone, key),
                                        ZoomLevels.zoom_to_scale(zoom))
            result = self.materialize(rule, clone)
            parts.extend(result if result is not None else [rule])
        return parts

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
        allowed = {"map_scale"} if scale is not None else set()
        if original.hasParserError() or original.referencedColumns() - {""} or \
                set(original.referencedVariables()) - allowed:
            return
        if scale is not None and "@map_scale" in text:
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
                split = self._dense_split(flat_rule, layer.patternWidth(),
                                          lambda rule: self._svg_grid(rule, layer))
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
        orientation. Map-unit offsets are materialized exactly; screen-unit
        offsets keep a native offset on counter-clockwise rings, where
        MapLibre's right-hand side is the interior. Placed markers (vertex,
        centre...) are handled by the marker-line materialization."""
        if layer.layerType() != "SimpleLine":
            placements = _flag_names(layer.placements()) if hasattr(layer, "placements") else set()
            if placements & mat.POINT_PLACEMENTS:
                return None
            if layer.layerType() == "MarkerLine" and self._exact_interval(layer):
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
            params.append(("ccw", True))
        rule.recipe = mat.Recipe("polygon_offset", params=tuple(params))
        rule.set_attr("m", 1)
        if layer.layerType() == "HashLine":
            converted = self._hash_as_marker_line(clone, rule)
            rule.rule.symbol().changeSymbolLayer(0, converted)
        return [rule]

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
        if not pattern or min(pattern) < 0 or sum(pattern[0::2]) <= 0 or \
                any(v <= 0 for v in pattern[0::2]):
            return None
        dash_offset = 0.0
        if layer.dashPatternOffset():
            if normalize_unit(layer.dashPatternOffsetUnit()) != "map":
                return None
            dash_offset = float(layer.dashPatternOffset())
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
        return self._dense_split(flat_rule, period, dashes, min_px=self.DASH_MIN_PERIOD_PX)

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
        scale = (ZoomLevels.zoom_to_scale(low) or 0.0) / 2 ** (span / 2.0)
        return mm / 1000.0 * scale  # ground metres; map units are metres here

    # Interval markers along a line are few per tile even when close together.
    INTERVAL_MIN_SPACING_PX = 2.0

    def _dense_split(self, flat_rule: FlattenedRule, spacing: float, materialize,
                     min_px: Optional[float] = None):
        """Texture for the zooms where a map-unit grid is dense, materialized
        points for the zooms where its spacing is large on screen."""
        low, high = flat_rule.get_attr("o"), min(flat_rule.get_attr("i"), self.max_zoom)
        switch = None
        for zoom in range(low, high + 1):
            px = spacing * 96.0 / (0.0254 * ZoomLevels.zoom_to_scale(zoom))
            if px >= (self.GRID_MIN_SPACING_PX if min_px is None else min_px):
                switch = zoom
                break
        if switch == low:
            return materialize(flat_rule)
        texture = flat_rule.derive()
        if switch is None:
            return None  # dense at every exported zoom: texture only
        texture.set_attr("i", switch - 1)
        grid = flat_rule.derive()
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
            return [self._with_symbol(rule, marker.clone(), 0, 1, recipe)]
        if not density:
            return points(flat_rule)
        return self._dense_split(flat_rule, math.sqrt(density / count), points)

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

    def _map_units(self, value: float, unit, what: str, flat_rule) -> float:
        if not value:
            return 0.0
        if normalize_unit(unit) == "map":
            return value
        self._report("Q2VT_PATTERN_APPROXIMATE",
                     f"Pattern {what} in screen units is ignored for a map-unit grid.", flat_rule)
        return 0.0

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
            self._map_units(layer.displacementX(), layer.displacementXUnit(), "displacement", flat_rule),
            self._map_units(layer.displacementY(), layer.displacementYUnit(), "displacement", flat_rule),
            self._map_units(layer.offsetX(), layer.offsetXUnit(), "offset", flat_rule),
            self._map_units(layer.offsetY(), layer.offsetYUnit(), "offset", flat_rule),
            self.project_crs or flat_rule.layer.crs().authid(), self._anchor(layer, flat_rule),
            inset, rows_from_top=clip != int(Qgis.MarkerClipMode.Shape),
            deviation=self._deviation(layer, flat_rule), seed=layer.seed())
        return [self._with_symbol(flat_rule, marker.clone(), 0, 1, recipe)]

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
        if active:
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
                self._map_units(layer.displacementX(), layer.displacementXUnit(), "displacement", flat_rule),
                self._map_units(layer.displacementY(), layer.displacementYUnit(), "displacement", flat_rule),
                self._map_units(layer.offsetX(), layer.offsetXUnit(), "offset", flat_rule),
                self._map_units(layer.offsetY(), layer.offsetYUnit(), "offset", flat_rule),
                crs, self._anchor(layer, flat_rule), 0.0, rows_from_top=False,
                clip_shape=True, clip_mode="shape", deviation=self._deviation(layer, flat_rule),
                seed=layer.seed(), paths=paths, fill=fill)
        parts = []
        if simple.color().alpha() > 0:
            fill = QgsSimpleFillSymbolLayer(simple.color())
            fill.setStrokeStyle(Qt.PenStyle.NoPen)
            symbol = QgsFillSymbol([fill])
            symbol.setOpacity(marker.opacity())
            parts.append(self._with_symbol(flat_rule, symbol, 2, 1, recipe(True)))
        if simple.strokeStyle() != Qt.PenStyle.NoPen and simple.strokeColor().alpha() > 0:
            stroke = QgsSimpleLineSymbolLayer(simple.strokeColor(), simple.strokeWidth())
            stroke.setWidthUnit(simple.strokeWidthUnit())
            stroke.setWidthMapUnitScale(simple.strokeWidthMapUnitScale())
            stroke.setPenStyle(simple.strokeStyle())
            stroke.setPenJoinStyle(simple.penJoinStyle())
            symbol = QgsLineSymbol([stroke])
            symbol.setOpacity(marker.opacity())
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
        stroke = QgsSimpleLineSymbolLayer(simple.strokeColor(), simple.strokeWidth())
        stroke.setWidthUnit(simple.strokeWidthUnit())
        stroke.setWidthMapUnitScale(simple.strokeWidthMapUnitScale())
        stroke.setPenStyle(simple.strokeStyle())
        stroke.setPenCapStyle(simple.penCapStyle())
        stroke.setPenJoinStyle(simple.penJoinStyle())
        symbol = QgsLineSymbol([stroke])
        symbol.setOpacity(marker.opacity())
        recipe = mat.grid_recipe(
            layer.distanceX(), layer.distanceY(),
            self._map_units(layer.displacementX(), layer.displacementXUnit(), "displacement", flat_rule),
            self._map_units(layer.displacementY(), layer.displacementYUnit(), "displacement", flat_rule),
            self._map_units(layer.offsetX(), layer.offsetXUnit(), "offset", flat_rule),
            self._map_units(layer.offsetY(), layer.offsetYUnit(), "offset", flat_rule),
            self.project_crs or flat_rule.layer.crs().authid(), self._anchor(layer, flat_rule),
            0.0, rows_from_top=clip != int(Qgis.MarkerClipMode.Shape), segments=segments,
            clip_shape=clip == int(Qgis.MarkerClipMode.Shape), clip_mode={
                int(Qgis.MarkerClipMode.Shape): "shape",
                int(Qgis.MarkerClipMode.CentroidWithin): "centroid",
                int(Qgis.MarkerClipMode.CompletelyWithin): "within",
                int(Qgis.MarkerClipMode.NoClipping): "none"}.get(clip, "shape"),
            deviation=self._deviation(layer, flat_rule), seed=layer.seed())
        return [self._with_symbol(flat_rule, symbol, 1, 1, recipe)]

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
        recipe = mat.grid_recipe(width, height, 0.0, 0.0, width / 2.0, -height / 2.0,
                                 self.project_crs or flat_rule.layer.crs().authid(),
                                 self._anchor(layer, flat_rule))
        return [self._with_symbol(flat_rule, QgsMarkerSymbol([marker]), 0, 1, recipe)]

    # -- marker lines --------------------------------------------------------
    def _marker_points_symbol(self, sub_symbol: QgsMarkerSymbol, rotate: bool,
                              extra_angle: float, offset: float, offset_unit,
                              flat_rule: FlattenedRule) -> QgsMarkerSymbol:
        symbol = sub_symbol.clone()
        for index in range(symbol.symbolLayerCount()):
            marker = symbol.symbolLayer(index)
            if rotate:
                marker.setDataDefinedProperty(
                    QgsSymbolLayer.Property.PropertyAngle,
                    QgsProperty.fromExpression(
                        mat.marker_rotation_expression(marker.angle() + extra_angle)))
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
                current = marker.offset()
                marker.setOffset(QPointF(current.x(), current.y() + delta))
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

    def _marker_line(self, flat_rule: FlattenedRule, layer) -> Optional[List[FlattenedRule]]:
        placements = _flag_names(layer.placements())
        points = placements & mat.POINT_PLACEMENTS
        exact_interval = self._exact_interval(layer)
        if not points and not exact_interval:
            return None  # screen-unit interval only: native repeated symbol
        if "CurvePoint" in points:
            self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                         "Curve-point markers are placed on every vertex.", flat_rule)
        sub = layer.subSymbol()
        if sub is None:
            return []
        offset, offset_unit = layer.offset(), layer.offsetUnit()
        crs = self.project_crs or flat_rule.layer.crs().authid()
        # QGIS offsets the line (polygons: every ring, as a buffer) before
        # measuring positions along it; vertex ends of open lines are the only
        # positions where offsetting the markers instead is equivalent.
        needs_offset_line = (points | ({"Interval"} if exact_interval else set())) \
            - {"FirstVertex", "LastVertex"}
        if flat_rule.get_attr("g") == 2:
            needs_offset_line = points | ({"Interval"} if exact_interval else set())
        line_offset = 0.0
        if offset and needs_offset_line:
            if normalize_unit(offset_unit) == "map":
                line_offset, offset = offset, 0.0
            else:
                self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                             "Screen-unit offset: markers are offset from the original line; "
                             "QGIS measures positions on the offset line.", flat_rule)
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
            recipe = mat.marker_points(points, offset=line_offset, crs=crs)
            if _ring_filter(layer):
                recipe = mat.Recipe(recipe.kind, recipe.placements, recipe.params + (
                    ("ring_filter", _ring_filter(layer)), ("crs", crs)))
            rules.append(self._with_symbol(flat_rule, symbol, 0, 1, recipe))
        if "Interval" in placements:
            native = flat_rule.derive()
            interval_layer = layer.clone()
            interval_layer.setPlacements(Qgis.MarkerLinePlacement.Interval)
            native.rule.symbol().changeSymbolLayer(0, interval_layer)
            if exact_interval:
                recipe = mat.interval_points(layer.interval(), float(layer.offsetAlongLine()),
                                             line_offset, crs)
                if _ring_filter(layer):
                    recipe = mat.Recipe(recipe.kind, recipe.placements,
                                        recipe.params + (("ring_filter", _ring_filter(layer)),))
                def exact(rule, recipe=recipe):
                    average = self._average_angle_length(layer, rule)
                    if average:
                        recipe = mat.Recipe(recipe.kind, recipe.placements,
                                            recipe.params + (("average", average),))
                    return [self._with_symbol(rule, symbol.clone(), 0, 2, recipe)]
                split = self._dense_split(native, layer.interval(), exact,
                                          min_px=self.INTERVAL_MIN_SPACING_PX)
                rules.extend(split if split is not None else [native])
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
        else:
            self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                         "Hash sub-symbol is not a simple line; drawn as a thin line.", flat_rule)
        marker_symbol = QgsMarkerSymbol([marker])
        if hash_symbol is not None:
            marker_symbol.setOpacity(hash_symbol.opacity())
        converted = QgsMarkerLineSymbolLayer(layer.rotateSymbols(), layer.interval())
        converted.setPlacements(layer.placements())
        converted.setIntervalUnit(layer.intervalUnit())
        converted.setOffset(layer.offset())
        converted.setOffsetUnit(layer.offsetUnit())
        converted.setOffsetAlongLine(layer.offsetAlongLine())
        converted.setOffsetAlongLineUnit(layer.offsetAlongLineUnit())
        converted.setSubSymbol(marker_symbol)
        return converted

    # -- arrows --------------------------------------------------------------
    def _arrow_head_units(self, layer, flat_rule):
        """(unit, body width, head width, head length) in one common unit, or
        None when map units and screen units are mixed."""
        values = [(layer.arrowWidth(), layer.arrowWidthUnit()),
                  (layer.arrowStartWidth(), layer.arrowStartWidthUnit()),
                  (layer.headThickness(), layer.headThicknessUnit()),
                  (layer.headLength(), layer.headLengthUnit())]
        if all(normalize_unit(unit) == "map" for _, unit in values):
            width, start, thick, length = (v for v, _ in values)
            return Qgis.RenderUnit.MapUnits, (width + start) / 2.0, max(width, start) + 2 * thick, length
        mm = [_to_mm(v, unit) for v, unit in values]
        if any(v is None for v in mm):
            self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                         "Arrow sizes mixing map units and screen units: heads are omitted.",
                         flat_rule)
            return None
        width, start, thick, length = mm
        return Qgis.RenderUnit.Millimeters, (width + start) / 2.0, max(width, start) + 2 * thick, length

    def _arrow(self, flat_rule: FlattenedRule, layer) -> List[FlattenedRule]:
        """Arrow body (straight, per segment or circular arcs, like QGIS) as a
        line, heads as triangles of the QGIS head length and width at the
        ends of every arrow."""
        from qgis.core import QgsEllipseSymbolLayer  # pylint: disable=import-outside-toplevel
        fill = layer.subSymbol()
        fill_layer = fill.symbolLayer(0) if fill and fill.symbolLayerCount() else None
        color = fill_layer.color() if fill_layer is not None else None
        if fill_layer is None or fill_layer.layerType() != "SimpleFill" or fill.symbolLayerCount() > 1:
            self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                         "Arrow fill is drawn with its first layer's colour only.", flat_rule)
        if int(layer.arrowType()) != 0 or layer.arrowStartWidth() != layer.arrowWidth():
            self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                         "Half or tapered arrows are drawn with a constant-width body and "
                         "full heads.", flat_rule)
        curved, repeated = bool(layer.isCurved()), bool(layer.isRepeated())
        crs = self.project_crs or flat_rule.layer.crs().authid()
        head_type = int(layer.headType())  # 0 single, 1 reversed, 2 double
        sizes = self._arrow_head_units(layer, flat_rule)
        cut_start = cut_end = 0.0
        if sizes is not None and sizes[0] == Qgis.RenderUnit.MapUnits:
            cut_end = sizes[3] if head_type in (0, 2) else 0.0
            cut_start = sizes[3] if head_type in (1, 2) else 0.0

        width = sizes[1] if sizes is not None else layer.arrowWidth()
        body = QgsSimpleLineSymbolLayer(color, width)
        body.setWidthUnit(sizes[0] if sizes is not None else layer.arrowWidthUnit())
        body.setPenCapStyle(0x00)  # flat
        if width == 0 and fill_layer is not None and fill_layer.strokeStyle() == 0:
            body.setPenStyle(0)  # no body at all (QGIS: empty polygon, no outline)
        body_symbol = QgsLineSymbol([body])
        body_symbol.setOpacity(fill.opacity() if fill else 1.0)
        body_recipe = mat.Recipe("arrow_body", params=(
            ("arrow_curved", curved), ("arrow_repeated", repeated), ("crs", crs),
            ("cut_start", cut_start), ("cut_end", cut_end)))
        rules = [self._with_symbol(flat_rule, body_symbol, 1, 1, body_recipe)]
        if sizes is None:
            return rules

        unit, _, head_width, head_length = sizes
        if head_width <= 0 or head_length <= 0:
            return rules
        head = QgsEllipseSymbolLayer()
        head.setShape(QgsEllipseSymbolLayer.Shape.Triangle
                      if hasattr(QgsEllipseSymbolLayer, "Shape") else Qgis.MarkerShape.Triangle)
        head.setSymbolWidth(head_width)
        head.setSymbolHeight(head_length)
        head.setSymbolWidthUnit(unit)
        head.setSymbolHeightUnit(unit)
        head.setOffset(QPointF(0, head_length / 2.0))  # tip on the line end
        head.setOffsetUnit(unit)
        head.setColor(color)
        head.setStrokeStyle(0)
        head_symbol = QgsMarkerSymbol([head])
        head_symbol.setOpacity(fill.opacity() if fill else 1.0)
        # The triangle points up; +90 turns it along the line direction.
        ends = {0: [("LastVertex", 90.0)], 1: [("FirstVertex", 270.0)],
                2: [("LastVertex", 90.0), ("FirstVertex", 270.0)]}.get(head_type, [])
        for index, (placement, extra) in enumerate(ends, start=2):
            symbol = self._marker_points_symbol(head_symbol, True, extra, 0.0, None, flat_rule)
            recipe = mat.Recipe("marker_points", (placement,), (
                ("arrow_curved", curved), ("arrow_repeated", repeated), ("crs", crs)))
            rules.append(self._with_symbol(flat_rule, symbol, 0, index, recipe))
        return rules

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

    # -- map-unit hatches ------------------------------------------------------
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
        anchor = "feature"
        try:
            if layer.coordinateReference() != Qgis.SymbolCoordinateReference.Feature:
                anchor = "world"
                self._report("Q2VT_PATTERN_APPROXIMATE",
                             "Viewport-anchored hatch is anchored to the map origin.",
                             flat_rule)
        except AttributeError:
            pass
        crs = self.project_crs or flat_rule.layer.crs().authid()
        recipe = mat.hatch_recipe(layer.lineAngle(), layer.distance(), offset, crs, anchor)
        return [self._with_symbol(flat_rule, sub.clone(), 1, 1, recipe)]
