"""
export_worker_main.py

A worker process of the rule export (see export_workers.py), started by the
main QGIS with QGIS's own Python: a headless QGIS with Processing, then the
plugin's worker loop on stdin / stdout. Its own output goes to stderr (a log
file); stdout carries only the protocol.
"""

import importlib
import json
import os
import sys
import types


def main() -> None:
    protocol_in = os.fdopen(os.dup(0), "rb")
    protocol_out = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)  # anything else written to stdout (QGIS, GDAL) goes to the log
    sys.stdout = sys.stderr
    for path in json.loads(os.environ.get("Q2VT_WORKER_PATH", "[]")):
        if path not in sys.path:
            sys.path.append(path)
    from qgis.core import QgsApplication  # pylint: disable=import-outside-toplevel
    if os.environ.get("Q2VT_WORKER_PREFIX"):
        QgsApplication.setPrefixPath(os.environ["Q2VT_WORKER_PREFIX"], True)
    app = QgsApplication([], False)
    app.initQgis()
    from processing.core.Processing import Processing  # pylint: disable=import-outside-toplevel
    Processing.initialize()
    # The plugin package under the main process's name, without running the
    # plugin's own __init__ (it registers the plugin in QGIS).
    package, root = os.environ["Q2VT_WORKER_PACKAGE"], os.environ["Q2VT_WORKER_ROOT"]
    if package not in sys.modules:
        module = types.ModuleType(package)
        module.__path__ = [root]
        sys.modules[package] = module
    workers = importlib.import_module(f"{package}.src.core.export_workers")
    workers.serve(protocol_in, protocol_out)
    protocol_out.flush()
    sys.stderr.flush()
    os._exit(0)  # pylint: disable=protected-access  # nothing to keep: no exitQgis


if __name__ == "__main__":
    main()
