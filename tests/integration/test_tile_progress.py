"""Progress bar of an export (owner report: it sat at 85% for the whole 15
minutes of tiling). ogr2ogr reports no progress, so TileProgress estimates
it from the jobs' costs and elapsed time; every stage of a publication has
its own share of the bar, which only moves forward."""

import os
import sys

from qgis.core import QgsProcessingFeedback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_export_cache import _setup  # noqa: E402  pylint: disable=wrong-import-position
from test_publishing_pipeline import EXTENT  # noqa: E402  pylint: disable=wrong-import-position


def test_tile_progress_moves_while_jobs_run_and_never_goes_back(plugin):
    from q2vt_plugin.src.core import tiles_generator as tg  # pylint: disable=import-error
    tracker = tg.TileProgress([100.0, 300.0])
    rate = tg._SECONDS_PER_COST  # pylint: disable=protected-access
    tracker.start(0, 0.0)
    tracker.start(1, 0.0)
    assert tracker.fraction(0.0) == 0.0
    mid = tracker.fraction(150 * rate)  # job 0 past its time, job 1 half way
    assert 0.3 < mid < 0.9
    tracker.finish(0, 200 * rate)  # slower than the guess: the rate is learnt
    assert abs(tracker.rate() - 2 * rate) < 1e-15
    assert tracker.fraction(200 * rate) >= mid  # never backwards
    assert tracker.fraction(10 ** 6) < 1.0  # still running: never full
    tracker.finish(1, 10 ** 6)
    assert tracker.fraction(10 ** 6) == 1.0


def test_tile_progress_follows_what_ogr2ogr_reports(plugin):
    """Debrecen: the bar sat at 45% for four minutes while the big pieces
    ran longer than their cost guessed. ogr2ogr -progress (features written)
    moves it; the tiles are put together after the last feature."""
    import io  # pylint: disable=import-outside-toplevel
    from q2vt_plugin.src.core import tiles_generator as tg  # pylint: disable=import-error
    tracker = tg.TileProgress([100.0, 100.0], reporting=True)
    tracker.start(0, 0.0)
    tracker.start(1, 0.0)
    assert tracker.fraction(1000.0) == 0.0  # nothing written yet: no guess
    tracker.report(0, 0.5, 100.0)
    tracker.report(1, 0.2, 100.0)
    assert abs(tracker.fraction(100.0) - 0.85 * 0.35) < 1e-9
    tracker.report(0, 1.0, 200.0)  # job 0 writes its tiles
    assembling = [tracker.fraction(t) for t in (200.0, 230.0, 1000.0)]
    assert assembling == sorted(assembling) and assembling[-1] < 0.5 * (0.99 + 0.85 * 0.2) + 1e-9
    tracker.finish(0, 1000.0)
    tracker.finish(1, 1000.0)
    assert tracker.fraction(1000.0) == 1.0
    reader = tg._OutputReader(io.StringIO("0...10...20...30.."), True)  # pylint: disable=protected-access
    reader.run()
    assert reader.fraction == 0.35
    reader = tg._OutputReader(io.StringIO("0...10...20...30...40...50...60...70...80...90...100 - done.\n"), True)  # pylint: disable=protected-access
    reader.run()
    assert reader.fraction == 1.0
    errors = tg._OutputReader(io.StringIO("ERROR 1: x\n" * 5000), False)  # pylint: disable=protected-access
    errors.run()
    assert errors.text.endswith("ERROR 1: x\n") and len(errors.text) == 20000


class Bar(QgsProcessingFeedback):
    def __init__(self):
        super().__init__()
        self.events = []  # ("bar", percent) and ("log", line), in order

    def setProgress(self, value):  # noqa: N802
        super().setProgress(value)
        self.events.append(("bar", value))

    def pushInfo(self, info):  # noqa: N802
        self.events.append(("log", info))


def test_publication_bar_only_moves_forward(plugin, tmp_path):
    from publishing.controller import export_local  # pylint: disable=import-outside-toplevel
    project, profile, _parcels, _zones = _setup(tmp_path)
    profile.output.reuse_unchanged = False  # tiles are generated
    bar = Bar()
    export_local(project, profile, EXTENT, bar)
    values = [v for kind, v in bar.events if kind == "bar"]
    assert values and all(b >= a for a, b in zip(values, values[1:])), values
    assert values[-1] >= 99 and values[0] < 5

    def bar_at(line):
        index = next(i for i, (kind, v) in enumerate(bar.events) if kind == "log" and v == line)
        return max([v for kind, v in bar.events[:index] if kind == "bar"] or [0])
    # The vector tiles get most of the bar (was: 85% at their start): the
    # datasets, then (4.28) the tiles, made in the background meanwhile and
    # waited for at TILES.
    start, end = bar_at(". Exporting rules to datasets..."), bar_at(". Generating tiles...")
    assert start < 10 and end - start > 25, (start, end)
    start, end = bar_at("[TILES]"), bar_at("[BUILD_RELEASE]")
    assert end - start > 40, (start, end)


_FLAKY_JOB = r'''
import os, sys
marker, output = sys.argv[1], sys.argv[2]
if os.path.exists(output):
    sys.exit("the partial output of the failed try was left")
if not os.path.exists(marker) or sys.argv[3] == "always":
    open(marker, "w").close()
    open(output, "w").write("partial")
    sys.stderr.write("ERROR 1: IllegalArgumentException: encountered NaN/Inf numbers\n")
    sys.exit(1)
print("0...10...20...30...40...50...60...70...80...90...100 - done.")
open(output, "w").write("tiles")
'''


def test_a_failed_tile_job_runs_once_more(plugin, tmp_path):
    """ogr2ogr once stopped on a dataset it tiled in every other run (GEOS
    "NaN/Inf numbers"): a failed job runs once more; a second failure fails
    the export as before."""
    import sys as _sys  # pylint: disable=import-outside-toplevel
    import pytest  # pylint: disable=import-outside-toplevel
    from q2vt_plugin.src.core import tiles_generator as tg  # pylint: disable=import-error
    script = tmp_path / "job.py"
    script.write_text(_FLAKY_JOB)
    feedback = Bar()
    generator = tg.GDALTilesGenerator([], {}, str(tmp_path), None, 100, feedback)
    output = tmp_path / "piece.mbtiles"
    generator._run_parallel([[_sys.executable, str(script), str(tmp_path / "tried"), str(output), "once"]],  # pylint: disable=protected-access
                            [1.0], ["layer"])
    assert output.read_text() == "tiles"
    assert any(kind == "log" and "running it once more" in text and "NaN/Inf" in text
               for kind, text in feedback.events)
    assert generator.layer_pieces == {"layer": 1}
    output.unlink()
    with pytest.raises(RuntimeError, match="NaN/Inf"):
        generator._run_parallel([[_sys.executable, str(script), str(tmp_path / "tried"), str(output), "always"]],  # pylint: disable=protected-access
                                [1.0], ["layer"])
