"""
masks.py

QGIS selective masking on the web map, approximated. QGIS cuts the chosen
symbol layers around a label (a label's mask) or a mask marker's shape, so
what lies under those symbol layers shows through. MapLibre cannot cut one
layer with another's shape, so:

- a label's mask becomes the label's halo in the map's background colour
  (a halo of the mask's size, as strong as the mask's opacity): it covers
  everything under the label, not only the masked symbol layers;
- a mask marker becomes its shape drawn in the background colour, where
  the marker is, over the layers drawn before it.

Both are exact over a plain background (the usual case: contour labels,
road labels, symbols knocking out the lines under them) and are reported as
approximate (Q2VT_MASK_APPROXIMATE).
"""

from typing import Optional

from qgis.core import QgsProject, QgsSymbolLayer
from qgis.PyQt.QtGui import QColor

_COLOUR_PROPERTIES = ("PropertyFillColor", "PropertyStrokeColor", "PropertyColor",
                      "PropertySecondaryColor")


def background_colour(project=None) -> QColor:
    """The map's background colour (QGIS draws masks over it)."""
    project = project or QgsProject.instance()
    colour = QColor(project.backgroundColor())
    return colour if colour.isValid() else QColor(255, 255, 255)


def label_mask(text_format) -> Optional[object]:
    """The label's mask settings when it masks anything, else None."""
    mask = text_format.mask() if hasattr(text_format, "mask") else None
    if mask is None or not mask.enabled() or not mask.maskedSymbolLayers():
        return None
    return mask


def _recolour(symbol, colour: QColor) -> None:
    for layer in symbol.symbolLayers():
        layer.setColor(colour)
        for setter in ("setFillColor", "setStrokeColor"):
            if hasattr(layer, setter):
                getattr(layer, setter)(colour)
        properties = layer.dataDefinedProperties()
        for name in _COLOUR_PROPERTIES:
            key = getattr(QgsSymbolLayer.Property, name, None)
            if key is not None and properties.property(key) is not None:
                properties.property(key).setActive(False)
        layer.setDataDefinedProperties(properties)
        if layer.subSymbol() is not None:
            _recolour(layer.subSymbol(), colour)


def knock_out_mask_markers(symbol, colour: QColor) -> int:
    """Replaces every mask marker in ``symbol`` (its sub-symbols too) by its
    shape in ``colour``; returns how many."""
    if symbol is None:
        return 0
    count = 0
    index = 0
    while index < symbol.symbolLayerCount():
        layer = symbol.symbolLayer(index)
        if layer.layerType() == "MaskMarker":
            # One that masks nothing is not enabled() (QGIS draws nothing).
            shape = layer.subSymbol()
            if layer.masks() and shape is not None and shape.symbolLayerCount():
                shape = shape.clone()
                _recolour(shape, colour)
                for offset, part in enumerate(shape.symbolLayers()):
                    symbol.insertSymbolLayer(index + offset, part.clone())
                index += shape.symbolLayerCount()
                symbol.takeSymbolLayer(index)  # owned by Python, freed with it
                count += 1
                continue
        else:
            count += knock_out_mask_markers(layer.subSymbol(), colour)
        index += 1
    return count
