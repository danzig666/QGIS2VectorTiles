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

from typing import List, Optional

from qgis.core import (
    Qgis,
    QgsLineSymbol,
    QgsMarkerLineSymbolLayer,
    QgsMarkerSymbol,
    QgsProject,
    QgsProperty,
    QgsSimpleLineSymbolLayer,
    QgsSimpleMarkerSymbolLayer,
    QgsSymbolLayer,
)
from qgis.PyQt.QtCore import QPointF

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
        if kind == "PointPatternFill" and normalize_unit(layer.distanceXUnit()) == "map" \
                and normalize_unit(layer.distanceYUnit()) == "map":
            return self._dense_split(flat_rule, min(layer.distanceX(), layer.distanceY()),
                                     lambda rule: self._point_grid(rule, layer))
        if kind == "SVGFill" and normalize_unit(layer.patternWidthUnit()) == "map":
            return self._dense_split(flat_rule, layer.patternWidth(),
                                     lambda rule: self._svg_grid(rule, layer))
        return None

    # A map-unit grid becomes point features only from the zoom where its
    # spacing reaches this many CSS px; below that it is a per-zoom texture
    # (identical look, a fraction of the features).
    GRID_MIN_SPACING_PX = 8.0

    def _dense_split(self, flat_rule: FlattenedRule, spacing: float, materialize):
        """Texture for the zooms where a map-unit grid is dense, materialized
        points for the zooms where its spacing is large on screen."""
        low, high = flat_rule.get_attr("o"), min(flat_rule.get_attr("i"), self.max_zoom)
        switch = None
        for zoom in range(low, high + 1):
            px = spacing * 96.0 / (0.0254 * ZoomLevels.zoom_to_scale(zoom))
            if px >= self.GRID_MIN_SPACING_PX:
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
        if layer.maximumRandomDeviationX() or layer.maximumRandomDeviationY():
            self._report("Q2VT_PATTERN_APPROXIMATE",
                         "Random deviation of pattern markers is not reproduced.", flat_rule)
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
            inset, rows_from_top=clip != int(Qgis.MarkerClipMode.Shape))
        return [self._with_symbol(flat_rule, marker.clone(), 0, 1, recipe)]

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

    def _marker_line(self, flat_rule: FlattenedRule, layer) -> Optional[List[FlattenedRule]]:
        placements = _flag_names(layer.placements())
        points = placements & mat.POINT_PLACEMENTS
        if not points:
            return None  # interval only: native repeated symbol
        if "CurvePoint" in points:
            self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                         "Curve-point markers are placed on every vertex.", flat_rule)
        try:
            along = float(layer.offsetAlongLine())
        except AttributeError:
            along = 0.0
        if along:
            self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                         "Offset along the line is not applied to materialized markers.",
                         flat_rule)
        sub = layer.subSymbol()
        if sub is None:
            return []
        rules = []
        offset, offset_unit = layer.offset(), layer.offsetUnit()
        recipe = mat.marker_points(points)
        corner_sensitive = points - {"FirstVertex", "LastVertex"}
        if offset and corner_sensitive:
            if normalize_unit(offset_unit) == "map":
                # QGIS places these markers on the offset line; offset the
                # geometry itself (exact) instead of each marker.
                crs = self.project_crs or flat_rule.layer.crs().authid()
                recipe = mat.marker_points(points, offset=offset, crs=crs)
                offset = 0.0
            else:
                self._report("Q2VT_MARKER_PLACEMENT_APPROX",
                             "Screen-unit offset: markers are offset from the original line; "
                             "QGIS measures positions on the offset line.", flat_rule)
        symbol = self._marker_points_symbol(sub, layer.rotateSymbols(), 0.0, offset,
                                            offset_unit, flat_rule)
        rules.append(self._with_symbol(flat_rule, symbol, 0, 1, recipe))
        if "Interval" in placements:
            native = flat_rule.derive()
            interval_layer = layer.clone()
            interval_layer.setPlacements(Qgis.MarkerLinePlacement.Interval)
            native.rule.symbol().changeSymbolLayer(0, interval_layer)
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
