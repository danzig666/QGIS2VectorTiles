"""
config.py

Global constants, configuration loading, and PyQt version-specific imports.
All other modules import shared symbols from here to avoid duplication.
"""

from os.path import abspath, dirname, join

from qgis.PyQt.QtCore import qVersion


# =====================================================================
# PLUGIN PATHS
# The folder this plugin is installed in (src/utils/ is two levels down),
# whatever its name: the fork installs as QGIS2VectorTilesFork next to the
# official QGIS2VectorTiles, and a development checkout may be linked in.
# =====================================================================
_PLUGIN_DIR = dirname(dirname(dirname(abspath(__file__))))
_RESOURCES = join(_PLUGIN_DIR, "resources")
_SERVER = join(_RESOURCES, "tiles_server.py")

# =====================================================================
# SERVER / NETWORK SETTINGS
# Safe to adjust if you need a different local port.
# =====================================================================
_PORT = 9000


# =====================================================================
# DATA PROCESSING SETTINGS
# Safe to adjust to tune output quality/size vs. processing speed.
# =====================================================================
_EPSG_CRS = 3857                          # Output projection (Web Mercator)
# Geometry simplification before tiling, in tile units (1/4096 of a tile) at the
# export's max zoom: a quarter unit moves nothing beyond the tiles' own rounding.
# (It was 1 CRS unit = 1 m: each layer simplified on its own, so boundaries
# shared by two layers, e.g. a zone boundary on a parcel edge, drifted apart by
# up to ~1 m, several pixels when zoomed in.)
_DATA_SIMPLIFICATION_TOLERANCE = 0.25
_REMOVE_DUPLICATES_DISTANCE = 300         # Minimum spacing between points, in points
_TOP_SCALE = 295829355.45                 # Map scale of zoom 0 (96 DPI, Web Mercator); see fidelity/zoom.py
_SPRITE_QUALITY = 3
_FIELD_PREFIX = 'q2vt'    
_SIMPLIFICATION=1
_SIMPLIFICATION_MAX_ZOOM=0                   # Field name prefix


# =====================================================================
# MAPLIBRE GLYPH (SDF) GENERATION
# =====================================================================
# --- Format constants — fixed by the MapLibre/Mapbox glyph PBF spec. ---
# These are NOT tunable settings. MapLibre GL JS's client-side glyph
# parser hardcodes these exact values; changing them WILL break glyph
# rendering (mismatched image size errors, square/clipped halos, blurry
# text). Do not edit this block.
_GLYPH_RANGE_SIZE = 256        # Codepoints per .pbf range file (spec-fixed)
_MAX_UNICODE = 65535           # Highest codepoint MapLibre's glyph protocol supports
_MAPLIBRE_GLYPH_BORDER = 3     # MapLibre client's fixed glyph border (never changes)
_REFERENCE_EM = 24.0           # MapLibre's internal reference font size, in px
_REFERENCE_BUFFER = 3.0        # Mapbox/MapLibre reference generator (tiny-sdf) buffer default
_REFERENCE_RADIUS = 8.0        # Mapbox/MapLibre reference generator (tiny-sdf) radius default;
                                # also hardcoded client-side as the shader's SDF_PX constant

# --- Derived generation parameters — DO NOT edit individually. ---
# _SDF_RADIUS and _BUFFER are mathematically dependent on _REFERENCE_RADIUS,
# _REFERENCE_BUFFER, and _FONT_RENDER_SIZE above. Changing one without
# recalculating the others reproduces bugs already solved (mismatched
# image size, blurry/rectangular halos). If _FONT_RENDER_SIZE ever needs
# to change, _SDF_RADIUS and _BUFFER must be recomputed using the
# formulas in the comments below — do not just edit the numbers.
_FONT_RENDER_SIZE = 24         # Source rasterization point size
_SDF_CUTOFF = 0.25             # MapLibre/Mapbox spec: outline lands at byte ~192
_SUPERSAMPLE = 4               # Internal antialiasing supersample factor
_SDF_RADIUS = 8.0              # = _REFERENCE_RADIUS * (_FONT_RENDER_SIZE / _REFERENCE_EM)
_BUFFER = 10                   # = ceil(max(_REFERENCE_BUFFER * scale, _SDF_RADIUS + 2, _MAPLIBRE_GLYPH_BORDER))
_SDF_COVERAGE_THRESHOLD = 127  # AA coverage midpoint used to binarize the glyph mask
_MAPLIBRE_LABELS_FACTOR = 1.0   # Measured parity with QGIS after the glyph-metric and zoom-scale fixes (was an empirical 1.4).

# =====================================================================
# PyQt VERSION GUARD
# Imports the right Qt5 / Qt6 symbols once, re-exported below.
# =====================================================================

from qgis.PyQt.QtXml import QDomDocument
from qgis.PyQt.QtCore import QVariant, Qt
from qgis.PyQt import sip



__all__ = [
    # Plugin paths
    "_PLUGIN_DIR",
    "_RESOURCES",
    "_SERVER",
    # Server / network
    "_PORT",
    # Data processing
    "_EPSG_CRS",
    "_DATA_SIMPLIFICATION_TOLERANCE",
    "_REMOVE_DUPLICATES_DISTANCE",
    "_TOP_SCALE",
    "_SPRITE_QUALITY",
    "_FIELD_PREFIX",
    "_SIMPLIFICATION",
    "_SIMPLIFICATION_MAX_ZOOM",
    # Glyph generation
    "_GLYPH_RANGE_SIZE",
    "_MAX_UNICODE",
    "_MAPLIBRE_GLYPH_BORDER",
    "_REFERENCE_EM",
    "_REFERENCE_BUFFER",
    "_REFERENCE_RADIUS",
    "_FONT_RENDER_SIZE",
    "_SDF_CUTOFF",
    "_SUPERSAMPLE",
    "_SDF_RADIUS",
    "_BUFFER",
    "_SDF_COVERAGE_THRESHOLD",
    "_MAPLIBRE_LABELS_FACTOR",
    # PyQt re-exports
    "Qt",
    "QDomDocument",
    "QVariant",
    "sip",
]