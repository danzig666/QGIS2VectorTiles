"""
bindings.py

Summarize how each emitted MapLibre property is driven (constant, feature
attribute, zoom or both), derived from the final style so the report
describes exactly what the browser receives.
"""

from typing import Dict, List

from . import expressions as ex
from .model import PropertyBinding


def _kind(value) -> str:
    fields = ex.referenced_fields(value) if isinstance(value, list) else set()
    zoom = isinstance(value, list) and ex.contains_zoom(value)
    if fields and zoom:
        return "field+zoom"
    if fields:
        return "field"
    if zoom:
        return "zoom"
    return "constant"


def style_bindings(style: dict) -> Dict[str, List[PropertyBinding]]:
    """Non-constant property bindings per style layer id."""
    result: Dict[str, List[PropertyBinding]] = {}
    for layer in style.get("layers", []):
        bindings = []
        for section in ("layout", "paint"):
            for target, value in sorted((layer.get(section) or {}).items()):
                kind = _kind(value)
                if kind == "constant":
                    continue
                fields = sorted(ex.referenced_fields(value)) if isinstance(value, list) else []
                bindings.append(PropertyBinding(
                    target=target, kind=kind, result_type="", value=value,
                    field_id=", ".join(fields)))
        if bindings:
            result[layer.get("id", "")] = bindings
    return result


def bindings_report(style: dict) -> List[dict]:
    """JSON-ready rows: one per data- or zoom-driven property."""
    return [{"layer": layer_id, "property": b.target, "kind": b.kind, "fields": b.field_id}
            for layer_id, items in style_bindings(style).items() for b in items]
