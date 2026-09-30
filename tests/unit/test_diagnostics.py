import json

import pytest

from fidelity.diagnostics import (DiagnosticCollector, Severity, StrictModeError,
                                  redact_paths, render_html_report)


def test_collector_dedupes_and_counts():
    c = DiagnosticCollector()
    c.add("Q2VT_DDP_NO_EMITTER", "x", layer_id="L")
    c.add("Q2VT_DDP_NO_EMITTER", "x", layer_id="L")
    c.add("Q2VT_SPRITE_TRANSPARENT")
    assert len(c.items) == 2
    assert c.counts() == {"info": 1, "warning": 1, "error": 0}


def test_unknown_code_rejected():
    with pytest.raises(KeyError):
        DiagnosticCollector().add("NOPE")


def test_strict_mode_fails_on_fidelity_loss_but_not_info():
    c = DiagnosticCollector()
    c.add("Q2VT_SPRITE_TRANSPARENT")
    c.add("Q2VT_UNIT_MAP_UNITS_APPROX")
    c.enforce_strict()
    c.add("Q2VT_PATTERN_APPROXIMATE", "hatch")
    with pytest.raises(StrictModeError):
        c.enforce_strict()


def test_redaction_removes_paths_and_detail():
    c = DiagnosticCollector()
    c.add("Q2VT_SPRITE_RENDER_FAILED", "missing /home/alice/secret/plan.svg",
          detail='"parcel" || \'x\'')
    payload = json.loads(c.to_json(redact=True))
    entry = payload["diagnostics"][0]
    assert "alice" not in entry["message"] and "plan.svg" in entry["message"]
    assert "detail" not in entry
    assert redact_paths("C:\\Users\\bob\\x\\y.svg").endswith("y.svg")


def test_html_report_escapes_user_text():
    c = DiagnosticCollector()
    c.add("Q2VT_DDP_NO_EMITTER", "<script>alert(1)</script>", severity=Severity.WARNING)
    page = render_html_report(json.loads(c.to_json()))
    assert "<script>alert" not in page and "&lt;script&gt;" in page


def test_bindings_summarize_data_and_zoom_driven_properties():
    from fidelity.bindings import bindings_report
    from fidelity.diagnostics import render_html_report
    style = {"layers": [{"id": "a", "type": "line", "paint": {
        "line-color": "red",
        "line-width": ["interpolate", ["exponential", 2], ["zoom"], 0, 1, 24, 2],
        "line-opacity": ["to-number", ["get", "q2vt_o"], 1]}}]}
    rows = bindings_report(style)
    assert {(r["property"], r["kind"]) for r in rows} == {
        ("line-width", "zoom"), ("line-opacity", "field")}
    page = render_html_report({"diagnostics": [], "counts": {}, "bindings": rows})
    assert "q2vt_o" in page and "Driven by" in page
