"""
Shards of an index in one file (pure Python).

The search index, the feature lookup and the parcel report are split into
shards so the viewer loads only what a query needs. Written as separate
files, a city's search index alone was tens of thousands of files (a word
prefix each), slow to upload anywhere. Now an index's shards are gzip
members one after another in one ``.pack`` file; its manifest gives each
shard's ``offset`` and ``length`` there, and the viewer reads one with an
HTTP range request, as it reads PMTiles tiles (``shards.mjs``). Packs are
served without a Content-Encoding, so the offsets stay valid.
"""

import gzip
import hashlib
import json
import os
from typing import Optional

PACK_ENCODING = "gzip"


class ShardPack:
    """Appends shards (JSON) to ``path``; ``add`` returns the shard's
    manifest fields (path, offset, length, bytes, sha256 of the JSON)."""

    def __init__(self, path: str):
        self.path = path
        self.name = os.path.basename(path)
        self._handle = open(path, "wb")  # pylint: disable=consider-using-with
        self._offset = 0

    def add(self, payload) -> dict:
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        member = gzip.compress(data, compresslevel=6, mtime=0)
        self._handle.write(member)
        entry = {"path": self.name, "offset": self._offset, "length": len(member),
                 "encoding": PACK_ENCODING, "bytes": len(data),
                 "sha256": hashlib.sha256(data).hexdigest()}
        self._offset += len(member)
        return entry

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def read_shard(folder: str, shard: dict):
    """A shard's JSON, from its pack (offset, length) or its own file."""
    path = os.path.join(folder, *str(shard["path"]).split("/"))
    with open(path, "rb") as handle:
        if shard.get("offset") is None:
            return json.loads(handle.read().decode("utf-8"))
        handle.seek(int(shard["offset"]))
        member = handle.read(int(shard["length"]))
    return json.loads(gzip.decompress(member).decode("utf-8"))


def pack_text(path: str) -> Optional[str]:
    """Every shard of a pack as text (the release's leak scan reads it)."""
    try:
        with open(path, "rb") as handle:
            return gzip.decompress(handle.read()).decode("utf-8", errors="replace")
    except (OSError, EOFError, ValueError):
        return None
