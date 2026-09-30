"""
diagnostics.py

Stable, machine-readable export diagnostics.

A diagnostic records *what* could not be reproduced exactly, *where* (layer,
rule, symbol-layer index), *which* export strategy was chosen and *what the
user can do about it*. Codes are stable identifiers (``Q2VT_*``); messages are
human-readable and may change between versions.
"""

import html
import json
import os
import re
import threading
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Dict, Iterable, List, Optional


class Severity(str, Enum):
    """Diagnostic severity. ``ERROR`` always fails a strict export."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class CodeInfo:
    """Static description of a diagnostic code."""

    severity: Severity
    title: str
    suggestion: str = ""
    # When True the diagnostic describes an approximation or omission, so a
    # strict export must fail even if its severity is only a warning.
    fidelity_loss: bool = True


CODES: Dict[str, CodeInfo] = {
    # --- Units / zoom -----------------------------------------------------
    "Q2VT_UNIT_UNKNOWN": CodeInfo(
        Severity.ERROR, "Unknown or unresolved render unit",
        "Use millimeters, points, pixels, inches, map units or meters at scale."),
    "Q2VT_UNIT_PERCENTAGE": CodeInfo(
        Severity.WARNING, "Percentage unit without a known reference dimension",
        "Use an absolute unit for this property."),
    "Q2VT_UNIT_MAP_UNITS_APPROX": CodeInfo(
        Severity.INFO, "Map-unit size converted with a reference-latitude scale factor",
        "Project CRS is not Web Mercator; sizes are exact only near the reference latitude.",
        fidelity_loss=False),
    "Q2VT_MIXED_UNITS": CodeInfo(
        Severity.WARNING, "Symbol mixes map units with screen units",
        "Screen-unit parts (e.g. millimetre outlines) scale with the map in the browser."),
    "Q2VT_ZOOM_EMPTY_INTERVAL": CodeInfo(
        Severity.WARNING, "Rule has an empty visibility interval and was skipped",
        "Check the rule's minimum and maximum scale."),
    # --- Properties / expressions -----------------------------------------
    "Q2VT_DDP_NO_EMITTER": CodeInfo(
        Severity.WARNING, "Data-defined property has no MapLibre equivalent",
        "The static value is used in the browser style."),
    "Q2VT_DDP_EVAL_ERROR": CodeInfo(
        Severity.ERROR, "Data-defined expression could not be evaluated",
        "Fix the expression; it must be valid for every exported feature."),
    "Q2VT_EXPR_INVALID": CodeInfo(
        Severity.ERROR, "Generated MapLibre expression is invalid",
        "Report this as a converter bug with the diagnostic detail."),
    # --- Symbols / sprites ------------------------------------------------
    "Q2VT_SPRITE_RENDER_FAILED": CodeInfo(
        Severity.ERROR, "Symbol could not be rendered to a sprite image",
        "The symbol is omitted from the web style. Check SVG paths and sub-symbols."),
    "Q2VT_SPRITE_WRONG_INPUT": CodeInfo(
        Severity.ERROR, "Sprite renderer received an object of the wrong type",
        "Report this as a converter bug with the diagnostic detail."),
    "Q2VT_SPRITE_TRANSPARENT": CodeInfo(
        Severity.INFO, "Symbol renders fully transparent by design",
        fidelity_loss=False),
    "Q2VT_SPRITE_VARIANTS_BUDGET": CodeInfo(
        Severity.WARNING, "Too many distinct data-defined symbol appearances",
        "Reduce the number of distinct colours/shapes or accept the static symbol."),
    "Q2VT_SPRITE_MISSING": CodeInfo(
        Severity.ERROR, "Style references an image missing from the sprite sheet",
        "Report this as a converter bug with the diagnostic detail."),
    "Q2VT_PATTERN_APPROXIMATE": CodeInfo(
        Severity.WARNING, "Pattern reproduced only approximately",
        "Consider a simpler hatch (0/45/90 degrees, millimeter spacing)."),
    "Q2VT_PATTERN_NONPERIODIC": CodeInfo(
        Severity.WARNING,
        "A periodic texture cannot reproduce the requested pattern within tolerance",
        "Use an angle/spacing combination with a small repeat cell."),
    "Q2VT_PATTERN_MAP_UNITS": CodeInfo(
        Severity.WARNING, "Map-unit pattern spacing is frozen at a reference zoom",
        "Browser fill patterns keep a constant screen size."),
    "Q2VT_MARKER_PLACEMENT_APPROX": CodeInfo(
        Severity.WARNING, "Marker-line placement approximated by repeated symbols",
        "Exact vertex/end markers require materialized geometry (planned)."),
    "Q2VT_FONT_UNRESOLVED": CodeInfo(
        Severity.ERROR, "Label font is not installed; labels would render without glyphs",
        "Install the font or choose an installed font for the label."),
    "Q2VT_GLYPHS_MISSING": CodeInfo(
        Severity.ERROR, "Style references a font stack without generated glyphs",
        "Check the label font; protected labels would be invisible in the browser."),
    # --- Rendering / ordering ---------------------------------------------
    "Q2VT_UNSUPPORTED_SYMBOL_LAYER": CodeInfo(
        Severity.WARNING, "Symbol layer type is not supported by the web style",
        "Replace it with a supported symbol layer or accept its omission."),
    "Q2VT_UNSUPPORTED_EFFECT": CodeInfo(
        Severity.WARNING, "Paint effect or blend mode is ignored in the web style"),
    "Q2VT_ELSE_NESTED_SIBLINGS": CodeInfo(
        Severity.WARNING, "ELSE rule approximated: sibling rules have nested children",
        "The ELSE condition uses sibling filters only."),
    "Q2VT_HYBRID_NOT_AVAILABLE": CodeInfo(
        Severity.WARNING, "Hybrid raster fallback is not implemented yet",
        "Unsupported components are reported instead of rasterized."),
    # --- Tiles / publication ----------------------------------------------
    "Q2VT_TILES_ZOOM_MISMATCH": CodeInfo(
        Severity.ERROR, "Generated tile archive does not cover the requested zooms"),
    "Q2VT_SOURCE_LAYER_MISSING": CodeInfo(
        Severity.WARNING, "Style references a source layer absent from the tile archive",
        "The rule matched no features in the export extent.", fidelity_loss=False),
    "Q2VT_FIELD_MISSING": CodeInfo(
        Severity.ERROR, "Style references an attribute absent from the tile layer"),
    "Q2VT_STRICT_FAILED": CodeInfo(
        Severity.ERROR, "Strict export rejected unsupported or approximate components"),
    "Q2VT_PROJECT_MUTATED": CodeInfo(
        Severity.ERROR, "Export changed the source project styling",
        "Report this as a converter bug."),
}


@dataclass(frozen=True)
class Diagnostic:
    """A single export diagnostic (immutable)."""

    code: str
    severity: Severity
    message: str
    layer_id: str = ""
    rule_id: str = ""
    symbol_layer_index: Optional[int] = None
    component: str = ""
    strategy: str = ""
    feature_count: Optional[int] = None
    suggestion: str = ""
    # Free-form technical detail (expressions, paths). Removed by redaction.
    detail: str = ""

    @property
    def fidelity_loss(self) -> bool:
        info = CODES.get(self.code)
        return info.fidelity_loss if info else True

    def to_dict(self, redact: bool = False) -> dict:
        data = asdict(self)
        data["severity"] = self.severity.value
        if redact:
            data["detail"] = ""
            data["message"] = redact_paths(self.message)
        return {k: v for k, v in data.items() if v not in ("", None)}


_PATH_RE = re.compile(r"(?:[A-Za-z]:)?(?:[\\/][^\\/\s'\"]+){2,}")


def redact_paths(text: str) -> str:
    """Replace absolute file-system paths with ``<path>/basename``."""
    return _PATH_RE.sub(lambda m: f"<path>/{os.path.basename(m.group(0))}", text or "")


class StrictModeError(RuntimeError):
    """Raised when a strict export encounters fidelity-losing diagnostics."""

    def __init__(self, diagnostics: List[Diagnostic]):
        self.diagnostics = diagnostics
        lines = [f"{d.code}: {d.message}" for d in diagnostics[:20]]
        more = len(diagnostics) - len(lines)
        if more > 0:
            lines.append(f"... and {more} more")
        super().__init__("Strict export failed:\n" + "\n".join(lines))


class DiagnosticCollector:
    """Thread-safe accumulator of diagnostics with de-duplication."""

    def __init__(self):
        self._items: List[Diagnostic] = []
        self._seen: set = set()
        self._lock = threading.Lock()

    def add(self, code: str, message: str = "", severity: Optional[Severity] = None,
            **context) -> Diagnostic:
        info = CODES.get(code)
        if info is None:
            raise KeyError(f"Unknown diagnostic code: {code}")
        diag = Diagnostic(
            code=code,
            severity=severity or info.severity,
            message=message or info.title,
            suggestion=context.pop("suggestion", "") or info.suggestion,
            **context,
        )
        key = (diag.code, diag.message, diag.layer_id, diag.rule_id,
               diag.symbol_layer_index, diag.component)
        with self._lock:
            if key not in self._seen:
                self._seen.add(key)
                self._items.append(diag)
        return diag

    def extend(self, diagnostics: Iterable[Diagnostic]):
        for diag in diagnostics:
            key = (diag.code, diag.message, diag.layer_id, diag.rule_id,
                   diag.symbol_layer_index, diag.component)
            with self._lock:
                if key not in self._seen:
                    self._seen.add(key)
                    self._items.append(diag)

    @property
    def items(self) -> List[Diagnostic]:
        with self._lock:
            return list(self._items)

    def by_code(self, code: str) -> List[Diagnostic]:
        return [d for d in self.items if d.code == code]

    def has_errors(self) -> bool:
        return any(d.severity == Severity.ERROR for d in self.items)

    def strict_violations(self) -> List[Diagnostic]:
        """Diagnostics that must fail a strict export."""
        return [
            d for d in self.items
            if d.severity == Severity.ERROR
            or (d.fidelity_loss and d.severity == Severity.WARNING)
        ]

    def enforce_strict(self):
        violations = self.strict_violations()
        if violations:
            raise StrictModeError(violations)

    def counts(self) -> Dict[str, int]:
        result = {s.value: 0 for s in Severity}
        for d in self.items:
            result[d.severity.value] += 1
        return result

    def to_json(self, redact: bool = True, extra: Optional[dict] = None) -> str:
        payload = {
            "counts": self.counts(),
            "diagnostics": [d.to_dict(redact=redact) for d in self.items],
        }
        if extra:
            payload.update(extra)
        return json.dumps(payload, indent=2, ensure_ascii=False)


def render_html_report(payload: dict) -> str:
    """Render the JSON report payload as a small self-contained HTML page.

    All user-controlled text is HTML-escaped.
    """
    esc = html.escape
    rows = []
    for d in payload.get("diagnostics", []):
        location = " / ".join(
            str(v) for v in (d.get("layer_id"), d.get("rule_id"), d.get("symbol_layer_index"))
            if v not in (None, "")
        )
        rows.append(
            "<tr class='{sev}'><td>{sev}</td><td><code>{code}</code></td><td>{msg}</td>"
            "<td>{loc}</td><td>{strategy}</td><td>{sugg}</td></tr>".format(
                sev=esc(d.get("severity", "")), code=esc(d.get("code", "")),
                msg=esc(d.get("message", "")), loc=esc(location),
                strategy=esc(d.get("strategy", "")), sugg=esc(d.get("suggestion", "")),
            )
        )
    counts = payload.get("counts", {})
    summary = ", ".join(f"{esc(str(v))} {esc(k)}" for k, v in counts.items())
    meta_rows = "".join(
        f"<tr><th>{esc(str(k))}</th><td>{esc(json.dumps(v, ensure_ascii=False))}</td></tr>"
        for k, v in payload.items() if k not in ("diagnostics", "counts")
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Export fidelity report</title>
<style>
:root {{ --bg:#fff; --fg:#1f2328; --muted:#59636e; --line:#d1d9e0;
  --err:#fbe5e1; --warn:#fff5d6; --info:#eef4fb; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1;
  --line:#3d444d; --err:#3b1d1a; --warn:#3a2f10; --info:#122235; }} }}
body {{ background:var(--bg); color:var(--fg); font:14px/1.45 system-ui,sans-serif;
  margin:0; padding:16px; }}
table {{ border-collapse:collapse; width:100%; margin:12px 0; }}
th,td {{ border:1px solid var(--line); padding:4px 8px; text-align:left; vertical-align:top; }}
tr.error td {{ background:var(--err); }} tr.warning td {{ background:var(--warn); }}
tr.info td {{ background:var(--info); }} .muted {{ color:var(--muted); }}
.wrap {{ overflow-x:auto; }}
</style></head><body>
<h1>Export fidelity report</h1>
<p class="muted">{summary}</p>
<div class="wrap"><table><tr><th>Severity</th><th>Code</th><th>Message</th>
<th>Location</th><th>Strategy</th><th>Suggestion</th></tr>{''.join(rows)}</table></div>
<h2>Export metadata</h2><div class="wrap"><table>{meta_rows}</table></div>
</body></html>
"""
