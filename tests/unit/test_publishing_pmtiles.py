"""MBTiles -> PMTiles packaging (PUB-05): the same MVT tiles at the same XYZ
addresses, MVT-only checks, cancellation, immutability, edge cases."""

import gzip
import hashlib
import os
import shutil
import sqlite3
import subprocess

import pytest

from publishing import mvt
from publishing.errors import Cancelled, PublishingError
from publishing.pmtiles_builder import PmtilesOptions, build_pmtiles, preflight_mbtiles
from publishing.validation import (compare_archives, open_pmtiles, validate_pmtiles)
from publishing.vendor.pmtiles.tile import Compression, TileType, zxy_to_tileid
from publishing_fixtures import encode_tile, make_mbtiles, pyramid, sample_tile


def _digest(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


@pytest.mark.parametrize("compress", ["gzip", "none", "mixed"])
def test_every_tile_and_payload_survives(tmp_path, compress):
    """A02: identical XYZ address sets and decompressed MVT bytes."""
    tiles = pyramid(4)  # 341 tiles, zoom 0 included
    source = make_mbtiles(str(tmp_path / "in.mbtiles"), tiles, compress=compress)
    before = _digest(source)
    archive = build_pmtiles(source, str(tmp_path / "out.pmtiles"),
                            PmtilesOptions(sample_tiles=0))
    assert _digest(source) == before  # input untouched
    assert compare_archives(source, archive.path) == {"tiles": len(tiles)}
    assert archive.addressed_tiles == len(tiles) and archive.min_zoom == 0 and archive.max_zoom == 4
    with open_pmtiles(archive.path) as pm:
        assert pm.header["tile_type"] == TileType.MVT
        assert pm.header["tile_compression"] == Compression.GZIP
        found = {address: mvt.payload(data) for address, data in pm.tiles()}
        layers = sorted(layer["id"] for layer in pm.metadata()["vector_layers"])
    assert found == tiles  # XYZ rows, not TMS
    assert layers == ["lines", "points", "polygons"]


def test_tms_rows_become_xyz(tmp_path):
    tiles = {(3, 1, 2): sample_tile(3, 1, 2)}
    source = make_mbtiles(str(tmp_path / "one.mbtiles"), tiles)
    with sqlite3.connect(source) as conn:  # stored as TMS row 8 - 1 - 2 = 5
        assert conn.execute("SELECT tile_row FROM tiles").fetchone()[0] == 5
    archive = build_pmtiles(source, str(tmp_path / "one.pmtiles"))
    with open_pmtiles(archive.path) as pm:
        [(address, data)] = list(pm.tiles())
    assert address == (3, 1, 2)
    assert mvt.decode(data)["points"]["features"][0]["properties"]["zxy"] == "3/1/2"


def test_sparse_coverage_and_duplicate_payloads(tmp_path):
    tiles = pyramid(6, sparse=True)
    tiles[(6, 10, 11)] = tiles[(6, 10, 10)]  # identical payload: stored once
    source = make_mbtiles(str(tmp_path / "s.mbtiles"), tiles)
    archive = build_pmtiles(source, str(tmp_path / "s.pmtiles"))
    assert archive.addressed_tiles == len(tiles) and archive.tile_contents == len(tiles) - 1
    compare_archives(source, archive.path)


def test_unicode_and_spaces_in_paths(tmp_path):
    folder = tmp_path / "Térkép mappa" / "ő ű"
    folder.mkdir(parents=True)
    source = make_mbtiles(str(folder / "csempék 1.mbtiles"), pyramid(2))
    archive = build_pmtiles(source, str(folder / "térkép.pmtiles"))
    compare_archives(source, archive.path)


def test_raster_mbtiles_refused(tmp_path):
    source = make_mbtiles(str(tmp_path / "r.mbtiles"), {(0, 0, 0): b"\x89PNG\r\n\x1a\n"},
                          metadata={"format": "png"}, compress="none")
    with pytest.raises(PublishingError) as error:
        build_pmtiles(source, str(tmp_path / "r.pmtiles"))
    assert error.value.code == "Q2VT_PUB_NOT_MVT"
    assert not (tmp_path / "r.pmtiles").exists()


def test_non_mvt_payload_refused_even_with_pbf_format(tmp_path):
    source = make_mbtiles(str(tmp_path / "bad.mbtiles"), {(0, 0, 0): b"\x89PNG\r\n\x1a\nxxxx"})
    with pytest.raises(PublishingError) as error:
        build_pmtiles(source, str(tmp_path / "bad.pmtiles"))
    assert error.value.code == "Q2VT_PUB_NOT_MVT"


def test_empty_and_malformed_inputs(tmp_path):
    empty = make_mbtiles(str(tmp_path / "e.mbtiles"), {(0, 0, 0): sample_tile(0, 0, 0)})
    with sqlite3.connect(empty) as conn:
        conn.execute("DELETE FROM tiles")
    with pytest.raises(PublishingError) as error:
        build_pmtiles(empty, str(tmp_path / "e.pmtiles"))
    assert error.value.code == "Q2VT_PUB_EMPTY_ARCHIVE"
    dup = make_mbtiles(str(tmp_path / "d.mbtiles"), {(1, 0, 0): sample_tile(1, 0, 0)}, unique=False)
    with sqlite3.connect(dup) as conn:
        conn.execute("INSERT INTO tiles SELECT * FROM tiles")
    with pytest.raises(PublishingError, match="Duplicate"):
        build_pmtiles(dup, str(tmp_path / "d.pmtiles"))
    with pytest.raises(PublishingError):
        build_pmtiles(str(tmp_path / "missing.mbtiles"), str(tmp_path / "m.pmtiles"))


def test_invalid_metadata_is_repaired_not_trusted(tmp_path):
    source = make_mbtiles(str(tmp_path / "m.mbtiles"), pyramid(3),
                          metadata={"bounds": "nonsense", "json": "{broken", "center": None})
    archive = build_pmtiles(source, str(tmp_path / "m.pmtiles"))
    assert any("bounds" in w for w in archive.warnings)
    assert any("json" in w for w in archive.warnings)
    assert set(archive.vector_layers) == {"points", "lines", "polygons"}
    assert archive.bounds[0] == pytest.approx(-180)


def test_truncated_vector_layers_are_completed(tmp_path):
    source = make_mbtiles(str(tmp_path / "t.mbtiles"), pyramid(2),
                          metadata={"json": '{"vector_layers": [{"id": "points", "fields": {}}]}'})
    archive = build_pmtiles(source, str(tmp_path / "t.pmtiles"))
    with open_pmtiles(archive.path) as pm:
        layers = {layer["id"]: layer for layer in pm.metadata()["vector_layers"]}
    assert set(layers) == {"points", "lines", "polygons"}
    assert "parcel" in layers["lines"]["fields"]


def test_existing_archive_is_never_overwritten(tmp_path):
    source = make_mbtiles(str(tmp_path / "i.mbtiles"), pyramid(1))
    target = tmp_path / "live.pmtiles"
    target.write_bytes(b"live archive")
    with pytest.raises(PublishingError):
        build_pmtiles(source, str(target))
    assert target.read_bytes() == b"live archive"


def test_cancellation_cleans_up(tmp_path):
    source = make_mbtiles(str(tmp_path / "c.mbtiles"), pyramid(5))
    calls = {"n": 0}

    class Feedback:
        def isCanceled(self):  # noqa: N802
            calls["n"] += 1
            return calls["n"] > 1

        def setProgress(self, value):  # noqa: N802
            pass

    with pytest.raises(Cancelled):
        build_pmtiles(source, str(tmp_path / "c.pmtiles"), feedback=Feedback())
    assert sorted(os.listdir(tmp_path)) == ["c.mbtiles"]  # no partial archive, no index


def test_validation_rejects_tampered_archives(tmp_path):
    source = make_mbtiles(str(tmp_path / "v.mbtiles"), pyramid(2))
    archive = build_pmtiles(source, str(tmp_path / "v.pmtiles"))
    data = bytearray(open(archive.path, "rb").read())
    data[99] = 2  # tile type PNG
    bad = tmp_path / "png.pmtiles"
    bad.write_bytes(bytes(data))
    with pytest.raises(PublishingError) as error:
        validate_pmtiles(str(bad))
    assert error.value.code == "Q2VT_PUB_NOT_MVT"
    truncated = tmp_path / "short.pmtiles"
    truncated.write_bytes(open(archive.path, "rb").read()[:-50])
    with pytest.raises(PublishingError):
        validate_pmtiles(str(truncated), sample=0)


def test_zoom_zero_only(tmp_path):
    source = make_mbtiles(str(tmp_path / "z0.mbtiles"), {(0, 0, 0): sample_tile(0, 0, 0)})
    archive = build_pmtiles(source, str(tmp_path / "z0.pmtiles"))
    assert (archive.min_zoom, archive.max_zoom, archive.addressed_tiles) == (0, 0, 1)
    compare_archives(source, archive.path)


def test_tile_ids_follow_the_official_hilbert_order():
    assert zxy_to_tileid(0, 0, 0) == 0
    assert zxy_to_tileid(1, 0, 0) == 1 and zxy_to_tileid(1, 0, 1) == 2
    assert zxy_to_tileid(1, 1, 1) == 3 and zxy_to_tileid(1, 1, 0) == 4


def test_mvt_decoder_reads_types_ids_and_values():
    data = encode_tile({"a": [("point", [1, 2], {"s": "x", "i": -5, "f": 1.5, "b": True}, 9)]})
    feature = mvt.decode(gzip.compress(data))["a"]["features"][0]
    assert feature["id"] == 9 and feature["type"] == 1
    assert feature["properties"] == {"s": "x", "i": -5, "f": 1.5, "b": True}
    with pytest.raises(mvt.MvtDecodeError):
        mvt.decode(b"\x89PNG")


def _cli():
    path = os.environ.get("Q2VT_PMTILES_CLI") or shutil.which("pmtiles")
    return path if path and os.path.exists(path) else None


@pytest.mark.skipif(_cli() is None, reason="official go-pmtiles CLI not available "
                    "(set Q2VT_PMTILES_CLI; tools/publishing/fetch_pmtiles_cli.py)")
def test_official_cli_verifies_the_archive(tmp_path):
    source = make_mbtiles(str(tmp_path / "cli.mbtiles"), pyramid(4))
    archive = build_pmtiles(source, str(tmp_path / "cli.pmtiles"))
    run = subprocess.run([_cli(), "verify", archive.path], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    show = subprocess.run([_cli(), "show", archive.path], capture_output=True, text=True,
                          check=True).stdout
    assert "tile type: mvt" in show and "addressed tiles count: 341" in show
    for z, x, y in [(0, 0, 0), (4, 3, 9), (2, 1, 3)]:
        tile = subprocess.run([_cli(), "tile", archive.path, str(z), str(x), str(y)],
                              capture_output=True, check=True).stdout
        assert mvt.payload(tile) == sample_tile(z, x, y)
