"""Hosting providers: local folder, S3-compatible object storage, Cloudflare R2, SSH / SFTP server."""

from .base import Credentials, Provider, ProviderError, provider_for  # noqa: F401
