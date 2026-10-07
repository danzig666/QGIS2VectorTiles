"""
Paint effects of simple lines (QGIS effect stacks) for the browser.

QGIS draws an effect as an image of the whole symbol layer. On a line:

* outer effects (outer glow, drop shadow) are drawn by the converter as
  wider, blurred lines; their geometry is simplified at the effect's size,
  since a blurred line on dense vertices shows spikes and overlaps;
* inner effects (inner shadow, inner glow) stay inside the line and depend
  on the line's direction on screen (the shadow's offset is fixed on
  screen). They are drawn as strips across the line whose colours come from
  QGIS itself: a straight line of each direction bucket rendered with the
  layer's inner effects and sampled across its width.
"""

import math
from typing import List, Optional

# QgsPaintEffect::type() names (SIP does not give every effect its class:
# an inner shadow comes back as a plain QgsPaintEffect).
OUTER_EFFECTS = ("outerGlow", "dropShadow")
INNER_EFFECTS = ("innerShadow", "innerGlow")
SOURCE_EFFECT = "drawSource"
DIRECTION_BUCKETS = 36  # 10 degrees: the inner shadow moves <= 0.17 x its offset
DPI = 96.0


def effect_list(layer) -> list:
    """Enabled effects of a symbol layer's paint effect, in drawing order."""
    effect = layer.paintEffect() if hasattr(layer, "paintEffect") else None
    if effect is None or not effect.enabled() or effect.type() == "default":
        return []
    if effect.type() == "effectStack":
        return [e for e in effect.effectList() if e.enabled()]
    return [effect]


def _draws(effect) -> bool:
    try:
        return int(getattr(effect.drawMode(), "value", effect.drawMode())) != 1  # not modifier only
    except (AttributeError, TypeError, ValueError):
        return True


def classify(layer):
    """(outer, inner, other) effect names that draw something."""
    outer, inner, other = [], [], []
    for effect in effect_list(layer):
        name = effect.type()
        if name == SOURCE_EFFECT:
            continue
        if not _draws(effect):
            other.append(name)
        elif name in OUTER_EFFECTS:
            outer.append(name)
        elif name in INNER_EFFECTS:
            inner.append(name)
        else:
            other.append(name)
    return outer, inner, other


def with_effects(layer, names):
    """Clone of ``layer`` keeping only its effects of the given types (and
    its source), in order; no effect at all when none is kept."""
    from qgis.core import QgsEffectStack, QgsPaintEffectRegistry  # pylint: disable=import-outside-toplevel
    clone = layer.clone()
    kept = [e.clone() for e in effect_list(layer) if e.type() in tuple(names) + (SOURCE_EFFECT,)]
    if not any(e.type() != SOURCE_EFFECT for e in kept):
        clone.setPaintEffect(QgsPaintEffectRegistry.defaultStack())
        return clone
    stack = QgsEffectStack()
    for effect in kept:
        stack.appendEffect(effect)
    clone.setPaintEffect(stack)
    return clone


def outer_extent_px(layer, to_px) -> float:
    """Total width in pixels the outer effects reach (line + spread + blur)."""
    width = to_px(layer.width(), layer.widthUnit()) or 0.0
    reach = width
    for effect in effect_list(layer):
        name = effect.type()
        if name not in OUTER_EFFECTS:
            continue
        blur = to_px(effect.blurLevel(), effect.blurUnit()) or 0.0
        if name == "outerGlow":
            spread = to_px(effect.spread(), effect.spreadUnit()) or 0.0
            reach = max(reach, width + 2 * (spread + blur))
        else:
            reach = max(reach, width + 2 * blur)
    return reach


def strips(width_px: float):
    """[(offset, width)] of the strips across a line about one pixel each,
    overlapping by a fraction of a pixel (no seams between them)."""
    count = max(2, int(math.ceil(width_px)))
    step = width_px / count
    return [(round(-width_px / 2 + (k + 0.5) * step, 3), round(step + 0.75, 3))
            for k in range(count)]


def inner_effect_spec(layer, width_px: float, buckets: int = DIRECTION_BUCKETS) -> Optional[dict]:
    """Strips and their colour per screen direction bucket: QGIS's rendering
    of the line with only its source and inner effects (in stack order)."""
    from qgis.core import (QgsEffectStack, QgsFeature, QgsGeometry, QgsLineSymbol,  # pylint: disable=import-outside-toplevel
                           QgsMapRendererSequentialJob, QgsMapSettings, QgsPointXY,
                           QgsRectangle, QgsSingleSymbolRenderer, QgsVectorLayer)
    from qgis.PyQt.QtCore import QSize  # pylint: disable=import-outside-toplevel
    from qgis.PyQt.QtGui import QColor, QImage  # pylint: disable=import-outside-toplevel
    kept = [e.clone() for e in effect_list(layer)
            if e.type() in INNER_EFFECTS + (SOURCE_EFFECT,)]
    if not any(e.type() in INNER_EFFECTS for e in kept):
        return None
    line = layer.clone()
    stack = QgsEffectStack()
    for effect in kept:
        stack.appendEffect(effect)
    line.setPaintEffect(stack)
    symbol = QgsLineSymbol([line])
    across = strips(width_px)
    opaque = layer.color().alpha() == 255
    size = int(max(160, 6 * width_px + 120))
    centre = size / 2.0
    memory = QgsVectorLayer("LineString?crs=EPSG:3857", "inner_effect", "memory")
    memory.setRenderer(QgsSingleSymbolRenderer(symbol))

    def render(start, end):
        memory.dataProvider().truncate()
        feature = QgsFeature()
        feature.setGeometry(QgsGeometry.fromPolylineXY([start, end]))
        memory.dataProvider().addFeatures([feature])
        settings = QgsMapSettings()
        settings.setLayers([memory])
        settings.setExtent(QgsRectangle(0, 0, size, size))
        settings.setOutputSize(QSize(size, size))
        settings.setOutputDpi(DPI)
        settings.setBackgroundColor(QColor(0, 0, 0, 0))
        job = QgsMapRendererSequentialJob(settings)
        job.start()
        job.waitForFinished()
        return job.renderedImage().convertToFormat(QImage.Format.Format_ARGB32)
    colors: List[List[str]] = []
    caps: List[str] = []
    for bucket in range(buckets):
        phi = (bucket + 0.5) * 2 * math.pi / buckets   # screen angle, y down
        dx, dy = math.cos(phi), math.sin(phi)
        reach = size  # long enough for the blur not to see the ends
        # Map y points up: screen (dx, dy) is map (dx, -dy).
        image = render(QgsPointXY(centre - dx * reach, size - (centre - dy * reach)),
                       QgsPointXY(centre + dx * reach, size - (centre + dy * reach)))
        row = []
        for offset, _ in across:
            # Right of the direction of travel (MapLibre's positive offset).
            nx, ny = -dy, dx
            samples = []
            for t in range(-12, 13, 3):
                samples.append(_bilinear(image, centre + nx * offset + dx * t,
                                         centre + ny * offset + dy * t))
            r, g, b, a = (sum(s[i] for s in samples) / len(samples) for i in range(4))
            if opaque:  # the line's own antialiased edge is drawn by the line
                a = 255.0
            row.append(f"rgba({round(r)}, {round(g)}, {round(b)}, {round(a / 255.0, 3)})")
        colors.append(row)
        # The round end of a line going this way (a run's end, or a sharp
        # turn): QGIS's colour in the middle of the cap.
        image = render(QgsPointXY(centre - dx * reach, size - (centre - dy * reach)),
                       QgsPointXY(centre, size - centre))
        samples = [_bilinear(image, centre + (dx * c - dy * k) * width_px / 2,
                             centre + (dy * c + dx * k) * width_px / 2)
                   for c, k in ((0.25, 0.0), (0.5, 0.0), (0.2, 0.35), (0.2, -0.35), (0.45, 0.4),
                                (0.45, -0.4))]
        r, g, b, a = (sum(s[i] for s in samples) / len(samples) for i in range(4))
        caps.append(f"rgba({round(r)}, {round(g)}, {round(b)}, {1.0 if opaque else round(a / 255.0, 3)})")
    # QGIS shades the union of overlapping lines: their shared inside is as
    # light as the lighter of them (an inner shadow only darkens). Darker
    # strips (mostly the edges) are drawn first, lighter ones over them.
    def lightness(index):
        total = 0.0
        for row in colors:
            r, g, b = (float(v) for v in row[index][5:-1].split(",")[:3])
            total += 0.299 * r + 0.587 * g + 0.114 * b
        return total
    order = sorted(range(len(across)), key=lightness)
    return {"strips": across, "colors": colors, "caps": caps, "buckets": buckets,
            "width": round(width_px, 3), "order": order}


def _bilinear(image, x: float, y: float):
    """Straight (not premultiplied) RGBA at a fractional pixel position."""
    x, y = x - 0.5, y - 0.5
    x0, y0 = int(math.floor(x)), int(math.floor(y))
    fx, fy = x - x0, y - y0
    acc = [0.0, 0.0, 0.0, 0.0]
    for xi, yi, w in ((x0, y0, (1 - fx) * (1 - fy)), (x0 + 1, y0, fx * (1 - fy)),
                      (x0, y0 + 1, (1 - fx) * fy), (x0 + 1, y0 + 1, fx * fy)):
        pixel = image.pixel(min(max(xi, 0), image.width() - 1), min(max(yi, 0), image.height() - 1))
        a = (pixel >> 24) & 255
        # premultiply for interpolation (ARGB32 is straight alpha)
        acc[0] += ((pixel >> 16) & 255) * a * w
        acc[1] += ((pixel >> 8) & 255) * a * w
        acc[2] += (pixel & 255) * a * w
        acc[3] += a * w
    if acc[3] <= 1e-9:
        return (0.0, 0.0, 0.0, 0.0)
    return (acc[0] / acc[3], acc[1] / acc[3], acc[2] / acc[3], acc[3])
