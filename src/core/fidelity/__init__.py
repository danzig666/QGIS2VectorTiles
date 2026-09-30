"""
fidelity — typed planning, diagnostics and conversion helpers for
high-fidelity QGIS → MapLibre export.

Every module in this package is pure Python (standard library, plus Pillow
for ``patterns``/``assets``) and must not import ``qgis`` at module level, so
the conversion rules can be unit-tested without a QGIS installation. QGIS
objects are translated into plain values by thin adapters in the calling
modules (``maplibre_converter``, ``sprite_generator``, ...) or by
``qgis_adapter`` which imports QGIS lazily.
"""

FIDELITY_SCHEMA_VERSION = 1
