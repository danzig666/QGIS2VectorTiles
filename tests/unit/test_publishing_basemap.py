"""Vector basemap (PUB-17): region extract from a Protomaps-schema PMTiles
(local file or HTTP byte ranges, only the area's tiles), label characters
for glyph generation, flavor styles without sprites and with generated font
stacks, build discovery and failure modes."""

import io
import json
import os

import pytest

from publishing import basemap
from publishing.errors import PublishingError
from publishing.preview_server import PreviewServer
from publishing.validation import open_pmtiles, validate_pmtiles, vector_only_violations
from publishing_fixtures import protomaps_planet

EXTENT = (2119000.0, 6019000.0, 2123000.0, 6023000.0)  # ~ Budapest, EPSG:3857


@pytest.fixture(scope="module")
def planet(tmp_path_factory):
    return protomaps_planet(str(tmp_path_factory.mktemp("planet") / "planet.pmtiles"))


def _extract(reader, out, max_zoom=14, **kwargs):
    detail, overview = basemap.areas(EXTENT, 0.5, kwargs.pop("overview_km", 100.0))
    keys = basemap.text_keys(basemap.load_flavor("light", "hu")["layers"])
    return basemap.extract(reader, str(out), detail, overview, max_zoom, 7, keys, **kwargs)


def test_local_extract_copies_only_the_area(planet, tmp_path):
    reader = basemap.LocalReader(planet)
    result = _extract(reader, tmp_path / "basemap.pmtiles")
    reader.close()
    d = result.descriptor
    summary = validate_pmtiles(d.path, sample=0)
    assert summary["maxZoom"] == 14 and summary["minZoom"] == 0
    detail, overview = basemap.areas(EXTENT, 0.5, 100.0)
    with open_pmtiles(planet) as source:
        available = {address for address, _ in source.tiles()}
    expected = set(basemap.wanted_tiles(detail, overview, 14, 7)) & available
    assert d.addressed_tiles == len(expected) < len(available)
    with open_pmtiles(d.path) as archive:
        meta = archive.metadata()
        payloads = {address: data for address, data in archive.tiles()}
    assert {l["id"] for l in meta["vector_layers"]} >= {"earth", "roads", "places"}
    # Same payloads as the source (no re-encoding).
    with open_pmtiles(planet) as source:
        original = dict(source.tiles())
    assert all(payloads[a] == original[a] for a in payloads)
    assert {"Ő", "ó", "Arló"[2], "F"} <= result.characters


def test_http_extract_uses_byte_ranges(planet, tmp_path):
    with PreviewServer(os.path.dirname(planet)) as server:
        reader = basemap.HttpReader(server.url("planet.pmtiles"))
        result = _extract(reader, tmp_path / "basemap.pmtiles", max_zoom=12)
        ranges = [r for r in server.requests if r["path"].endswith("planet.pmtiles")]
    assert ranges and all(r["status"] == 206 for r in ranges)
    assert sum(r["bytes"] for r in ranges) < os.path.getsize(planet)
    assert result.descriptor.max_zoom == 12 and result.requests == len(ranges)


class _Response(io.BytesIO):
    def __init__(self, data, status=206):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_http_reader_retries_and_refuses_whole_file_answers():
    import urllib.error
    calls = []

    def flaky(request, timeout):
        calls.append(request.headers["Range"])
        if len(calls) < 3:
            raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, None)
        return _Response(b"abcd")
    reader = basemap.HttpReader("https://example.test/x.pmtiles", opener=flaky, sleep=lambda s: None)
    assert reader.get(10, 4) == b"abcd" and calls == ["bytes=10-13"] * 3
    whole = basemap.HttpReader("https://example.test/x.pmtiles",
                               opener=lambda r, timeout: _Response(b"a" * 100, status=200))
    with pytest.raises(PublishingError) as error:
        whole.get(0, 4)
    assert error.value.code == "Q2VT_PUB_RANGE_UNSUPPORTED"


def test_latest_build_is_discovered():
    builds = [{"key": "20260929.pmtiles"}, {"key": "20261001.pmtiles"}, {"key": "notes.txt"}]
    url = basemap.latest_build_url(lambda r, timeout: _Response(json.dumps(builds).encode(), 200))
    assert url == "https://build.protomaps.com/20261001.pmtiles"


def test_wrong_schema_and_too_many_tiles_are_refused(planet, tmp_path):
    from publishing_fixtures import make_mbtiles, pyramid  # noqa
    from publishing.pmtiles_builder import build_pmtiles
    other = build_pmtiles(make_mbtiles(str(tmp_path / "o.mbtiles"), pyramid(2)), str(tmp_path / "o.pmtiles"))
    with pytest.raises(PublishingError, match="Protomaps schema"):
        _extract(basemap.LocalReader(other.path), tmp_path / "a.pmtiles")
    with pytest.raises(PublishingError, match="limit"):
        _extract(basemap.LocalReader(planet), tmp_path / "b.pmtiles", max_tiles=10)


@pytest.mark.parametrize("flavor", ["light", "dark", "white", "grayscale", "black"])
@pytest.mark.parametrize("locale", ["hu", "en"])
def test_flavors_are_vector_only_without_sprites(flavor, locale):
    doc = basemap.load_flavor(flavor, locale)
    layers = basemap.prepare_flavor(doc, basemap.font_map())
    text = json.dumps(layers)
    assert "icon-image" not in text and "Noto Sans" not in text and '"protomaps"' not in text
    assert all(l["id"].startswith(basemap.LAYER_PREFIX) for l in layers)
    assert all(l.get("source", basemap.SOURCE_ID) == basemap.SOURCE_ID for l in layers)
    style = {"sources": {basemap.SOURCE_ID: {"type": "vector"}}, "layers": layers}
    assert not vector_only_violations(style)
    assert basemap.used_fontstacks(layers) <= set(basemap.FONTSTACKS.values())
    assert len(layers) > 40 and doc["colors"]["background"].startswith("#")


def test_line_geometry_and_streets_from_the_roads_layer(tmp_path):
    from publishing import mvt  # pylint: disable=import-outside-toplevel
    from publishing.basemap import STREETS_LAYER, LocalReader, street_records  # pylint: disable=import-outside-toplevel
    from publishing_fixtures import encode_tile, protomaps_planet  # pylint: disable=import-outside-toplevel
    tile = encode_tile({"roads": [("line", [[0, 10], [100, 10], [100, 50]], {"name": "A"}, None)],
                        "earth": [("polygon", [[[0, 0], [10, 0], [10, 10], [0, 0]]], {}, None)]})
    roads = mvt.decode(tile, geometry=True, layers_wanted={"roads"})
    assert list(roads) == ["roads"]
    assert mvt.lines(roads["roads"]["features"][0]["geometry"]) == [[(0, 10), (100, 10), (100, 50)]]
    ring = mvt.decode(tile, geometry=True)["earth"]["features"][0]["geometry"]
    assert mvt.lines(ring)[0][0] == mvt.lines(ring)[0][-1]  # ClosePath repeats the first point
    planet = protomaps_planet(str(tmp_path / "planet.pmtiles"))
    reader = LocalReader(planet)
    try:
        everything = street_records(reader, (18.95, 47.45, 19.05, 47.5))
        west_half = street_records(reader, (18.95, 47.45, 19.05, 47.5),
                                   clip=lambda line: [[p for p in line if p[0] <= 19.0]])
    finally:
        reader.close()
    # One "Fő utca" per tile row: rows 1.6 km apart are separate streets, the
    # tiles of one row join (pieces within 300 m).
    assert {r["label"] for r in everything} == {"Fő utca"} and len(everything) >= 3
    assert all(r["layerId"] == STREETS_LAYER and len(r["anchor"]) == 2 for r in everything)
    assert len({r["featureKey"] for r in everything}) == len(everything)
    assert all(r["bounds"][2] <= 19.0 + 1e-6 for r in west_half)
