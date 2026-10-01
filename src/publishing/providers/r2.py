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

from .s3 import S3Provider


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
