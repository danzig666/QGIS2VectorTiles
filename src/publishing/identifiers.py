"""
Stable feature identity of published layers (pure Python).

``q2vt_feature_key`` is a *string* computed on the export side, before
geometry generation and tile splitting, and carried by every dataset derived
from the feature (fills, outlines, hatches, labels, visible-polygon helpers):

* persistent keys come from user-selected non-NULL unique field(s); a single
  field is its text value (leading zeros, slashes and 64-bit integers kept
  exactly: never a JavaScript number); several fields are joined with a
  typed, length-prefixed encoding so ``("1/2", "3")`` and ``("1", "2/3")``
  never collide;
* without a key, an *export-scoped* id is used (``e<provider FID>``): unique
  in one release, not guaranteed across re-exports (providers may renumber
  features); the manifest says so.

Keys are unique per logical layer; links and lookups always carry the layer
id, so two layers with the same key never collide.
"""

import re
from typing import Dict, Iterable, List, Optional, Tuple

EXPORT_SCOPED = "export"
PERSISTENT = "persistent"


def quote_field(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def key_expression(key_fields: List[str]) -> Tuple[str, str]:
    """(QGIS expression, identity scope) of a layer's feature key."""
    if not key_fields:
        return "'e' || to_string($id)", EXPORT_SCOPED
    if len(key_fields) == 1:
        return _as_text(key_fields[0]), PERSISTENT
    parts = []
    for name in key_fields:
        text = _as_text(name)
        parts.append(f"length({text}) || ':' || {text}")
    # NULL in any part makes the whole key NULL (reported, never exported silently).
    return " || '|' || ".join(f"({part})" for part in parts), PERSISTENT


def _as_text(name: str) -> str:
    # to_string keeps integers exact (no float conversion); strings unchanged.
    return f"to_string({quote_field(name)})"


def encode_compound(values: Iterable) -> Optional[str]:
    """Python twin of the compound key expression (for tests/validation)."""
    parts = []
    for value in values:
        if value is None:
            return None
        text = str(value)
        parts.append(f"{len(text)}:{text}")
    return "|".join(parts)


def validate_keys(keys: Iterable[Optional[str]], limit: int = 5) -> Dict[str, List[str]]:
    """{"null": [...], "duplicate": [...]} examples (empty lists: valid)."""
    seen, duplicates, nulls = set(), [], 0
    for key in keys:
        if key is None or key == "":
            nulls += 1
            continue
        if key in seen and len(duplicates) < limit:
            duplicates.append(key)
        seen.add(key)
    return {"null": [str(nulls)] if nulls else [], "duplicate": duplicates}


_SAFE = re.compile(r"^[\x20-\x7e -￿]{1,512}$")


def is_link_safe(key: str) -> bool:
    """Keys must be printable text of reasonable length for URLs."""
    return bool(_SAFE.match(key or ""))
