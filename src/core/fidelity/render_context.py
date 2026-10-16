"""Render contexts built by hand (sprites, size probes) need the map's CRS
for distances: QGIS converts a "meters at scale" size
(Qgis.RenderUnit.MetersInMapUnits) to map units through the context's
QgsDistanceArea, and without a source CRS a 300 m marker came out a
thousandth of a pixel wide (an empty sprite)."""

from qgis.core import QgsCoordinateReferenceSystem, QgsDistanceArea, QgsProject


def with_map_distances(context, crs=None):
    """``context`` with a distance calculator in ``crs`` (default: the
    project CRS, else Web Mercator, the tiles' CRS); returned for chaining."""
    project = QgsProject.instance()
    if crs is None or not crs.isValid():
        crs = project.crs() if project.crs().isValid() else QgsCoordinateReferenceSystem("EPSG:3857")
    distances = QgsDistanceArea()
    distances.setSourceCrs(crs, project.transformContext())
    context.setDistanceArea(distances)
    return context
