"""
Logical provenance of generated components (pure Python).

A ``RuleProvenance`` is captured by the flattener *before* rules are cloned,
converted or split, so that every generated dataset/style layer can be traced
to the original QGIS layer and legend rule (human-readable label, stable
legend key). It is copied by ``FlattenedRule.derive()`` and every other
FlattenedRule construction; the publication manifest is built from it, never
from generated dataset names.

Logical ids are short, deterministic digests (stable across exports of the
same project): ``lyr-…`` from the QGIS layer id, ``rule-…`` from layer id +
legend key, ``g-…`` from the layer-tree group path.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Optional, Tuple


@dataclass(frozen=True)
class RuleProvenance:
    layer_id: str
    layer_name: str
    kind: str                        # "symbology" | "labeling"
    rule_key: str = ""               # legend key of the original rule/category/range
    rule_label: str = ""             # its legend text
    parent_keys: Tuple[str, ...] = field(default_factory=tuple)  # outermost first
    else_rule: bool = False
    component: str = ""              # "leader" for callout lines, "" otherwise


def digest(*parts: str, length: int = 10) -> str:
    text = "\x1f".join(parts)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def layer_logical_id(layer_id: str) -> str:
    return f"lyr-{digest(layer_id)}"


def rule_logical_id(layer_id: str, rule_key: str) -> str:
    return f"rule-{digest(layer_id, rule_key)}"


def group_logical_id(path: Tuple[str, ...]) -> str:
    return f"g-{digest(*path)}"


def owning_style_name(style_layer_id: str, names) -> Optional[str]:
    """The flat-rule style name (rule description) that produced
    ``style_layer_id``: equal to it or a prefix followed by ``_`` (per-zoom
    splits ``_z14``, runtime copies ...). Longest match wins."""
    best = None
    for name in names:
        if style_layer_id == name or style_layer_id.startswith(name + "_"):
            if best is None or len(name) > len(best):
                best = name
    return best
