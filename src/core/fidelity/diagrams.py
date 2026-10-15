"""
diagrams.py

QGIS diagrams (pie, histogram, stacked bar, text) of a vector layer for the
web map. The tiles carry, per feature, the diagram's category values (one
text: "v1;v2;...") and its size; the web viewer draws each diagram as an
image (resources/web_viewer/diagrams.mjs) from those and the diagram's
settings (``spec``), placed like QGIS places diagrams over their feature.

The geometry follows QGIS (measured on its renderer, see
tests/integration/test_diagrams.py):

- pie: an ellipse of the diagram size; slices start ``rotationOffset``
  degrees clockwise from 3 o'clock and run clockwise or counter-clockwise;
- histogram: bars ``barWidth`` wide, ``value * scale`` long: an interpolated
  size scales by (upper size - lower size) / (upper value - lower value); a
  fixed size by the feature's largest value / the size (QGIS's own rule);
- stacked bar: one bar of the diagram size, split by the values;
- the size of an interpolated diagram: linear in the classification value
  (its square root when scaled by area) between the lower and upper size,
  at least the minimum size.

Not reproduced: data-defined diagram properties other than Show (position,
distance, colours...), the histogram axis, label collision priorities and
an "around" placement's distance (the diagram is drawn over the point).
"""

from typing import List, Optional, Tuple

from qgis.core import (Qgis, QgsCentroidFillSymbolLayer, QgsDiagramLayerSettings,
                       QgsExpression, QgsFillSymbol, QgsLinearlyInterpolatedDiagramRenderer,
                       QgsLineSymbol, QgsMarkerLineSymbolLayer, QgsMarkerSymbol, QgsProperty,
                       QgsSimpleMarkerSymbolLayer, QgsSymbolLayer)

# Diagram types QGIS names (QgsDiagram.diagramName()).
TYPES = {"Pie": "pie", "Histogram": "histogram", "Stacked": "stacked", "Text": "text"}
# Icon names of diagram images: "<prefix><style layer id>|<size>|<v1;v2;...>".
IMAGE_PREFIX = "q2vt-diagram|"
# Placeholder marker properties that carry the diagram into the tiles.
VALUES_PROPERTY = QgsSymbolLayer.Property.PropertyName
SIZE_PROPERTY = QgsSymbolLayer.Property.PropertySize
_PX = {"mm": 96.0 / 25.4, "pt": 96.0 / 72.0, "in": 96.0, "px": 1.0}


def _unit(unit) -> Optional[str]:
    """"mm" / "pt" / "in" / "px" / "map" of a QGIS render unit, None for
    others (percentage, metres in map units)."""
    names = {Qgis.RenderUnit.Millimeters: "mm", Qgis.RenderUnit.Points: "pt",
             Qgis.RenderUnit.Inches: "in", Qgis.RenderUnit.Pixels: "px",
             Qgis.RenderUnit.MapUnits: "map"}
    return names.get(unit)


def _color(color) -> str:
    return f"rgba({color.red()},{color.green()},{color.blue()},{round(color.alphaF(), 3)})"


def _number(expression: str) -> str:
    """A category value as QGIS draws it: a number, 0 where it is none."""
    return f"coalesce(try(to_real(({expression}))), 0)"


def values_expression(attributes: List[str]) -> str:
    """The category values of a feature as one text, "v1;v2;..." (rounded:
    equal diagrams share one image in the browser)."""
    parts = ", ".join(f"round({_number(a)}, 4)" for a in attributes)
    return f"array_to_string(array({parts}), ';')"


def size_expression(renderer, settings, field_names: List[str]) -> Optional[str]:
    """Width of an interpolated diagram (QgsDiagram::sizeForValue), in its
    size unit; None for a fixed size. ``field_names``: the layer's fields (a
    classification field is given by its index)."""
    if not isinstance(renderer, QgsLinearlyInterpolatedDiagramRenderer):
        return None
    value = None
    if renderer.classificationAttributeIsExpression():
        value = renderer.classificationAttributeExpression()
    elif 0 <= renderer.classificationField() < len(field_names):
        value = QgsExpression.quotedColumnRef(field_names[renderer.classificationField()])
    if not value:
        return None
    lower, upper = float(renderer.lowerValue()), float(renderer.upperValue())
    lower_w, upper_w = float(renderer.lowerSize().width()), float(renderer.upperSize().width())
    v = _number(value)
    if settings.scaleByArea:
        v, lower, upper = f"sqrt(max({v}, 0))", lower ** 0.5 if lower > 0 else 0.0, \
            upper ** 0.5 if upper > 0 else 0.0
    if upper == lower:
        return f"{upper_w}"
    ratio = f"(({v}) - {lower}) / {upper - lower}"
    width = f"({upper_w} * {ratio} + {lower_w} * (1 - {ratio}))"
    minimum = float(settings.minimumSize or 0)
    if minimum > 0:
        width = f"max({width}, {minimum})"
    return f"round({width}, 3)"



def diagram_spec(layer) -> Tuple[Optional[dict], List[str]]:
    """(spec, notes) of a layer's diagrams, (None, notes) without any.

    ``spec``: what the viewer needs to draw them (sizes in their QGIS unit,
    ``px`` per unit for screen units) and what the export needs (``values``,
    ``size`` expressions, the placement and scale range)."""
    notes: List[str] = []
    try:
        enabled = layer.diagramsEnabled()
    except AttributeError:
        enabled = False
    renderer = layer.diagramRenderer() if enabled else None
    if renderer is None or renderer.diagram() is None:
        return None, notes
    kind = TYPES.get(renderer.diagram().diagramName())
    settings_list = renderer.diagramSettings()
    if kind is None or not settings_list:
        return None, [f"diagram type {renderer.diagram().diagramName()} is not exported"]
    settings = settings_list[0]
    if not settings.enabled or not settings.categoryAttributes:
        return None, notes
    unit = _unit(settings.sizeType)
    if unit is None:
        notes.append("diagram size unit is drawn as millimetres")
        unit = "mm"
    line_unit = _unit(settings.lineSizeUnit) or "mm"
    interpolated = isinstance(renderer, QgsLinearlyInterpolatedDiagramRenderer)
    spec = {
        "type": kind,
        "values": values_expression(list(settings.categoryAttributes)),
        "size": size_expression(renderer, settings, [f.name() for f in layer.fields()]),
        "colors": [_color(c) for c in settings.categoryColors],
        "labels": list(settings.categoryLabels),
        "unit": unit,
        "lineUnit": line_unit,
        "width": float(settings.size.width()),
        "height": float(settings.size.height()),
        "aspect": 1.0,
        "barWidth": float(settings.barWidth),
        "penColor": _color(settings.penColor),
        "penWidth": float(settings.penWidth),
        "opacity": float(settings.opacity),
        "background": _color(settings.backgroundColor),
        "rotation": float(settings.rotationOffset),
        "clockwise": settings.direction() == settings.Direction.Clockwise,
        "orientation": {0: "up", 1: "down", 2: "left", 3: "right"}.get(
            int(settings.diagramOrientation), "up"),
        "interpolated": interpolated,
        "scale": None,
    }
    spacing_unit = _unit(settings.spacingUnit())
    spec["spacing"] = float(settings.spacing()) * (
        _PX.get(spacing_unit, 1.0) / _PX.get(unit, 1.0) if spacing_unit in _PX and unit in _PX else 1.0)
    if interpolated:
        upper, lower = renderer.upperSize(), renderer.lowerSize()
        if upper.width() > 0:
            spec["aspect"] = float(upper.height()) / float(upper.width())
        span = float(renderer.upperValue()) - float(renderer.lowerValue())
        # QGIS's histogram scale: (upper width - lower height) / value span.
        spec["scale"] = (float(upper.width()) - float(lower.height())) / span if span else 0.0
    elif settings.size.width() > 0:
        spec["aspect"] = float(settings.size.height()) / float(settings.size.width())
    if settings.showAxis():
        notes.append("the histogram axis is not drawn")
    layer_settings = layer.diagramLayerSettings()
    spec["showAll"] = bool(layer_settings.showAllDiagrams())
    spec["placement"] = _placement(layer, layer_settings)
    if layer_settings.placement() == QgsDiagramLayerSettings.Placement.AroundPoint and \
            layer_settings.distance():
        notes.append("an 'around' placement is drawn over the point (no distance)")
    properties = layer_settings.dataDefinedProperties()
    show = properties.property(QgsDiagramLayerSettings.Property.Show)
    spec["show"] = show.asExpression() if show is not None and show.isActive() else ""
    ignored = [name for key, name in ((QgsDiagramLayerSettings.Property.PositionX, "position"),
                                      (QgsDiagramLayerSettings.Property.Distance, "distance"),
                                      (QgsDiagramLayerSettings.Property.StartAngle, "start angle"),
                                      (QgsDiagramLayerSettings.Property.BackgroundColor, "background"),
                                      (QgsDiagramLayerSettings.Property.StrokeColor, "outline colour"),
                                      (QgsDiagramLayerSettings.Property.StrokeWidth, "outline width"))
               if properties.property(key) is not None and properties.property(key).isActive()]
    if ignored:
        notes.append(f"data-defined diagram {', '.join(ignored)} use the static value")
    spec["scaleRange"] = (float(settings.maximumScale), float(settings.minimumScale)) \
        if settings.scaleBasedVisibility else None
    return spec, notes


def _placement(layer, layer_settings) -> str:
    """Where a feature's diagram goes: "point" (a point layer), "centroid" /
    "surface" (a polygon: over its centroid, or inside it), "line" (the
    middle of a line)."""
    geometry = layer.geometryType()
    if geometry == Qgis.GeometryType.Point:
        return "point"
    if geometry == Qgis.GeometryType.Line:
        return "line"
    placement = layer_settings.placement()
    if placement in (QgsDiagramLayerSettings.Placement.Horizontal, QgsDiagramLayerSettings.Placement.Free):
        return "surface"
    return "centroid"


def placeholder_symbol(spec: dict, geometry):
    """A symbol whose marker carries the diagram into the tiles (Name: the
    values, Size: an interpolated size) at the diagram's position; the style
    draws the diagram instead (maplibre_converter._diagram_layer)."""
    marker_layer = QgsSimpleMarkerSymbolLayer()
    marker_layer.setSize(1.0)
    marker_layer.setDataDefinedProperty(VALUES_PROPERTY, QgsProperty.fromExpression(spec["values"]))
    if spec.get("size"):
        marker_layer.setDataDefinedProperty(SIZE_PROPERTY, QgsProperty.fromExpression(spec["size"]))
    marker = QgsMarkerSymbol([marker_layer])
    if spec["placement"] == "point" or geometry == Qgis.GeometryType.Point:
        return marker
    if spec["placement"] == "line" or geometry == Qgis.GeometryType.Line:
        line_layer = QgsMarkerLineSymbolLayer()
        line_layer.setPlacements(Qgis.MarkerLinePlacements(Qgis.MarkerLinePlacement.CentralPoint))
        line_layer.setRotateSymbols(False)
        line_layer.setSubSymbol(marker)
        return QgsLineSymbol([line_layer])
    centroid = QgsCentroidFillSymbolLayer()
    centroid.setPointOnSurface(spec["placement"] == "surface")
    centroid.setPointOnAllParts(False)
    centroid.setSubSymbol(marker)
    return QgsFillSymbol([centroid])


def px_per_unit(unit: str) -> Optional[float]:
    """CSS pixels per screen size unit (None: map units)."""
    return _PX.get(unit)
