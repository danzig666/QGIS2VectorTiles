"""QGIS reference rendering helpers for equivalence tests."""

from qgis.core import QgsMapRendererSequentialJob, QgsMapSettings, QgsRectangle
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QColor


def render(layers, extent, size=(300, 300), crs=None):
    settings = QgsMapSettings()
    settings.setLayers(list(layers))
    settings.setDestinationCrs(crs or layers[0].crs())
    settings.setExtent(QgsRectangle(*extent) if isinstance(extent, tuple) else extent)
    settings.setOutputSize(QSize(*size))
    settings.setBackgroundColor(QColor("white"))
    # Like the map canvas: without the map settings scope, @map_scale and
    # other map variables are NULL in symbol and label expressions.
    from qgis.core import QgsExpressionContext, QgsExpressionContextUtils, QgsProject
    settings.setExpressionContext(QgsExpressionContext([
        QgsExpressionContextUtils.globalScope(),
        QgsExpressionContextUtils.projectScope(QgsProject.instance()),
        QgsExpressionContextUtils.mapSettingsScope(settings),
    ]))
    job = QgsMapRendererSequentialJob(settings)
    job.start()
    job.waitForFinished()
    return job.renderedImage()


def ink_mask(img, threshold=200):
    return {(x, y) for y in range(img.height()) for x in range(img.width())
            if min(QColor(img.pixel(x, y)).getRgb()[:3]) < threshold}


def mask_difference(a, b, tolerance_px=1):
    """Fraction of inked pixels in either mask with no match within tolerance."""
    def unmatched(src, dst):
        count = 0
        for x, y in src:
            if not any((x + dx, y + dy) in dst for dx in range(-tolerance_px, tolerance_px + 1)
                       for dy in range(-tolerance_px, tolerance_px + 1)):
                count += 1
        return count
    total = len(a) + len(b)
    if total == 0:
        return 0.0
    return (unmatched(a, b) + unmatched(b, a)) / total
