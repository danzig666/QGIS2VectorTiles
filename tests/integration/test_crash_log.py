"""export_log.txt: progress lines flushed as written, crash tracebacks routed to it."""

import faulthandler
import os
import threading


def test_notes_are_flushed_and_tracebacks_routed(plugin, tmp_path):
    from q2vt_plugin.src.utils import crash_log  # pylint: disable=import-error
    path = crash_log.start(str(tmp_path), "header line")
    try:
        assert faulthandler.is_enabled()
        crash_log.note("main step")
        worker = threading.Thread(target=crash_log.note, args=("worker step",), name="w1")
        worker.start()
        worker.join()
        text = open(path, encoding="utf-8").read()  # readable before the log closes
        assert "header line" in text and "main step" in text
        assert "[w1] worker step" in text
    finally:
        crash_log.stop()
    crash_log.note("after stop")  # no-op
    assert "after stop" not in open(path, encoding="utf-8").read()
    assert os.path.basename(path) == "export_log.txt"
