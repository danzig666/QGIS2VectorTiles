"""
main_thread.py

The export runs on QGIS's main thread (the algorithm sets NoThreading): QGIS
only allows the project, its layer tree and the map canvas to be changed from
there. Heavy work runs in worker threads; while the main thread waits for it,
``keep_responsive()`` lets QGIS repaint and see the Cancel button.
"""

from time import monotonic

from qgis.PyQt.QtCore import QCoreApplication, QThread

_INTERVAL_S = 0.1
_last = 0.0


def on_main_thread() -> bool:
    app = QCoreApplication.instance()
    return app is not None and QThread.currentThread() == app.thread()


def app_running() -> bool:
    return QCoreApplication.instance() is not None


def keep_responsive() -> None:
    """Process pending GUI events (main thread only, at most every 0.1 s)."""
    global _last  # pylint: disable=global-statement
    now = monotonic()
    if now - _last < _INTERVAL_S or not on_main_thread():
        return
    _last = now
    QCoreApplication.processEvents()
