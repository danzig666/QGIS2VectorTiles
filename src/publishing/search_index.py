"""
Publication-wide search index (pure Python), loaded lazily by a Web Worker.

Built from the private feature records of the *published* features (not
from tiles), so offscreen features are found without loading tiles. Records
hold a label, search terms, an inside anchor and bounds — never geometry.

Matching: normalized text (Unicode NFKD, combining marks removed — ő/ö/o,
ű/ü/u —, lower case, whitespace collapsed; ``/``, ``-`` and leading zeros
kept). Ranking: exact term, then label/term prefix, then word prefix, then
substring (substring only in the single-file mode).

Modes (chosen by measured size, never by silently dropping records):

* ``single``: one ``search/index.json`` when it is at most ``budget_bytes``
  and ``budget_records``;
* ``prefix``: deterministic shards by the first characters of every word of
  every term, all in ``search/index.pack`` (gzip members read by range; see
  shard_pack); the prefix length grows until shards
  fit ``shard_bytes``, and a bucket that still does not fit (a word shared
  by most records) is split into numbered parts. Queries match word
  prefixes (documented: no arbitrary substring search in this mode); the
  client loads the shards of the most selective query word.

The JavaScript twin of ``normalize`` is in resources/web_viewer/search_core.mjs;
both are tested with the same vectors.
"""

import hashlib
import json
import os
import re
import unicodedata
from typing import Dict, Iterable, List, Optional, Tuple

from .shard_pack import ShardPack

DEFAULT_BUDGET_BYTES = 10 * 1024 * 1024
DEFAULT_BUDGET_RECORDS = 50000
DEFAULT_SHARD_BYTES = 512 * 1024
PACK_NAME = "index.pack"  # the prefix shards, one after another (shard_pack)
_MANIFEST_LINE_BYTES = 150  # a shard's line in the manifest
NORMALIZATION = "NFKD, combining marks removed, lower case, whitespace collapsed"
_SPACE = re.compile(r"\s+")
_WORD = re.compile(r"[\s,;:()\"'\[\]]+")


def normalize(text) -> str:
    text = unicodedata.normalize("NFKD", str(text if text is not None else ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return _SPACE.sub(" ", text.lower()).strip()


def words(text: str) -> List[str]:
    return [w for w in _WORD.split(normalize(text)) if w]


def _entry(record: dict, layer_index: Dict[str, int]) -> list:
    terms = [t for t in record.get("terms") or [] if t not in (None, "")]
    return [layer_index[record["layerId"]], record["featureKey"], record.get("label") or "",
            terms, record.get("anchor"), record.get("bounds"), record.get("suggestedZoom")]


def _write(path: str, payload) -> Tuple[int, str]:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with open(path, "wb") as handle:
        handle.write(data)
    return len(data), hashlib.sha256(data).hexdigest()


def build_search_index(records: Iterable[dict], searchable: Dict[str, List[str]], out_dir: str,
                       budget_bytes: int = DEFAULT_BUDGET_BYTES,
                       budget_records: int = DEFAULT_BUDGET_RECORDS,
                       shard_bytes: int = DEFAULT_SHARD_BYTES) -> Optional[dict]:
    """Write ``out_dir`` (``search/``) and return its manifest, or None when
    no layer is searchable. ``searchable``: {logical layer id: [fields]}."""
    if not searchable:
        return None
    layers = sorted(searchable)
    layer_index = {lid: i for i, lid in enumerate(layers)}
    entries, coverage = [], {lid: {"records": 0, "fields": list(searchable[lid])} for lid in layers}
    seen = set()
    for record in records:
        lid = record.get("layerId")
        if lid not in layer_index or record.get("featureKey") in (None, ""):
            continue
        marker = (lid, record["featureKey"])
        if marker in seen:  # one entry per logical feature
            continue
        seen.add(marker)
        entries.append(_entry(record, layer_index))
        coverage[lid]["records"] += 1
    entries.sort(key=lambda e: (normalize(e[2]), e[0], e[1]))
    os.makedirs(out_dir, exist_ok=True)
    payload = {"layers": layers, "entries": entries}
    size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    manifest = {"schemaVersion": 1, "kind": "search", "records": len(entries),
                "normalization": NORMALIZATION, "coverage": coverage, "layers": layers}
    if size <= budget_bytes and len(entries) <= budget_records:
        nbytes, digest = _write(os.path.join(out_dir, "index.json"), payload)
        manifest.update(mode="single", prefixLength=0,
                        shards=[{"key": "", "path": "index.json", "records": len(entries),
                                 "bytes": nbytes, "sha256": digest}])
        return manifest
    shards, length = _prefix_shards(entries, shard_bytes)
    manifest.update(mode="prefix", prefixLength=length, shards=[])
    # Every shard in one file (shard_pack): a city's addresses made tens of
    # thousands of shard files, slow to upload anywhere.
    with ShardPack(os.path.join(out_dir, PACK_NAME)) as pack:
        for key in sorted(shards):
            # A bucket of a very common word (skewed data) is split into parts
            # of bounded size; the client loads every part of the key it needs.
            for part, chunk in enumerate(_parts(shards[key], shard_bytes)):
                item = {"key": key, **pack.add({"layers": layers, "entries": chunk}), "records": len(chunk)}
                if part or len(chunk) != len(shards[key]):
                    item["part"] = part
                manifest["shards"].append(item)
    return manifest


def _parts(entries: List[list], limit: int) -> List[List[list]]:
    parts, current, size = [], [], 2
    for entry in entries:
        n = len(json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
        if current and size + n > limit:
            parts.append(current)
            current, size = [], 2
        current.append(entry)
        size += n
    if current:
        parts.append(current)
    return parts


def entry_tokens(entry: list) -> set:
    """Normalized words of the label and terms, plus each whole term."""
    found = set(words(entry[2]))
    for term in entry[3]:
        found.update(words(term))
        found.add(normalize(term))
    return {t for t in found if t}


def _prefix_shards(entries: List[list], shard_bytes: int, max_length: int = 6) -> Tuple[Dict[str, List[list]], int]:
    """({prefix: entries having a token starting with it}, prefix length).

    The prefix length is the shortest (1..max_length) whose largest shard
    fits ``shard_bytes``; when none does (a word most records share, like a
    city's "utca", keeps its shard large at any length; it is split into
    parts), the one with the least to download: the manifest (a line per
    shard) plus what a query word loads on average. Always taking the
    longest made a city-size test index 47,703 shards (a manifest of
    megabytes) instead of 1,145. Tokens shorter than the length are filed
    under the whole token. The client loads the shard of a query word's
    first ``length`` characters, or every shard starting with a shorter
    word."""
    tokenized = [(entry, entry_tokens(entry)) for entry in entries]
    sizes = [len(json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
             for entry, _ in tokenized]
    best: Optional[Tuple[float, Dict[str, List[list]], int]] = None
    for length in range(1, max_length + 1):
        buckets: Dict[str, List[list]] = {}
        nbytes: Dict[str, int] = {}
        for (entry, toks), size in zip(tokenized, sizes):
            for key in {t[:length] for t in toks}:
                buckets.setdefault(key, []).append(entry)
                nbytes[key] = nbytes.get(key, 1) + size
        if max(nbytes.values(), default=0) <= shard_bytes:
            return buckets, length
        manifest = _MANIFEST_LINE_BYTES * len(buckets)
        if best is not None and manifest >= best[0]:
            break  # longer prefixes only add shards
        total = sum(nbytes.values())
        cost = manifest + (sum(n * n for n in nbytes.values()) / total if total else 0)
        if best is None or cost < best[0]:
            best = (cost, buckets, length)
    return (best[1], best[2]) if best else ({}, 1)
