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
    # The vector tiles get most of the bar (was: 85% at their start).
    start, end = bar_at(". Generating tiles..."), bar_at("[RECORDS]")
    assert start < 40 and end - start > 40, (start, end)
