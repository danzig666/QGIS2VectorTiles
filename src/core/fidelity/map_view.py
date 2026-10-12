"""
map_view.py

The map view of an export, for the expressions that read it.

QGIS evaluates ``@map_extent``, ``@map_extent_center``, ``@map_extent_width``,
``@map_extent_height``, ``@map_rotation``, ``@map_crs`` and the like for the
map being drawn, again whenever it is panned or zoomed. The export has no
map: these were NULL, and a rule filtered by ``contains($geometry,
@map_extent_center)`` or a geometry generator drawn from it exported nothing.
Here they hold the web map's starting view (the export extent, in the map =
project CRS), once: the web map does not follow the view
(Q2VT_EXPR_MAP_VIEW_STATIC). ``@map_scale`` and the zoom variables keep their
per-zoom handling (fidelity.qgis_expr.with_map_scale) and are not in this
scope.

The state is plain data (``state()`` / ``restore()``), so worker processes
rebuild the same scope; ``scope()`` builds a QgsExpressionContextScope and is
called on a main thread only (QgsMapSettings, CRS lookups). Not configured
(tests, the Processing tool without an extent): an empty scope, as before.
"""

from typing import Iterable, Optional, Set

# Variables QGIS takes from the map settings, held for the starting view.
VIEW_VARIABLES = frozenset({
    "map_extent", "map_extent_center", "map_extent_width", "map_extent_height", "map_rotation",
    "map_id", "map_crs", "map_crs_description", "map_crs_definition", "map_crs_acronym",
    "map_crs_projection", "map_crs_ellipsoid", "map_crs_proj4", "map_crs_wkt", "map_units",
    "map_layer_ids", "map_layers"})
# Those that change as the map moves: the web map keeps the starting view's.
EXTENT_VARIABLES = frozenset({"map_extent", "map_extent_center", "map_extent_width",
                              "map_extent_height"})
# Set by QGIS only while drawing (cursor, animation, temporal): NULL here.
RENDER_ONLY_VARIABLES = frozenset({
    "canvas_cursor_point", "layer_cursor_point", "frame_number", "frame_rate", "frame_duration",
    "frame_timeoffset", "animation_start_time", "animation_end_time", "animation_interval",
    "map_start_time", "map_end_time", "map_interval"})
_PER_ZOOM = ("map_scale", "zoom_level", "vector_tile_zoom")

_STATE: Optional[dict] = None
_BUILT = None  # the scope of _STATE, built once (copies are handed out)


def configure(extent_3857, map_crs, layer_ids: Iterable[str] = ()) -> None:
    """The export's starting view: ``extent_3857`` (QgsRectangle, EPSG:3857,
    the export extent the web map opens on) seen in ``map_crs`` (the project
    CRS). The centre is the view's centre, where the web map opens, the
    rectangle the view's bounding box in the map CRS. None: not configured."""
    global _STATE, _BUILT  # pylint: disable=global-statement
    _STATE, _BUILT = None, None
    if extent_3857 is None or extent_3857.isEmpty() or map_crs is None or not map_crs.isValid():
        return
    from qgis.core import (QgsCoordinateReferenceSystem, QgsCoordinateTransform,  # pylint: disable=import-outside-toplevel
                           QgsPointXY, QgsProject)
    try:
        to_map = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:3857"), map_crs,
                                        QgsProject.instance().transformContext())
        rect = to_map.transformBoundingBox(extent_3857)
        centre = to_map.transform(QgsPointXY(extent_3857.center()))
    except Exception:  # pylint: disable=broad-except
        return  # outside the CRS's area: the variables stay NULL, as before
    if rect.isEmpty():
        return
    _STATE = {"rect": (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum()),
              "centre": (centre.x(), centre.y()), "crs": map_crs.authid(), "wkt": map_crs.toWkt(),
              "layer_ids": sorted(layer_ids)}


def state() -> Optional[dict]:
    """The configured view as plain (picklable) data, for workers and cache keys."""
    return dict(_STATE) if _STATE else None


def restore(saved: Optional[dict]) -> None:
    global _STATE, _BUILT  # pylint: disable=global-statement
    _STATE, _BUILT = (dict(saved) if saved else None), None


def scope():
    """A QgsExpressionContextScope with the view variables (empty when not
    configured), a copy of one built once. Main thread (first call) only;
    copy it for other threads."""
    global _BUILT  # pylint: disable=global-statement
    from qgis.core import QgsExpressionContextScope  # pylint: disable=import-outside-toplevel
    if _BUILT is None:
        _BUILT = _build()
    return QgsExpressionContextScope(_BUILT)


def _build():
    from qgis.core import (QgsExpressionContextScope,  # pylint: disable=import-outside-toplevel
                           QgsExpressionContextUtils, QgsGeometry, QgsMapSettings, QgsPointXY,
                           QgsRectangle)
    if not _STATE:
        return QgsExpressionContextScope("Map Settings")
    from qgis.PyQt.QtCore import QSize  # pylint: disable=import-outside-toplevel
    xmin, ymin, xmax, ymax = _STATE["rect"]
    rect = QgsRectangle(xmin, ymin, xmax, ymax)
    settings = QgsMapSettings()
    settings.setDestinationCrs(_crs())
    settings.setOutputDpi(96)
    width = 1280
    settings.setOutputSize(QSize(width, max(1, round(width * rect.height() / max(rect.width(), 1e-12)))))
    settings.setExtent(rect)
    result = QgsExpressionContextUtils.mapSettingsScope(settings)
    # QgsMapSettings fits the extent to the image: the exact view instead.
    result.setVariable("map_extent", QgsGeometry.fromRect(rect), True)
    result.setVariable("map_extent_center", QgsGeometry.fromPointXY(QgsPointXY(*_STATE["centre"])), True)
    result.setVariable("map_extent_width", rect.width(), True)
    result.setVariable("map_extent_height", rect.height(), True)
    result.setVariable("map_rotation", 0.0, True)
    result.setVariable("map_id", "canvas", True)
    result.setVariable("map_layer_ids", list(_STATE["layer_ids"]), True)
    # Layer objects cannot cross threads or processes: their ids, as layer
    # functions take either.
    result.setVariable("map_layers", list(_STATE["layer_ids"]), True)
    for name in _PER_ZOOM:  # bound per zoom elsewhere; unbound stays NULL
        result.removeVariable(name)
    for name in result.variableNames():
        result.setVariable(name, result.variable(name), True)
    return result


def _crs():
    from qgis.core import QgsCoordinateReferenceSystem  # pylint: disable=import-outside-toplevel
    crs = QgsCoordinateReferenceSystem(_STATE["crs"]) if _STATE.get("crs") else None
    return crs if crs is not None and crs.isValid() else QgsCoordinateReferenceSystem.fromWkt(_STATE["wkt"])


def context(*scopes):
    """A QgsExpressionContext: the global scope, ``scopes``, then the view."""
    from qgis.core import QgsExpressionContext, QgsExpressionContextUtils  # pylint: disable=import-outside-toplevel
    result = QgsExpressionContext()
    result.appendScope(QgsExpressionContextUtils.globalScope())
    for item in scopes:
        result.appendScope(item)
    result.appendScope(scope())
    return result


def _variables(expression) -> Set[str]:
    if not expression:
        return set()
    from qgis.core import QgsExpression  # pylint: disable=import-outside-toplevel
    parsed = expression if isinstance(expression, QgsExpression) else QgsExpression(str(expression))
    if parsed.hasParserError():
        return set()
    return set(parsed.referencedVariables())


def view_reads(expression) -> Set[str]:
    """The view variables that move with the map which ``expression`` reads."""
    return _variables(expression) & EXTENT_VARIABLES


def render_only_reads(expression) -> Set[str]:
    """The variables only set while QGIS draws which ``expression`` reads."""
    return _variables(expression) & RENDER_ONLY_VARIABLES


def describe() -> str:
    """Where the starting view is, for messages."""
    if not _STATE:
        return "no export extent"
    crs = _crs()
    x, y = _STATE["centre"]
    return f"centre {x:.6g}, {y:.6g} {crs.authid() or crs.description()}"
