"""
Provider contract (plan §12.2). Authenticated operations (object API) are
separate from public reads (``public_verify.py`` reads the public URL).

A provider only ever writes below its publication prefix; keys are built
from validated relative paths. Nothing here walks a local folder: uploads
consume the release.json inventory.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

from ..errors import PublishingError
from ..validation import safe_relative_path


@dataclass(frozen=True)
class Credentials:
    """Object-storage credentials for one operation (never stored in profiles)."""

    access_key_id: str
    secret_access_key: str
    session_token: str = ""

    def secrets(self) -> List[str]:
        return [s for s in (self.secret_access_key, self.session_token) if s]

    def __repr__(self) -> str:  # never print secrets
        return f"Credentials(access_key_id={self.access_key_id[:4]}…)"


class ProviderError(PublishingError):
    """A provider failure with a stable code; ``retryable`` for transient ones."""

    def __init__(self, code: str, message: str = "", retryable: bool = False, detail: str = ""):
        super().__init__(code, message or None, detail)
        self.retryable = retryable


class Provider:
    """Interface; see S3Provider for the reference implementation."""

    kind = "base"
    conditional_writes = False

    def __init__(self, prefix: str):
        prefix = prefix.strip("/")
        if not prefix:
            raise PublishingError("Q2VT_PUB_DESTINATION", "A publication prefix is required.")
        self.prefix = prefix

    def key(self, relative: str) -> str:
        """Object key of a publication-relative path (contained in the prefix)."""
        return f"{self.prefix}/{safe_relative_path(relative)}"

    # -- object operations --------------------------------------------------------
    def inspect(self) -> List[Tuple[str, bool, str]]:
        raise NotImplementedError

    def head(self, relative: str) -> Optional[dict]:
        raise NotImplementedError

    def put_file(self, relative: str, path: str, content_type: str, cache_control: str,
                 sha256: str, progress=None, journal=None) -> str:
        raise NotImplementedError

    def put_bytes(self, relative: str, data: bytes, content_type: str, cache_control: str,
                  if_match: Optional[str] = None, if_none_match: bool = False) -> str:
        raise NotImplementedError

    def get_bytes(self, relative: str) -> Optional[Tuple[bytes, str]]:
        raise NotImplementedError

    def list_releases(self) -> List[str]:
        raise NotImplementedError

    def delete_release(self, release_id: str) -> int:
        raise NotImplementedError

    def incomplete_uploads(self) -> List[dict]:
        return []

    def abort_upload(self, relative: str, upload_id: str) -> None:
        pass


def provider_for(destination, credentials: Optional[Credentials], prefix: str, **kwargs) -> Provider:
    """The provider of a DestinationConfig."""
    if destination.kind == "r2":
        from .r2 import R2Provider  # pylint: disable=import-outside-toplevel
        return R2Provider(destination, credentials, prefix, **kwargs)
    if destination.kind == "s3":
        from .s3 import S3Provider  # pylint: disable=import-outside-toplevel
        return S3Provider(destination, credentials, prefix, **kwargs)
    from .local import LocalProvider  # pylint: disable=import-outside-toplevel
    return LocalProvider(destination, prefix, **kwargs)
