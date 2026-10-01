"""A deterministic in-process stand-in for the boto3 S3 client methods the
providers use, with S3 semantics that matter here (conditional writes,
multipart, prefix listing) and failure injection. Objects are stored as
files under ``root/<bucket>/<key>`` so a local HTTP server can serve them
as the "public domain" (ranges, content types)."""

import hashlib
import io
import json
import os
import threading


class ClientError(Exception):
    def __init__(self, code, status, message=""):
        super().__init__(f"{code}: {message}")
        self.response = {"Error": {"Code": code, "Message": message},
                         "ResponseMetadata": {"HTTPStatusCode": status}}


class FakeS3Client:
    def __init__(self, root, bucket="maps"):
        self.root = root
        self.bucket = bucket
        os.makedirs(os.path.join(root, bucket), exist_ok=True)
        self.meta = {}       # key -> {"etag", "ContentType", "CacheControl", "Metadata"}
        self.uploads = {}    # upload id -> {"key", "parts", ...}
        self.calls = []
        self.fail = []       # [(method, predicate(params) -> bool, exception, remaining)]
        self.lock = threading.Lock()

    # -- helpers ---------------------------------------------------------------------
    def inject(self, method, error, times=1, when=lambda params: True, after=None):
        """Raise ``error`` on the next ``times`` matching calls (``after``:
        run the real call first, then raise — a lost response)."""
        self.fail.append({"method": method, "error": error, "times": times, "when": when, "after": after})

    def _maybe_fail(self, method, params, before=True):
        for item in self.fail:
            if item["method"] == method and item["times"] > 0 and item["when"](params) \
                    and bool(item["after"]) != before:
                item["times"] -= 1
                raise item["error"]

    def _path(self, bucket, key):
        assert bucket == self.bucket
        return os.path.join(self.root, bucket, *key.split("/"))

    def _call(self, method, params, real):
        with self.lock:
            self.calls.append((method, {k: v for k, v in params.items() if k != "Body"}))
        self._maybe_fail(method, params, before=True)
        result = real()
        self._maybe_fail(method, params, before=False)
        return result

    def keys(self):
        return sorted(self.meta)

    # -- S3 API subset ---------------------------------------------------------------------
    def head_bucket(self, Bucket):  # noqa: N803
        return self._call("head_bucket", {"Bucket": Bucket}, lambda: {})

    def head_object(self, Bucket, Key):  # noqa: N803
        def real():
            if Key not in self.meta:
                raise ClientError("404", 404, "Not Found")
            path = self._path(Bucket, Key)
            return {"ContentLength": os.path.getsize(path), **self.meta[Key]}
        return self._call("head_object", {"Bucket": Bucket, "Key": Key}, real)

    def get_object(self, Bucket, Key):  # noqa: N803
        def real():
            if Key not in self.meta:
                raise ClientError("NoSuchKey", 404)
            with open(self._path(Bucket, Key), "rb") as handle:
                return {"Body": io.BytesIO(handle.read()), "ETag": self.meta[Key]["ETag"]}
        return self._call("get_object", {"Bucket": Bucket, "Key": Key}, real)

    def _store(self, Bucket, Key, data, params):  # noqa: N803
        if "IfNoneMatch" in params and Key in self.meta:
            raise ClientError("PreconditionFailed", 412)
        if "IfMatch" in params and (Key not in self.meta or self.meta[Key]["ETag"] != params["IfMatch"]):
            raise ClientError("PreconditionFailed", 412)
        path = self._path(Bucket, Key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data)
        etag = '"' + hashlib.md5(data).hexdigest() + '"'  # noqa: S324 - S3 ETag semantics
        self.meta[Key] = {"ETag": etag, "ContentType": params.get("ContentType"),
                          "CacheControl": params.get("CacheControl"), "Metadata": params.get("Metadata", {})}
        return {"ETag": etag}

    def put_object(self, **params):
        data = params["Body"]
        data = data if isinstance(data, bytes) else data.read()
        return self._call("put_object", params, lambda: self._store(params["Bucket"], params["Key"], data, params))

    def create_multipart_upload(self, **params):
        def real():
            upload = f"up-{len(self.uploads) + 1}"
            self.uploads[upload] = {"key": params["Key"], "parts": {}, "params": params}
            return {"UploadId": upload}
        return self._call("create_multipart_upload", params, real)

    def upload_part(self, **params):
        def real():
            upload = self.uploads[params["UploadId"]]
            etag = '"' + hashlib.md5(params["Body"]).hexdigest() + '"'  # noqa: S324
            upload["parts"][params["PartNumber"]] = (etag, params["Body"])
            return {"ETag": etag}
        return self._call("upload_part", params, real)

    def list_parts(self, **params):
        def real():
            upload = self.uploads.get(params["UploadId"])
            if upload is None:
                raise ClientError("NoSuchUpload", 404)
            return {"Parts": [{"PartNumber": n, "ETag": e} for n, (e, _) in sorted(upload["parts"].items())]}
        return self._call("list_parts", params, real)

    def complete_multipart_upload(self, **params):
        def real():
            upload = self.uploads.pop(params["UploadId"])
            numbers = [p["PartNumber"] for p in params["MultipartUpload"]["Parts"]]
            data = b"".join(upload["parts"][n][1] for n in numbers)
            stored = dict(upload["params"])
            for key in ("IfNoneMatch", "IfMatch"):
                if key in params:
                    stored[key] = params[key]
            return self._store(params["Bucket"], params["Key"], data, stored)
        return self._call("complete_multipart_upload", params, real)

    def abort_multipart_upload(self, **params):
        return self._call("abort_multipart_upload", params, lambda: self.uploads.pop(params["UploadId"], None) and {})

    def list_multipart_uploads(self, **params):
        return self._call("list_multipart_uploads", params, lambda: {"Uploads": [
            {"Key": u["key"], "UploadId": i} for i, u in self.uploads.items() if u["key"].startswith(params["Prefix"])]})

    def list_objects_v2(self, **params):
        def real():
            prefix = params.get("Prefix", "")
            keys = [k for k in sorted(self.meta) if k.startswith(prefix)]
            if params.get("Delimiter"):
                common = sorted({prefix + k[len(prefix):].split("/", 1)[0] + "/" for k in keys
                                 if "/" in k[len(prefix):]})
                return {"CommonPrefixes": [{"Prefix": c} for c in common], "IsTruncated": False}
            return {"Contents": [{"Key": k} for k in keys[:params.get("MaxKeys", 1000)]], "IsTruncated": False}
        return self._call("list_objects_v2", params, real)

    def delete_objects(self, **params):
        def real():
            for item in params["Delete"]["Objects"]:
                self.meta.pop(item["Key"], None)
                path = self._path(params["Bucket"], item["Key"])
                if os.path.exists(path):
                    os.remove(path)
            return {}
        return self._call("delete_objects", params, real)


def dump_calls(client):
    return json.dumps(client.calls, default=str)
