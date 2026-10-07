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
    QgsSymbolLayerUtils,
    QgsUnitTypes,
)
from qgis.PyQt.QtCore import QPointF
from qgis.PyQt.QtGui import QColor

from ..utils.config import Qt
from ..utils.flattened_rule import FlattenedRule
from ..utils.zoom_levels import ZoomLevels
from .fidelity.model import ZoomInterval
from .fidelity import materialize as mat
from .fidelity.diagnostics import DiagnosticCollector
from .fidelity.units import physical_factor, normalize_unit, MM


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
        for rule in self._per_zoom(flat_rule):
            zoom = rule.get_attr("o")
            clone = rule.rule.symbol().symbolLayer(0)
            for key in scale_only:
                self._fold_constant_ddp(clone, key, self._setter(clone, key),
                                        ZoomLevels.zoom_to_scale(zoom))
            result = self.materialize(rule, clone)
            parts.extend(result if result is not None else [rule])
        return parts

    def _per_zoom(self, flat_rule: FlattenedRule) -> List[FlattenedRule]:
        """One rule per zoom of ``flat_rule``; the last keeps its overzoom."""
        low, high = flat_rule.get_attr("o"), min(flat_rule.get_attr("i"), self.max_zoom)
        if low >= high:
            return [flat_rule]
        rules = []
        for zoom in range(low, high + 1):
            rule = flat_rule.derive()
            rule.set_attr("o", zoom)
            rule.set_attr("i", zoom)
            if flat_rule.visibility is not None:
                rule.visibility = flat_rule.visibility.intersect(
                    ZoomInterval(float(zoom), float(zoom + 1) if zoom < high else None))
            rules.append(rule)
        return rules

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
        orientation. Map-unit offsets are materialized exactly; screen-unit
        offsets keep a native offset on counter-clockwise rings, where
        MapLibre's right-hand side is the interior. Placed markers (vertex,
        centre...) are handled by the marker-line materialization."""
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
            params.append(("ccw", True))
        rule.recipe = mat.Recipe("polygon_offset", params=tuple(params))
        rule.set_attr("m", 1)
        if layer.layerType() == "HashLine":
            converted = self._hash_as_marker_line(clone, rule)
            replace_symbol_layer(rule.rule.symbol(), converted)
        return [rule]

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
        low, high = flat_rule.get_attr("o"), min(flat_rule.get_attr("i"), self.max_zoom)
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
        steps = next((n for n in (8, 4, 2, 1)
                      if vertices * len(zoom_rules) * n <= self.MAX_PATTERN_ELEMENTS), 0)
        if not steps:
            self._report("Q2VT_PATTERN_BUDGET",
                         "Line offset curves would exceed the output budget; drawn with the "
                         "browser's line offset.", flat_rule)
            return None
        crs = self.project_crs or flat_rule.layer.crs().authid()
        offset_mm = _to_mm(layer.offset(), layer.offsetUnit())
        rules = []
        for rule in zoom_rules:
            start = float(rule.get_attr("o"))
            for band, zoom in self._sub_zoom_bands(rule, steps, False):
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
                                 what="Dash pattern")

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

    # Budget of generated pattern features per rule (markers, dashes, line
    # work): past it the pattern is a texture at every zoom (reported), so
    # an export never stalls or drops a pattern silently on large layers.
    MAX_PATTERN_ELEMENTS = 2_000_000

    def _layer_totals(self, layer):
        """``(area, length, features)`` of a source layer in project map units
        (cached): the basis of pattern size estimates."""
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
        area = length = 0.0
        count = 0
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
        cache[key] = (area, length, count)
        return cache[key]

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
                     what: str = "Pattern"):
        """Texture for the zooms where a map-unit grid is dense, materialized
        points for the zooms where its spacing is large on screen. ``elements``
        estimates the features the materialized part would create."""
        if self._over_budget(flat_rule, elements, what):
            return None
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
        low, high = native.get_attr("o"), min(native.get_attr("i"), self.max_zoom)
        if low > high:
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
            return _to_mm(value, unit) / 1000.0 * (ZoomLevels.zoom_to_scale(0) or 0.0) / 2.0 ** zoom

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
                rules.append(over)
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

    @staticmethod
    def _sub_zoom_bands(rule: FlattenedRule, steps: int, last: bool):
        """[(rule, middle zoom)]: ``rule`` (one zoom) split into ``steps``
        equal zoom bands (attribute "b"); the last band of an open-ended last
        zoom stays visible when overzooming."""
        low = float(rule.get_attr("o"))
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
            return zoom_offset_mm / 1000.0 * \
                ZoomLevels.zoom_to_scale(rule.get_attr("o")) / math.sqrt(2)

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
                rules.extend(placed(rule, zoom_offset(rule)) for rule in self._per_zoom(flat_rule))
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
                    return [one(zoom_rule) for zoom_rule in self._per_zoom(rule)]
                split = self._dense_split(native, layer.interval(), exact,
                                          elements=self._layer_totals(flat_rule.layer)[1] /
                                          max(layer.interval(), 1e-9),
                                          what="Marker line",
                                          min_px=self.INTERVAL_MIN_SPACING_PX)
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
        rules = []
        if outer:
            reach = fx.outer_extent_px(layer, to_px)
            tolerance_px = max(0.5, reach / 8.0)
            world = 2 * math.pi * 6378137.0
            zoom_rules = self._per_zoom(flat_rule)
            for rule in zoom_rules:
                zoom = rule.get_attr("o") + 0.5
                # EPSG:3857 metres per MapLibre pixel (512 px tiles).
                tolerance = tolerance_px * world / (512.0 * 2.0 ** zoom)
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
            world = 2 * math.pi * 6378137.0
            for rule in self._per_zoom(flat_rule):
                # The direction is taken over one line width at the zoom (a
                # line zigzagging within its own width is shaded as QGIS
                # shades it: as a straight band).
                window = width_px * world / (512.0 * 2.0 ** (rule.get_attr("o") + 0.5))
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
        metres = (ZoomLevels.zoom_to_scale(0) or 0.0) / 2.0 ** zoom / 1000.0
        if crs.isGeographic():
            return metres / 111320.0
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
        painter = len(fill_layers) > 1 and fill.opacity() >= 1.0 and all(
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

        low, high = flat_rule.get_attr("o"), min(flat_rule.get_attr("i"), self.max_zoom)
        if screen and low <= high:
            features = self._layer_totals(flat_rule.layer)[2]
            arrows = features * (10 if layer.isRepeated() else 1) * (high - low + 1)
            steps = next((n for n in self.ARROW_BAND_STEPS if arrows * n <= self.MAX_PATTERN_ELEMENTS), 1)
            zoom_rules = self._per_zoom(flat_rule)
            bands = [band for index, rule in enumerate(zoom_rules)
                     for band in self._sub_zoom_bands(rule, steps, index == len(zoom_rules) - 1)]
        else:
            # Map units: one dataset; arcs flattened at the finest zoom.
            bands = [(flat_rule, max(low, high) + 0.5)]

        rules = []
        for copy, fill_layer in enumerate(fill_layers):
            fill_layer = fill_layer.clone()
            translate = None
            if fill_layer.layerType() == "SimpleFill":
                offset = fill_layer.offset()
                if abs(offset.x()) > 1e-9 or abs(offset.y()) > 1e-9:
                    translate = (offset.x(), offset.y(),
                                 QgsUnitTypes.encodeUnit(fill_layer.offsetUnit()))
                    fill_layer.setOffset(QPointF())
            symbol = QgsFillSymbol([fill_layer])
            symbol.setOpacity(fill.opacity())
            for band_rule, zoom in bands:
                derived = self._with_symbol(band_rule, symbol.clone(), 2, 1 + copy, recipe(zoom, copy))
                derived.translate = translate
                rules.append(derived)
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

    def _band_rules(self, flat_rule, layer, ramp, recipe_of, overlap_ok=True):
        """One rule drawing every colour band of the fill: a solid fill whose
        colour is the band's (COLOR_FIELD), from one materialized dataset."""
        from qgis.core import QgsFillSymbol, QgsSimpleFillSymbolLayer  # pylint: disable=import-outside-toplevel
        probe = [ramp.color(i / 64) for i in range(65)]
        bands = self._band_count(probe)
        colors = [ramp.color((i + 0.5) / bands) for i in range(bands)]
        overlap = overlap_ok and all(c.alpha() == 255 for c in colors)
        encoded = [QgsSymbolLayerUtils.encodeColor(c) for c in colors]  # "r,g,b,a", as QGIS reads it
        fill = QgsSimpleFillSymbolLayer(colors[0], Qt.BrushStyle.SolidPattern, colors[0],
                                        Qt.PenStyle.NoPen)
        fill.setDataDefinedProperty(QgsSymbolLayer.Property.PropertyFillColor,
                                    QgsProperty.fromField(mat.COLOR_FIELD))
        fill.setOffset(layer.offset())
        fill.setOffsetUnit(layer.offsetUnit())
        symbol = QgsFillSymbol([fill])
        symbol.setOpacity(flat_rule.rule.symbol().opacity())
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
        return self._band_rules(flat_rule, layer, self._ramp_of(layer), recipe)

    def _shapeburst(self, flat_rule: FlattenedRule, layer) -> List[FlattenedRule]:
        """A shapeburst fill as inset bands from the edge (colour 1) inwards."""
        whole = layer.useWholeShape()
        distance = 0.0
        if not whole:
            distance = layer.maxDistance()
            mm = _to_mm(distance, layer.distanceUnit())
            if mm is not None:
                # Screen units: the map distance at the middle of the rule's
                # zoom range (Web Mercator metres; 96 dpi CSS pixels).
                low, high = flat_rule.get_attr("o"), flat_rule.get_attr("i")
                if low is not None and high is not None:
                    high = min(float(high), float(self.max_zoom))
                    zoom = (min(float(low), high) + high) / 2
                else:
                    zoom = 16.0
                distance = mm * 96 / 25.4 * 40075016.68557849 / (512 * 2 ** zoom)
                self._report("Q2VT_GRADIENT_APPROXIMATE",
                             f"Shapeburst distance in screen units is fixed at zoom {zoom:g}.", flat_rule)
            if not distance or distance <= 0:
                return []
        if layer.blurRadius():
            self._report("Q2VT_GRADIENT_APPROXIMATE",
                         "Shapeburst blur is not applied (bands are already smooth steps).", flat_rule)
        crs = self.project_crs or flat_rule.layer.crs().authid()

        def recipe(band, bands, _overlap):
            return mat.shapeburst_band_recipe(distance, whole, layer.ignoreRings(), band, bands, crs)
        return self._band_rules(flat_rule, layer, self._ramp_of(layer), recipe)

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
        crs = self.project_crs or flat_rule.layer.crs().authid()
        recipe = mat.hatch_recipe(layer.lineAngle(), layer.distance(), offset, crs, "feature")
        return [self._with_symbol(flat_rule, sub.clone(), 1, 1, recipe)]
