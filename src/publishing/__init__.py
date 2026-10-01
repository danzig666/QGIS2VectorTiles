"""
Web publishing for QGIS2VectorTiles (fork).

Pure-Python core (no QGIS imports in this package's top-level modules except
``qgis_*.py`` adapters): publication profiles, MBTiles -> PMTiles packaging of
the *same* MVT tiles, immutable local web bundles, a loopback HTTP preview
server with byte ranges, and hosting providers.

The map data stays Mapbox Vector Tiles end to end (see
docs/publishing/ARCHITECTURE.md); nothing here rasterises the map.
"""

PUBLISHING_SCHEMA_VERSION = 1
