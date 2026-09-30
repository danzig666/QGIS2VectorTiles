"""Shared pytest configuration for QGIS2VectorTiles.

Three test levels (see docs/fidelity/TESTING.md):

* ``tests/unit``        - pure Python, no QGIS required.
* ``tests/integration`` - PyQGIS required; skipped automatically without it.
* ``tests/browser``     - Node + Playwright + Chromium; skipped without them.

The plugin repository root is itself the plugin package (``__init__.py`` with
``from .src...`` imports), so it is registered under the stable package name
``q2vt_plugin`` regardless of the checkout directory's name.
"""

import importlib.util
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIDELITY_PARENT = os.path.join(REPO_ROOT, "src", "core")

# Pure-Python fidelity package: importable as ``fidelity`` without QGIS.
if FIDELITY_PARENT not in sys.path:
    sys.path.insert(0, FIDELITY_PARENT)


def _qgis_available() -> bool:
    try:
        import qgis.core  # noqa: F401  pylint: disable=import-outside-toplevel,unused-import
    except Exception:  # noqa: BLE001
        return False
    return True


QGIS_AVAILABLE = _qgis_available()


def init_qgis():
    """Start one headless QgsApplication and the Processing framework."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from qgis.core import QgsApplication  # pylint: disable=import-outside-toplevel

    if QgsApplication.instance() is None:
        app = QgsApplication([], False)
        app.initQgis()
        init_qgis.app = app  # keep a reference for the process lifetime
    for candidate in ("/usr/share/qgis/python/plugins", "/usr/lib/qgis/python/plugins"):
        if os.path.isdir(candidate) and candidate not in sys.path:
            sys.path.append(candidate)
    try:
        from processing.core.Processing import Processing  # pylint: disable=import-outside-toplevel

        Processing.initialize()
    except ImportError:
        pass


def register_plugin_package():
    """Import the plugin root as ``q2vt_plugin`` (requires QGIS).

    The pure ``fidelity`` package is aliased to the plugin's copy so tests
    and plugin code share the same classes (enums, dataclasses).
    """
    if "q2vt_plugin" in sys.modules:
        return sys.modules["q2vt_plugin"]
    init_qgis()
    spec = importlib.util.spec_from_file_location(
        "q2vt_plugin",
        os.path.join(REPO_ROOT, "__init__.py"),
        submodule_search_locations=[REPO_ROOT],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["q2vt_plugin"] = module
    spec.loader.exec_module(module)
    prefix = "q2vt_plugin.src.core.fidelity"
    for name, mod in list(sys.modules.items()):
        if name == prefix or name.startswith(prefix + "."):
            sys.modules["fidelity" + name[len(prefix):]] = mod
    return module
