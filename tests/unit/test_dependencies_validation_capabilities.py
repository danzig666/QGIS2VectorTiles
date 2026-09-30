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
