"""
Local folder (or mounted network share) as a hosting destination: the same
immutable release layout as object storage, written with atomic renames.
Used for "publish to a web server folder" and as the reference provider in
tests. Activation compares the pointer's release id (local files have no
ETag); it is safe for one machine, not a cross-machine lock.
"""

import json
import os
import shutil
from typing import List, Optional, Tuple

from ..bundle import write_json_atomic
from ..errors import PublishingError
from .base import Provider


class LocalProvider(Provider):
    kind = "local"
    conditional_writes = True

    def __init__(self, destination, prefix: str, root: Optional[str] = None):
        super().__init__(prefix)
        self.root = os.path.abspath(root or destination.endpoint or destination.bucket or ".")

    def _path(self, relative: str) -> str:
        path = os.path.join(self.root, *self.key(relative).split("/"))
        if os.path.commonpath([os.path.abspath(path), self.root]) != self.root:
            raise PublishingError("Q2VT_PUB_PATH_UNSAFE", relative)
        return path

    def inspect(self) -> List[Tuple[str, bool, str]]:
        ok = os.path.isdir(self.root) and os.access(self.root, os.W_OK)
        return [("folder", ok, self.root if ok else f"{self.root} is not a writable folder")]

    def head(self, relative: str) -> Optional[dict]:
        path = self._path(relative)
        if not os.path.isfile(path):
            return None
        meta = path + ".q2vt-meta"
        metadata = {}
        if os.path.exists(meta):
            with open(meta, encoding="utf-8") as handle:
                metadata = json.load(handle)
        return {"size": os.path.getsize(path), "etag": metadata.get("sha256", ""), "metadata": metadata}

    def put_file(self, relative, path, content_type, cache_control, sha256, progress=None, journal=None):
        target = self._path(relative)
        existing = self.head(relative)
        if existing:
            if existing["metadata"].get("sha256") == sha256:
                return sha256
            raise PublishingError("Q2VT_PUB_UPLOAD", f"{relative} exists with other content.")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".partial"
        shutil.copyfile(path, tmp)
        os.replace(tmp, target)
        with open(target + ".q2vt-meta", "w", encoding="utf-8") as handle:
            json.dump({"sha256": sha256, "contentType": content_type}, handle)
        return sha256

    def copy_object(self, source_relative, relative, content_type, cache_control, sha256):
        source = self._path(source_relative)
        meta = self.head(source_relative)
        if meta is None or meta["metadata"].get("sha256") != sha256:
            return None
        return self.put_file(relative, source, content_type, cache_control, sha256)

    def put_bytes(self, relative, data, content_type, cache_control, if_match=None,
                  if_none_match=False, sha256=""):
        target = self._path(relative)
        current = self.get_bytes(relative)
        if if_none_match and current is not None:
            raise PublishingError("Q2VT_PUB_ACTIVATION_CONFLICT", f"{relative} already exists.")
        if if_match is not None and (current is None or current[1] != if_match):
            raise PublishingError("Q2VT_PUB_ACTIVATION_CONFLICT", f"{relative} changed meanwhile.")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = target + ".partial"
        with open(tmp, "wb") as handle:
            handle.write(data)
        os.replace(tmp, target)
        return self._etag(data)

    @staticmethod
    def _etag(data: bytes) -> str:
        import hashlib  # pylint: disable=import-outside-toplevel
        return '"' + hashlib.sha256(data).hexdigest()[:32] + '"'

    def get_bytes(self, relative):
        path = self._path(relative)
        if not os.path.isfile(path):
            return None
        with open(path, "rb") as handle:
            data = handle.read()
        return data, self._etag(data)

    def read_pointer(self):
        found = self.get_bytes("current.json")
        if found is None:
            return None, None
        return json.loads(found[0].decode("utf-8")), found[1]

    def list_releases(self):
        from ..web_builder import RELEASE_ID  # pylint: disable=import-outside-toplevel
        folder = os.path.join(self.root, *self.prefix.split("/"), "releases")
        if not os.path.isdir(folder):
            return []
        return sorted(n for n in os.listdir(folder) if RELEASE_ID.match(n))

    def delete_release(self, release_id):
        from ..web_builder import RELEASE_ID  # pylint: disable=import-outside-toplevel
        if not RELEASE_ID.match(release_id):
            raise PublishingError("Q2VT_PUB_PATH_UNSAFE", release_id)
        folder = os.path.join(self.root, *self.prefix.split("/"), "releases", release_id)
        count = sum(len(files) for _, _, files in os.walk(folder))
        shutil.rmtree(folder, ignore_errors=True)
        return count

    def write_json(self, relative: str, payload) -> None:
        write_json_atomic(self._path(relative), payload)
