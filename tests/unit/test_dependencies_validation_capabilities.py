from fidelity.capabilities import capabilities_markdown, classify
from fidelity.dependencies import prunable_fields, style_field_dependencies
from fidelity.diagnostics import DiagnosticCollector
from fidelity.model import Strategy
from fidelity.validation import validate_style

STYLE = {"layers": [
    {"id": "a", "type": "line", "source": "s", "source-layer": "roads",
     "paint": {"line-width": ["*", ["to-number", ["get", "q2vt_property__w_00"], 1], 3.78]}},
    {"id": "b", "type": "symbol", "source": "s", "source-layer": "parcels",
     "layout": {"text-field": "{q2vt_label}", "icon-image": "marker_0"}},
]}


def test_dependencies_are_per_source_layer():
    deps = style_field_dependencies(STYLE)
    assert deps == {"roads": {"q2vt_property__w_00"}, "parcels": {"q2vt_label"}}


def test_pruning_only_touches_generated_fields():
    existing = {"q2vt_property__w_00", "q2vt_property__unused_00", "zoning", "q2vt_orig_id"}
    assert prunable_fields(existing, {"q2vt_property__w_00"}) == {"q2vt_property__unused_00"}


def test_field_name_occurring_elsewhere_does_not_keep_it():
    # The legacy `field in str(style)` check kept roads' field for parcels.
    assert "q2vt_property__w_00" in prunable_fields(
        {"q2vt_property__w_00"}, style_field_dependencies(STYLE)["parcels"])


def test_validation_reports_missing_sprite_field_and_empty_interval():
    style = {"layers": STYLE["layers"] + [
        {"id": "c", "type": "fill", "source": "s", "source-layer": "roads",
         "minzoom": 5, "maxzoom": 5, "paint": {"fill-color": "red"}}]}
    c = DiagnosticCollector()
    validate_style(style, c, sprite_names=[], tile_layers={"roads": set(), "parcels": {"q2vt_label"}})
    codes = sorted(d.code for d in c.items)
    assert codes == ["Q2VT_FIELD_MISSING", "Q2VT_SPRITE_MISSING", "Q2VT_ZOOM_EMPTY_INTERVAL"]


def test_classify_reports_unsupported_ddp_and_unknown_types():
    result = classify("SimpleLine", ["StrokeWidth", "CapStyle"])
    assert result.strategy == Strategy.NATIVE and result.unsupported_properties == ("CapStyle",)
    assert classify("MyPluginLayer").strategy == Strategy.UNSUPPORTED
    assert classify("ShapeburstFill").strategy == Strategy.UNSUPPORTED


def test_capability_doc_mentions_every_type():
    doc = capabilities_markdown()
    for name in ("SimpleFill", "LinePatternFill", "ShapeburstFill"):
        assert f"`{name}`" in doc


def _pbf(number, payload: bytes) -> bytes:
    size, out = len(payload), b""
    while True:
        byte = size & 0x7F
        size >>= 7
        out += bytes([byte | (0x80 if size else 0)])
        if not size:
            break
    return bytes([(number << 3) | 2]) + out + payload


def test_tile_layers_reads_names_and_keys_from_tiles():
    import gzip
    from fidelity.validation import tile_layers
    layer = _pbf(1, b"roads") + _pbf(3, b"q2vt_orig_id") + _pbf(3, b"name")
    tile = _pbf(3, layer) + _pbf(3, _pbf(1, b"water"))
    assert tile_layers(tile) == {"roads": {"q2vt_orig_id", "name"}, "water": set()}
    assert tile_layers(gzip.compress(tile)) == tile_layers(tile)


def test_truncated_metadata_is_completed_from_tiles(tmp_path):
    import json
    import sqlite3
    from fidelity.validation import inspect_mbtiles, tile_layer_fields
    path = str(tmp_path / "t.mbtiles")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE metadata (name text, value text)")
    conn.execute("CREATE TABLE tiles (zoom_level int, tile_column int, tile_row int, "
                 "tile_data blob)")
    listed = [{"id": f"l{i}", "fields": {}} for i in range(100)]  # GDAL's cap
    conn.execute("INSERT INTO metadata VALUES ('json', ?)",
                 (json.dumps({"vector_layers": listed}),))
    conn.execute("INSERT INTO tiles VALUES (14, 0, 0, ?)",
                 (_pbf(3, _pbf(1, b"l150") + _pbf(3, b"k")),))
    conn.commit()
    conn.close()
    archive = inspect_mbtiles(path, {"l0", "l150", "l151"})
    fields = tile_layer_fields(archive)
    assert fields["l150"] == {"k"} and "l151" not in fields
    assert archive["complete"]
