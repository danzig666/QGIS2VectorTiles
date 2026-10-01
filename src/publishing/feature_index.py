"""
Exact feature lookup by (layer id, feature key) for deep links and popups
(pure Python). Independent of the search UI: a link to an offscreen feature
works on a cold page even when search is disabled.

Records: key, label, approved popup attributes, anchor and bounds — never
geometry. Shards: ``features/f-<prefix>.json`` keyed by the first hex digits
of FNV-1a-32 over ``layerId + "\\u0000" + key`` (UTF-8); the JavaScript
twin is ``fnv1a`` in resources/web_viewer/search_core.mjs.
"""

import hashlib
import json
import os
from typing import Dict, Iterable, Optional

TARGET_SHARD_RECORDS = 2000


def fnv1a(text: str) -> str:
    value = 0x811C9DC5
    for byte in text.encode("utf-8"):
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return f"{value:08x}"


def shard_key(layer_id: str, key: str, length: int) -> str:
    return fnv1a(f"{layer_id}\u0000{key}")[:length] if length else ""


def build_feature_index(records: Iterable[dict], layers: Dict[str, dict], out_dir: str,
                        target: int = TARGET_SHARD_RECORDS) -> Optional[dict]:
    """``layers``: {logical layer id: {"popup": [fields], "scope": ...}}
    for layers with popups or deep links."""
    if not layers:
        return None
    items = {}
    for record in records:
        lid = record.get("layerId")
        if lid not in layers or record.get("featureKey") in (None, ""):
            continue
        fields = layers[lid]["popup"]
        items[(lid, record["featureKey"])] = {
            "l": lid, "k": record["featureKey"], "t": record.get("label") or "",
            "a": {name: (record.get("attributes") or {}).get(name) for name in fields},
            "p": record.get("anchor"), "b": record.get("bounds"), "z": record.get("suggestedZoom")}
    count = len(items)
    length = 0
    while count / (16 ** length) > target and length < 4:
        length += 1
    shards: Dict[str, list] = {}
    for (lid, key), item in sorted(items.items()):
        shards.setdefault(shard_key(lid, key, length), []).append(item)
    os.makedirs(out_dir, exist_ok=True)
    manifest = {"schemaVersion": 1, "kind": "features", "mode": "hash" if length else "single",
                "prefixLength": length, "records": count, "hash": "fnv1a32(layerId + U+0000 + key)",
                "coverage": {lid: {"records": sum(1 for (l, _) in items if l == lid),
                                   "fields": list(info["popup"])} for lid, info in layers.items()},
                "shards": []}
    for key in sorted(shards):
        name = f"f-{key or 'all'}.json"
        data = json.dumps(shards[key], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        with open(os.path.join(out_dir, name), "wb") as handle:
            handle.write(data)
        manifest["shards"].append({"key": key, "path": name, "records": len(shards[key]),
                                   "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    return manifest
