"""Browser tests need QGIS (to export), Node, the pinned npm packages and Chromium."""

import os
import shutil

from q2vt_support import QGIS_AVAILABLE, register_plugin_package

HERE = os.path.dirname(os.path.abspath(__file__))
CHROMIUM = os.environ.get("Q2VT_CHROMIUM", "/opt/pw-browsers/chromium")
READY = (
    QGIS_AVAILABLE
    and shutil.which("node") is not None
    and os.path.isdir(os.path.join(HERE, "node_modules", "playwright-core"))
    and os.path.exists(CHROMIUM)
)

if not READY:
    collect_ignore_glob = ["test_*.py"]  # pylint: disable=invalid-name
else:
    register_plugin_package()
