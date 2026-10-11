"""
reference_scale.py

Symbology reference scale (``QgsFeatureRenderer.referenceScale()``).

With a reference scale QGIS multiplies every millimetre, point, pixel and inch
size of the layer's symbols and labels by ``reference scale / map scale``
(QgsVectorLayerRenderer and QgsLabelingEngine set it on the render context;
QgsRenderContext::convertToPainterUnits applies it), so those sizes follow the
map like map units. The exporter rewrites them, on its own copies of the
rules, as map units of their size at the reference scale; everything
downstream (zoom curves, per-zoom sprites, materialized geometry) then
treats them as the map-unit sizes they are.
"""

from typing import List, Optional

from qgis.core import (Qgis, QgsEffectStack, QgsMapUnitScale, QgsPalLayerSettings, QgsProperty,
                       QgsSymbolLayer)
from qgis.PyQt.QtCore import QPointF, QSizeF

from .fidelity.qgis_expr import in_screen_units
from .fidelity.units import MM, normalize_unit, physical_factor

# Value getters whose name is not the unit getter's name without "Unit"
# (tried in order; the first one the object has is used).
_VALUE_NAMES = {
    "customDashPatternUnit": ("customDashVector",),
    "averageAngleUnit": ("averageAngleLength",),
    "randomDeviationXUnit": ("maximumRandomDeviationX",),
    "randomDeviationYUnit": ("maximumRandomDeviationY",),
    "distanceUnit": ("distance", "maxDistance", "scale"),  # line pattern, shapeburst, vector field
    "offsetUnit": ("offset", "offsetDistance"),  # shadow settings and effects
    "blurUnit": ("blurLevel",),
    "blurRadiusUnit": ("blurRadius",),
}
# Unit getters without a size of their own (aggregate or unused).
_NO_VALUE = {"outputUnit", "strokeWidthUnit"}
# Areas scale with the square of the length factor.
_AREAS = {"densityAreaUnit"}

_P = QgsSymbolLayer.Property
# Data-defined properties holding a plain number in the unit of a converted
# size (Property names; a missing name is ignored on older QGIS).
_NUMERIC_PROPERTIES = {
    "sizeUnit": ("PropertySize",),
    "strokeWidthUnit": ("PropertyStrokeWidth",),
    "widthUnit": ("PropertyStrokeWidth",),
    "symbolWidthUnit": ("PropertyWidth",),
    "symbolHeightUnit": ("PropertyHeight",),
    "intervalUnit": ("PropertyInterval",),
    "offsetAlongLineUnit": ("PropertyOffsetAlongLine",),
    "averageAngleUnit": ("PropertyAverageAngleLength",),
    "distanceXUnit": ("PropertyDistanceX",),
    "distanceYUnit": ("PropertyDistanceY",),
    "displacementXUnit": ("PropertyDisplacementX",),
    "displacementYUnit": ("PropertyDisplacementY",),
    "distanceUnit": ("PropertyLineDistance", "PropertyShapeburstMaxDistance"),
    "hashLengthUnit": ("PropertyLineDistance",),
    "arrowWidthUnit": ("PropertyArrowWidth",),
    "arrowStartWidthUnit": ("PropertyArrowStartWidth",),
    "headLengthUnit": ("PropertyArrowHeadLength",),
    "headThicknessUnit": ("PropertyArrowHeadThickness",),
    "trimDistanceStartUnit": ("PropertyTrimStart",),
    "trimDistanceEndUnit": ("PropertyTrimEnd",),
    "dashPatternOffsetUnit": ("PropertyDashPatternOffset",),
}
# Data-defined lengths that are not a plain number (an "x,y" offset, a dash
# list): reported when active on a converted symbol layer.
_OTHER_LENGTH_PROPERTIES = ("PropertyOffset", "PropertyCustomDash")
_LABEL_NUMERIC_PROPERTIES = ("Size", "BufferSize", "LabelDistance")
_LABEL_OTHER_PROPERTIES = ("OffsetXY", "ShapeSizeX", "ShapeSizeY", "ShapeOffset", "ShapeRadii",
                           "ShapeStrokeWidth", "ShadowOffsetDist", "ShadowRadius",
                           "RepeatDistance", "OverrunDistance", "MaskBufferSize")


def _setter_name(getter: str) -> str:
    return "set" + getter[0].upper() + getter[1:]


def _scaled(value, factor: float):
    if isinstance(value, QPointF):
        return QPointF(value.x() * factor, value.y() * factor)
    if isinstance(value, QSizeF):
        return QSizeF(value.width() * factor, value.height() * factor)
    if isinstance(value, (list, tuple)):
        return [float(item) * factor for item in value]
    return float(value) * factor


class ReferenceScaleConverter:
    """Rewrite screen-unit sizes as map units at ``reference_scale``.

    ``scale_metres_per_map_unit``: metres QGIS's scale calculator counts per
    project map unit (MapUnitContext). ``unhandled`` collects what keeps its
    screen size (for one diagnostic per layer).
    """

    def __init__(self, reference_scale: float, scale_metres_per_map_unit: float = 1.0,
                 project_crs: str = "", layer_crs: str = ""):
        self.reference_scale = float(reference_scale)
        self.scale_metres_per_map_unit = float(scale_metres_per_map_unit) or 1.0
        self.project_crs = project_crs
        self.layer_crs = layer_crs
        self.unhandled: List[str] = []

    def map_units_per(self, unit) -> Optional[float]:
        """Project map units of one ``unit`` (mm, pt, px, in) at the reference scale."""
        px = physical_factor(normalize_unit(unit))
        if px is None:
            return None
        mm = px / physical_factor(MM)
        return mm / 1000.0 * self.reference_scale / self.scale_metres_per_map_unit

    # --- objects with xxxUnit()/xxx() pairs ---------------------------------
    def _convert_units(self, obj, what: str) -> List[str]:
        """Convert every screen-unit ``xxxUnit``/``xxx`` pair of ``obj``;
        returns the converted unit getter names."""
        converted = []
        for name in dir(obj):
            if not name.endswith("Unit") or name.startswith("set") or name in ("outputUnit",):
                continue
            unit_setter = getattr(obj, _setter_name(name), None)
            getter = getattr(obj, name, None)
            if not callable(getter) or not callable(unit_setter):
                continue
            try:
                unit = getter()
            except TypeError:
                continue
            factor = self.map_units_per(unit)
            if factor is None:
                continue
            base = name[:-len("Unit")]
            value_name = next((candidate for candidate in _VALUE_NAMES.get(name, (base,))
                               if callable(getattr(obj, candidate, None))
                               and callable(getattr(obj, _setter_name(candidate), None))), None)
            if value_name is None:
                if name not in _NO_VALUE:
                    self.unhandled.append(f"{what} {base}")
                continue
            if name in _AREAS:
                factor *= factor
            try:
                getattr(obj, _setter_name(value_name))(_scaled(getattr(obj, value_name)(), factor))
            except (TypeError, ValueError):
                self.unhandled.append(f"{what} {base}")
                continue
            unit_setter(Qgis.RenderUnit.MapUnits)
            scale_setter = getattr(obj, f"set{base[0].upper()}{base[1:]}MapUnitScale", None)
            if callable(scale_setter):
                scale_setter(QgsMapUnitScale())
            converted.append(name)
        return converted

    def _scale_property(self, collection, key, factor: float) -> None:
        prop = collection.property(key)
        expression = prop.asExpression() if prop is not None else ""
        if expression:
            collection.setProperty(key, QgsProperty.fromExpression(f"({expression}) * {factor!r}"))

    # --- symbols --------------------------------------------------------------
    def symbol(self, symbol) -> None:
        if symbol is None:
            return
        for layer in symbol.symbolLayers():
            self.symbol_layer(layer)

    def symbol_layer(self, layer) -> None:
        kind = layer.layerType()
        if kind == "GeometryGenerator":
            self._generator(layer)
        else:
            units = {name: getattr(layer, name)() for name in dir(layer)
                     if name.endswith("Unit") and not name.startswith("set")
                     and callable(getattr(layer, name, None)) and name != "outputUnit"
                     and _safe_unit(layer, name) is not None}
            converted = self._convert_units(layer, kind)
            properties = layer.dataDefinedProperties()
            scaled = set()
            for name in converted:
                factor = self.map_units_per(units[name])
                if name in _AREAS:
                    factor *= factor
                for key in _NUMERIC_PROPERTIES.get(name, ()):
                    enum = getattr(_P, key, None)
                    if enum is not None and key not in scaled and properties.isActive(enum):
                        self._scale_property(properties, enum, factor)
                        scaled.add(key)
                if name == "offsetUnit":
                    enum = _P.PropertyOffset
                    if properties.isActive(enum):
                        if int(getattr(layer.type(), "value", layer.type())) == 1:  # line: a number
                            self._scale_property(properties, enum, factor)
                        else:
                            self.unhandled.append(f"{kind} data-defined offset")
                if name == "customDashPatternUnit" and properties.isActive(_P.PropertyCustomDash):
                    self.unhandled.append(f"{kind} data-defined dash pattern")
            layer.setDataDefinedProperties(properties)
            self._effect(layer.paintEffect(), kind)
        self.symbol(layer.subSymbol())

    def _generator(self, layer) -> None:
        """A generator in screen units: its expression evaluated on the
        geometry scaled to those units at the reference scale (constant map
        units per unit), then a map-unit generator."""
        per_unit = self.map_units_per(layer.units())
        if per_unit is not None:
            layer.setGeometryExpression(in_screen_units(
                layer.geometryExpression(), per_unit, self.project_crs, self.layer_crs))
            layer.setUnits(Qgis.RenderUnit.MapUnits)

    def _effect(self, effect, what: str) -> None:
        if effect is None or not effect.enabled():
            return
        effects = effect.effectList() if isinstance(effect, QgsEffectStack) else [effect]
        for item in effects:
            self._convert_units(item, f"{what} effect")

    # --- labels ---------------------------------------------------------------
    def label_settings(self, settings: QgsPalLayerSettings) -> QgsPalLayerSettings:
        """A copy of ``settings`` with its screen-unit sizes in map units."""
        result = QgsPalLayerSettings(settings)
        text = result.format()
        sized = {"sizeUnit": text.sizeUnit()}
        self._convert_units(text, "label text")
        for part_name in ("buffer", "background", "shadow", "mask"):
            part = getattr(text, part_name)()
            if part.enabled():
                self._convert_units(part, f"label {part_name}")
                if part_name == "background":
                    self.symbol(part.markerSymbol())
                    self.symbol(part.fillSymbol())
                getattr(text, _setter_name(part_name))(part)
        result.setFormat(text)
        for unit_attr, values in (("distUnits", ("dist",)), ("offsetUnits", ("xOffset", "yOffset")),
                                  ("repeatDistanceUnit", ("repeatDistance",)),
                                  ("overrunDistanceUnit", ("overrunDistance",))):
            factor = self.map_units_per(getattr(result, unit_attr))
            if factor is None:
                continue
            for value in values:
                setattr(result, value, getattr(result, value) * factor)
            setattr(result, unit_attr, Qgis.RenderUnit.MapUnits)
        callout = result.callout()
        if callout is not None and callout.enabled():
            callout = callout.clone()
            self._convert_units(callout, "label callout")
            self.symbol(getattr(callout, "lineSymbol", lambda: None)())
            result.setCallout(callout)
        properties = result.dataDefinedProperties()
        label_property = QgsPalLayerSettings.Property
        original = settings.format()  # buffer() refers into it: keep it alive
        factors = {"Size": self.map_units_per(sized["sizeUnit"]),
                   "BufferSize": self.map_units_per(original.buffer().sizeUnit()),
                   "LabelDistance": self.map_units_per(settings.distUnits)}
        for name in _LABEL_NUMERIC_PROPERTIES:
            enum = getattr(label_property, name, None)
            if enum is not None and properties.isActive(enum) and factors[name] is not None:
                self._scale_property(properties, enum, factors[name])
        for name in _LABEL_OTHER_PROPERTIES:
            enum = getattr(label_property, name, None)
            if enum is not None and properties.isActive(enum):
                self.unhandled.append(f"label data-defined {name}")
        result.setDataDefinedProperties(properties)
        return result


def _safe_unit(obj, name: str):
    try:
        return getattr(obj, name)()
    except TypeError:
        return None


def apply_to_rules(rule_system, rule_type: int, converter: ReferenceScaleConverter) -> None:
    """Convert every symbol (``rule_type`` 0) or label settings (1) of a
    rule-based renderer / labeling copy in place."""
    root = rule_system.rootRule()
    for rule in [root] + list(root.descendants()):
        if rule_type == 0:
            converter.symbol(rule.symbol())
        elif rule.settings() is not None:
            rule.setSettings(converter.label_settings(rule.settings()))
