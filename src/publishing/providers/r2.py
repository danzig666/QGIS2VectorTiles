"""
Cloudflare R2 through its S3 API.

* S3 API endpoint: ``https://<account id>.r2.cloudflarestorage.com`` (or an
  explicit endpoint, e.g. a jurisdiction endpoint), region ``auto``. This
  authenticated endpoint is never the public URL: maps are read from the
  explicitly configured public custom domain (or r2.dev for development,
  which Cloudflare rate-limits).
* R2 supports conditional PutObject (If-Match / If-None-Match), used for
  conflict-safe activation of ``current.json``.
* botocore's default request checksums (CRC32 on every upload, added in
  1.36) are limited to "when required", as R2 documents for S3 clients.
"""

import re
from typing import Dict
from urllib.parse import urlparse

from .s3 import S3Provider

_ACCOUNT = re.compile(r"^[0-9a-f]{32}$")
_ENDPOINT_HOST = re.compile(r"^([0-9a-f]{32})((?:\.[a-z0-9-]+)?)\.r2\.cloudflarestorage\.com$")


def parse_pasted(text: str) -> Dict[str, str]:
    """What a user pastes from the Cloudflare dashboard, as settings.

    Accepts the account id, the S3 API URL (``https://<id>.r2.cloudflarestorage.com``,
    optionally with ``/<bucket>``, or a jurisdiction endpoint such as
    ``<id>.eu.r2...``) or a dashboard address (``https://dash.cloudflare.com/<id>/r2/...``).
    Returns ``account_id`` and, when present, ``endpoint`` (jurisdictions only:
    the default endpoint is derived from the id) and ``bucket``; ``{}`` if
    nothing is recognised.
    """
    value = (text or "").strip().lower()
    if _ACCOUNT.match(value):
        return {"account_id": value}
    parsed = urlparse(value if "://" in value else f"https://{value}")
    host = parsed.hostname or ""
    segments = [part for part in parsed.path.split("/") if part]
    match = _ENDPOINT_HOST.match(host)
    if match:
        found = {"account_id": match.group(1)}
        if match.group(2):  # e.g. ".eu": keep the jurisdiction endpoint
            found["endpoint"] = f"https://{host}"
        if segments:
            found["bucket"] = segments[0]
        return found
    if host == "dash.cloudflare.com" and segments and _ACCOUNT.match(segments[0]):
        found = {"account_id": segments[0]}
        if len(segments) >= 4 and segments[1] == "r2" and segments[2] in ("default", "eu", "fedramp") \
                and segments[3] == "buckets" and len(segments) >= 5:
            found["bucket"] = segments[4]
        return found
    return {}


class R2Provider(S3Provider):
    kind = "r2"

    @staticmethod
    def _endpoint(destination) -> str:
        if destination.endpoint:
            return destination.endpoint
        account = (destination.account_id or "").strip()
        if not account or not account.replace("-", "").isalnum():
            from ..errors import PublishingError  # pylint: disable=import-outside-toplevel
            raise PublishingError("Q2VT_PUB_DESTINATION", "Enter the Cloudflare account id.")
        return f"https://{account}.r2.cloudflarestorage.com"

    def client_config(self, Config):  # noqa: N803
        try:
            return Config(signature_version="s3v4", retries={"max_attempts": 1, "mode": "standard"},
                          connect_timeout=20, read_timeout=120, s3={"addressing_style": "path"},
                          request_checksum_calculation="when_required",
                          response_checksum_validation="when_required")
        except TypeError:  # older botocore without these options
            return super().client_config(Config)
