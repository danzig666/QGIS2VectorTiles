"""Maximum heatmap density (fidelity.heatmap): exact, and in bounded time
when a screen radius holds the whole layer (exports from zoom 0)."""

import random
import time

import pytest

from fidelity import heatmap as hm


def _brute_max(points, radius, at):
    return max(hm.quartic_density(points, xy, radius) for xy in at)


def _samples(points, radius, samples=hm.MAX_SAMPLES):
    """The positions max_density evaluates (data points and grid steps)."""
    index = {}
    for x, y, _ in points:
        key = (int(x // radius), int(y // radius))
        index[key] = index.get(key, 0) + 1
    step = max(1, len(points) // samples)
    at = [points[i][:2] for i in range(0, len(points), step)]
    for cx, cy in sorted(index, key=lambda cell: -index[cell])[:max(1, samples // 16)]:
        at.extend(((cx + (i + 0.5) / 4) * radius, (cy + (j + 0.5) / 4) * radius)
                  for i in range(4) for j in range(4))
    return at


@pytest.mark.parametrize("seed", range(6))
def test_max_density_is_the_point_by_point_sum(seed):
    rng = random.Random(seed)
    origin = (rng.uniform(-3e6, 3e6), rng.uniform(-3e6, 3e6))
    span = rng.choice([50.0, 2000.0])
    points = [(origin[0] + rng.gauss(0, span / 8), origin[1] + rng.uniform(0, span),
               rng.choice([0.0, 0.5, 1.0, 3.0])) for _ in range(rng.choice([1, 40, 600]))]
    for radius in (span / 200, span / 10, span / 2, span * 20):
        expected = _brute_max(points, radius, _samples(points, radius))
        assert hm.max_density(points, radius) == pytest.approx(expected, rel=1e-9, abs=1e-12)


def test_zoomed_out_max_density_does_not_visit_every_point():
    """Zoomed out, a 10 mm radius holds all 20 000 points: every sample
    summed every point (about 13 s per zoom, eleven zooms from zoom 0)."""
    rng = random.Random(1)
    points = [(2.1e6 + rng.random() * 4000, 6.0e6 + rng.random() * 4000, 1.0)
              for _ in range(20000)]
    started = time.time()
    tops = [hm.max_density(points, 10 * 96 / 25.4 * hm.map_units_per_css_px(zoom))
            for zoom in range(0, 9)]
    elapsed = time.time() - started
    # Nearly every point counts fully at the densest spot.
    assert all(19000 < top <= 20000 for top in tops)
    assert elapsed < 5, elapsed


def test_map_radius_density_is_evaluated_once():
    """A map-unit radius is the same at every zoom: one evaluation."""
    calls = []
    original = hm.max_density

    def counting(points, radius, samples=hm.MAX_SAMPLES):
        calls.append(radius)
        return original(points, radius, samples)

    class Ramp:  # pylint: disable=too-few-public-methods
        @staticmethod
        def color(_):
            class Color:  # pylint: disable=too-few-public-methods
                red = green = blue = staticmethod(lambda: 0)
                alphaF = staticmethod(lambda: 1.0)
            return Color()

    hm.max_density = counting
    try:
        spec = hm.build_spec(Ramp(), 25.0, "map", 0.0, [(0.0, 0.0, 1.0), (10.0, 0.0, 1.0)],
                             range(0, 25))
    finally:
        hm.max_density = original
    assert len(calls) == 1
    assert [zoom for zoom, _ in spec["intensity_stops"]] == list(range(0, 25))
