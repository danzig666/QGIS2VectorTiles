"""
S3-compatible object storage provider (boto3/botocore).

* SDK: the QGIS Python's own boto3 when installed, else the pinned vendored
  copy (src/publishing/vendor/s3, appended at the *end* of sys.path so other
  plugins' packages keep precedence). Explicit credentials only: the SDK
  never falls back to ambient credentials (environment, ~/.aws, instance
  roles). TLS certificates are always verified.
* Immutable release objects are created with ``If-None-Match: *`` where the
  service supports conditional writes; ``current.json`` is replaced with
  ``If-Match: <etag>`` (or created with ``If-None-Match: *``).
* Large files use multipart uploads; a private journal (never published)
  records upload ids and part ETags so an interrupted upload resumes.
* Transient failures (timeouts, 429, 5xx, connection errors) are retried
  with exponential backoff and jitter within a bounded budget; permission
  and credential errors are not retried.
"""

import json
import os
import random
import sys
import threading
import time
from typing import Callable, List, Optional, Tuple

from ..errors import Cancelled, PublishingError
from .base import Credentials, Provider, ProviderError

VENDOR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor", "s3")
MIN_PART = 5 * 1024 * 1024
DEFAULT_PART = 16 * 1024 * 1024
MAX_PARTS = 10000
_SDK_LOCK = threading.Lock()


def sdk():
    """(boto3, botocore.config.Config, botocore.exceptions) — system first."""
    with _SDK_LOCK:
        try:
            import boto3  # pylint: disable=import-outside-toplevel
        except ImportError:
            if VENDOR not in sys.path:
                sys.path.append(VENDOR)
            try:
                import boto3  # pylint: disable=import-outside-toplevel
            except ImportError as error:
                raise PublishingError("Q2VT_PUB_DEPENDENCY",
                                      f"The S3 library could not be loaded: {error}") from error
        from botocore.config import Config  # pylint: disable=import-outside-toplevel
        from botocore import exceptions  # pylint: disable=import-outside-toplevel
        return boto3, Config, exceptions


def sdk_versions() -> dict:
    boto3, _, _ = sdk()
    import botocore  # pylint: disable=import-outside-toplevel
    return {"boto3": boto3.__version__, "botocore": botocore.__version__,
            "vendored": os.path.dirname(os.path.dirname(boto3.__file__)) == VENDOR}


RETRYABLE_CODES = {"RequestTimeout", "RequestTimeTooSkewed", "SlowDown", "Throttling",
                   "ThrottlingException", "InternalError", "ServiceUnavailable",
                   "TooManyRequests", "503", "500", "502", "504", "429"}
CREDENTIAL_CODES = {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch",
                    "ExpiredToken", "InvalidToken", "Unauthorized", "403", "401"}
CONFLICT_CODES = {"PreconditionFailed", "412", "ConditionalRequestConflict", "409"}


class S3Provider(Provider):
    kind = "s3"

    def __init__(self, destination, credentials: Optional[Credentials], prefix: str, client=None,
                 part_size: int = DEFAULT_PART, max_retries: int = 5,
                 sleep: Callable[[float], None] = time.sleep, conditional_writes: Optional[bool] = None):
        super().__init__(prefix)
        if not destination.bucket:
            raise PublishingError("Q2VT_PUB_DESTINATION", "No bucket configured.")
        self.destination = destination
        self.bucket = destination.bucket
        self.credentials = credentials
        self.endpoint = self._endpoint(destination)
        self.region = destination.region or "auto"
        self.part_size = max(MIN_PART, part_size)
        self.max_retries = max_retries
        self.sleep = sleep
        self.conditional_writes = destination.conditional_writes if conditional_writes is None \
            else conditional_writes
        self._client = client

    @staticmethod
    def _endpoint(destination) -> str:
        return destination.endpoint

    # -- client ------------------------------------------------------------------------
    def client_config(self, Config):  # noqa: N803 - botocore class
        return Config(signature_version="s3v4", retries={"max_attempts": 1, "mode": "standard"},
                      connect_timeout=20, read_timeout=120, s3={"addressing_style": "path"})

    @property
    def client(self):
        if self._client is None:
            if self.credentials is None:
                raise PublishingError("Q2VT_PUB_CREDENTIALS", "No credentials for this destination.")
            boto3, Config, _ = sdk()
            session = boto3.session.Session(
                aws_access_key_id=self.credentials.access_key_id,
                aws_secret_access_key=self.credentials.secret_access_key,
                aws_session_token=self.credentials.session_token or None,
                region_name=self.region)
            # A fresh Session with explicit keys: no environment/profile/instance
            # credentials are consulted. verify=True: certificates are checked.
            self._client = session.client("s3", endpoint_url=self.endpoint or None, verify=True,
                                          config=self.client_config(Config))
        return self._client

    # -- error handling ----------------------------------------------------------------
    @staticmethod
    def _code(error) -> Tuple[str, int]:
        response = getattr(error, "response", None) or {}
        code = str((response.get("Error") or {}).get("Code", ""))
        status = int((response.get("ResponseMetadata") or {}).get("HTTPStatusCode", 0) or 0)
        return code, status

    def _classify(self, error) -> ProviderError:
        code, status = self._code(error)
        name = type(error).__name__
        text = self._redact(f"{name}: {code or ''} {status or ''} {error}")
        if code in CONFLICT_CODES or str(status) in CONFLICT_CODES:
            return ProviderError("Q2VT_PUB_ACTIVATION_CONFLICT", "The object changed meanwhile.", detail=text)
        if code in CREDENTIAL_CODES or str(status) in CREDENTIAL_CODES:
            return ProviderError("Q2VT_PUB_CREDENTIALS", "The storage refused the credentials or "
                                 "the key has no permission for this bucket/prefix.", detail=text)
        if code in ("NoSuchBucket",):
            return ProviderError("Q2VT_PUB_DESTINATION", f"Bucket '{self.bucket}' does not exist.", detail=text)
        retryable = code in RETRYABLE_CODES or str(status) in RETRYABLE_CODES or status >= 500 or \
            name in ("EndpointConnectionError", "ConnectTimeoutError", "ReadTimeoutError",
                     "ConnectionClosedError", "ConnectionError", "ProxyConnectionError",
                     "ResponseStreamingError", "IncompleteReadError")
        return ProviderError("Q2VT_PUB_UPLOAD", "Object storage request failed.", retryable, text)

    def _redact(self, text: str) -> str:
        for secret in (self.credentials.secrets() if self.credentials else []):
            text = text.replace(secret, "***")
        return text

    def _call(self, name: str, progress=None, **params):
        """Call a client method with bounded retries for transient failures."""
        attempt = 0
        while True:
            if progress is not None and progress.canceled():
                raise Cancelled()
            try:
                return getattr(self.client, name)(**params)
            except Exception as error:  # noqa: BLE001 - classified below
                if isinstance(error, (PublishingError, Cancelled)):
                    raise
                failure = self._classify(error)
                attempt += 1
                if not failure.retryable or attempt > self.max_retries:
                    raise failure from error
                delay = min(30.0, 0.5 * 2 ** attempt) * (0.5 + random.random())
                self.sleep(delay)

    def _not_found(self, error) -> bool:
        code, status = self._code(error)
        return code in ("404", "NoSuchKey", "NotFound") or status == 404

    # -- operations --------------------------------------------------------------------
    def inspect(self) -> List[Tuple[str, bool, str]]:
        checks = []
        try:
            self._call("head_bucket", Bucket=self.bucket)
            checks.append(("bucket", True, f"Bucket '{self.bucket}' reachable."))
        except PublishingError as error:
            checks.append(("bucket", False, error.message))
            return checks
        try:
            self._call("list_objects_v2", Bucket=self.bucket, Prefix=self.prefix + "/", MaxKeys=1)
            checks.append(("list", True, f"Prefix '{self.prefix}/' can be listed."))
        except PublishingError as error:
            checks.append(("list", False, error.message))
        return checks

    def head(self, relative: str) -> Optional[dict]:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=self.key(relative))
        except Exception as error:  # noqa: BLE001
            if self._not_found(error):
                return None
            raise self._classify(error) from error
        return {"size": response.get("ContentLength"), "etag": response.get("ETag"),
                "metadata": response.get("Metadata") or {}, "contentType": response.get("ContentType")}

    def put_bytes(self, relative: str, data: bytes, content_type: str, cache_control: str,
                  if_match: Optional[str] = None, if_none_match: bool = False, sha256: str = "") -> str:
        params = {"Bucket": self.bucket, "Key": self.key(relative), "Body": data,
                  "ContentType": content_type, "CacheControl": cache_control}
        if sha256:
            params["Metadata"] = {"sha256": sha256}
        if if_match and self.conditional_writes:
            params["IfMatch"] = if_match
        if if_none_match and self.conditional_writes:
            params["IfNoneMatch"] = "*"
        response = self._call("put_object", **params)
        return response.get("ETag", "")

    def get_bytes(self, relative: str) -> Optional[Tuple[bytes, str]]:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self.key(relative))
        except Exception as error:  # noqa: BLE001
            if self._not_found(error):
                return None
            raise self._classify(error) from error
        return response["Body"].read(), response.get("ETag", "")

    def put_file(self, relative: str, path: str, content_type: str, cache_control: str,
                 sha256: str, progress=None, journal=None) -> str:
        size = os.path.getsize(path)
        existing = self.head(relative)
        if existing and existing["metadata"].get("sha256") == sha256 and existing["size"] == size:
            return existing["etag"]  # already uploaded (retry/resume)
        if existing:
            raise ProviderError("Q2VT_PUB_UPLOAD", f"{relative} already exists with other content; "
                                "release objects are immutable.")
        if size <= self.part_size:
            with open(path, "rb") as handle:
                data = handle.read()
            return self.put_bytes(relative, data, content_type, cache_control,
                                  if_none_match=True, sha256=sha256)
        return self._multipart(relative, path, size, content_type, cache_control, sha256,
                               progress, journal)

    def copy_object(self, source_relative: str, relative: str, content_type: str,
                    cache_control: str, sha256: str) -> Optional[str]:
        existing = self.head(relative)
        if existing and existing["metadata"].get("sha256") == sha256:
            return existing["etag"]  # already there (retry/resume)
        if existing:
            raise ProviderError("Q2VT_PUB_UPLOAD", f"{relative} already exists with other content; "
                                "release objects are immutable.")
        response = self._call(
            "copy_object", Bucket=self.bucket, Key=self.key(relative),
            CopySource={"Bucket": self.bucket, "Key": self.key(source_relative)},
            MetadataDirective="REPLACE", ContentType=content_type, CacheControl=cache_control,
            Metadata={"sha256": sha256})
        return (response.get("CopyObjectResult") or {}).get("ETag", "")

    def _multipart(self, relative, path, size, content_type, cache_control, sha256, progress, journal):
        part_size = max(self.part_size, -(-size // MAX_PARTS))
        key = self.key(relative)
        state = journal.get(relative) if journal is not None else None
        upload_id = state.get("uploadId") if state and state.get("sha256") == sha256 else None
        parts = {}
        if upload_id:
            try:  # reconcile with what the service has
                listed = self._call("list_parts", Bucket=self.bucket, Key=key, UploadId=upload_id)
                parts = {p["PartNumber"]: p["ETag"] for p in listed.get("Parts", [])}
            except PublishingError:
                upload_id = None
        if not upload_id:
            created = self._call("create_multipart_upload", Bucket=self.bucket, Key=key,
                                 ContentType=content_type, CacheControl=cache_control,
                                 Metadata={"sha256": sha256})
            upload_id = created["UploadId"]
            parts = {}
        if journal is not None:
            journal.update(relative, {"uploadId": upload_id, "sha256": sha256, "size": size,
                                      "partSize": part_size, "parts": parts})
        count = -(-size // part_size)
        with open(path, "rb") as handle:
            for number in range(1, count + 1):
                if number in parts:
                    continue
                if progress is not None:
                    progress.check()
                handle.seek((number - 1) * part_size)
                body = handle.read(part_size)
                response = self._call("upload_part", progress, Bucket=self.bucket, Key=key,
                                      UploadId=upload_id, PartNumber=number, Body=body)
                parts[number] = response["ETag"]
                if journal is not None:
                    journal.update(relative, {"parts": parts})
                if progress is not None:
                    progress.update(number / count, f"{relative}: part {number}/{count}")
        params = {"Bucket": self.bucket, "Key": key, "UploadId": upload_id,
                  "MultipartUpload": {"Parts": [{"PartNumber": n, "ETag": parts[n]} for n in sorted(parts)]}}
        if self.conditional_writes:
            params["IfNoneMatch"] = "*"
        response = self._call("complete_multipart_upload", **params)
        if journal is not None:
            journal.update(relative, {"completed": True})
        return response.get("ETag", "")

    def incomplete_uploads(self) -> List[dict]:
        response = self._call("list_multipart_uploads", Bucket=self.bucket, Prefix=self.prefix + "/")
        return [{"key": u["Key"], "uploadId": u["UploadId"], "initiated": str(u.get("Initiated", ""))}
                for u in response.get("Uploads", [])]

    def abort_upload(self, relative: str, upload_id: str) -> None:
        self._call("abort_multipart_upload", Bucket=self.bucket, Key=self.key(relative), UploadId=upload_id)

    def list_releases(self) -> List[str]:
        from ..web_builder import RELEASE_ID  # pylint: disable=import-outside-toplevel
        releases, token = set(), None
        while True:
            params = {"Bucket": self.bucket, "Prefix": f"{self.prefix}/releases/", "Delimiter": "/"}
            if token:
                params["ContinuationToken"] = token
            response = self._call("list_objects_v2", **params)
            for item in response.get("CommonPrefixes", []):
                name = item["Prefix"].rstrip("/").rsplit("/", 1)[-1]
                if RELEASE_ID.match(name):
                    releases.add(name)
            if not response.get("IsTruncated"):
                return sorted(releases)
            token = response.get("NextContinuationToken")

    def delete_release(self, release_id: str) -> int:
        from ..web_builder import RELEASE_ID  # pylint: disable=import-outside-toplevel
        if not RELEASE_ID.match(release_id):
            raise PublishingError("Q2VT_PUB_PATH_UNSAFE", release_id)
        prefix = f"{self.prefix}/releases/{release_id}/"
        deleted, token = 0, None
        while True:
            params = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                params["ContinuationToken"] = token
            response = self._call("list_objects_v2", **params)
            keys = [{"Key": o["Key"]} for o in response.get("Contents", []) if o["Key"].startswith(prefix)]
            for start in range(0, len(keys), 1000):
                self._call("delete_objects", Bucket=self.bucket,
                           Delete={"Objects": keys[start:start + 1000], "Quiet": True})
                deleted += len(keys[start:start + 1000])
            if not response.get("IsTruncated"):
                return deleted
            token = response.get("NextContinuationToken")

    # -- pointer ------------------------------------------------------------------------
    def read_pointer(self) -> Tuple[Optional[dict], Optional[str]]:
        found = self.get_bytes("current.json")
        if found is None:
            return None, None
        data, etag = found
        try:
            return json.loads(data.decode("utf-8")), etag
        except ValueError as error:
            raise PublishingError("Q2VT_PUB_ACTIVATION", "The remote current.json is not JSON.") from error


class Journal:
    """Private resumable-upload journal (JSON next to the local bundle,
    never in the publication). Holds no credentials."""

    def __init__(self, path: str, target: dict):
        self.path = path
        self.target = target
        self.data = {"target": target, "files": {}}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as handle:
                    old = json.load(handle)
                if old.get("target") == target:
                    self.data = old
            except (OSError, ValueError):
                pass
        self._lock = threading.Lock()

    def get(self, relative: str) -> dict:
        return dict(self.data["files"].get(relative, {}))

    def update(self, relative: str, values: dict) -> None:
        with self._lock:
            entry = self.data["files"].setdefault(relative, {})
            entry.update({k: (dict(v) if isinstance(v, dict) else v) for k, v in values.items()})
            if "parts" in entry:
                entry["parts"] = {int(k): v for k, v in entry["parts"].items()}
            tmp = f"{self.path}.tmp"
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=1, default=str)
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def done(self, relative: str, etag: str) -> None:
        self.update(relative, {"done": True, "etag": etag})
