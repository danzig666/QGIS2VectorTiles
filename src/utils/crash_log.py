"""
crash_log.py

``export_log.txt`` in the export folder: every progress message, flushed as it
is written, plus Python's ``faulthandler`` traceback of all threads if QGIS
crashes (segfault, access violation, abort). QGIS closes without a message on
a native crash, and the Processing log is lost with it; this file is not.
"""

import faulthandler
import platform
import sys
import threading
from datetime import datetime
from os.path import join

FILE_NAME = "export_log.txt"

_lock = threading.Lock()
_handle = None
_previous_faulthandler = None


def start(folder: str, header: str = "") -> str:
    """Open the log in ``folder`` and route crash tracebacks to it."""
    global _handle, _previous_faulthandler  # pylint: disable=global-statement
    stop()
    path = join(folder, FILE_NAME)
    try:
        handle = open(path, "a", encoding="utf-8", buffering=1)  # pylint: disable=consider-using-with
    except OSError:
        return ""
    with _lock:
        _handle = handle
    note(f"QGIS2VectorTiles (fork) export log. Python {platform.python_version()}, "
         f"{platform.platform()}")
    if header:
        note(header)
    try:
        _previous_faulthandler = faulthandler.is_enabled()
        faulthandler.enable(file=handle, all_threads=True)
        note("Crash tracebacks are written to this file.")
    except (RuntimeError, ValueError, OSError, AttributeError):
        _previous_faulthandler = None
    return path


def note(message: str) -> None:
    """Append one timestamped line (any thread; no-op without a log)."""
    stamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    with _lock:
        if _handle is None:
            return
        try:
            _handle.write(f"{stamp} [{threading.current_thread().name}] {message}\n")
            _handle.flush()
        except (OSError, ValueError):
            pass


def stop() -> None:
    """Close the log; give crash tracebacks back to stderr if they went there."""
    global _handle, _previous_faulthandler  # pylint: disable=global-statement
    with _lock:
        handle, _handle = _handle, None
    if handle is None:
        return
    try:
        if faulthandler.is_enabled():
            faulthandler.disable()
        if _previous_faulthandler and sys.stderr is not None:
            faulthandler.enable(file=sys.stderr, all_threads=True)
    except (RuntimeError, ValueError, OSError, AttributeError):
        pass
    _previous_faulthandler = None
    try:
        handle.close()
    except OSError:
        pass
