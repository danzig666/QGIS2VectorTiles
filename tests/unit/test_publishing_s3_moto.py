"""S3 provider with the real (vendored) boto3/botocore against a local moto
S3 server: signing, conditional writes, multipart, listing and a full
publish verified through the server's public HTTP URL.

Opt-in development check (moto is not a plugin dependency):

    pip install --target /tmp/moto_env "moto[server]"
    Q2VT_MOTO_PATH=/tmp/moto_env Q2VT_MOTO_PYTHON=python3.11 pytest tests/unit/test_publishing_s3_moto.py

Generic S3 mocks do not prove Cloudflare public-domain/CORS behaviour (plan
§17.1); that needs an authorised R2 sandbox.
"""

import os
import socket
import subprocess
import time
import urllib.request

import pytest

from publishing.errors import PublishingError
from publishing.models import DestinationConfig, PublicationProfile, ReleaseState
from publishing.providers.base import Credentials
from publishing.providers.s3 import S3Provider, sdk_versions
from publishing.deployments import publish
from publishing.web_builder import build_release
from publishing_fixtures import fixture_bundle

MOTO_PATH = os.environ.get("Q2VT_MOTO_PATH", "")
MOTO_PYTHON = os.environ.get("Q2VT_MOTO_PYTHON", "python3")
pytestmark = pytest.mark.skipif(not MOTO_PATH, reason="moto server not configured (Q2VT_MOTO_PATH)")


@pytest.fixture(scope="module")
def moto():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([MOTO_PYTHON, "-m", "moto.server", "-p", str(port)],
                               env={**os.environ, "PYTHONPATH": MOTO_PATH},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(url, timeout=1)
            break
        except OSError:
            time.sleep(0.2)
    yield url
    process.terminate()
    process.wait(timeout=10)


def _provider(moto, bucket="maps", prefix="maps/felho"):
    destination = DestinationConfig(kind="s3", endpoint=moto, bucket=bucket, region="us-east-1")
    provider = S3Provider(destination, Credentials("testing", "testing-secret"), prefix,
                          sleep=lambda s: None)
    try:
        provider.client.create_bucket(Bucket=bucket)
    except Exception:  # noqa: BLE001 - exists
        pass
    return provider


def test_vendored_sdk_is_used_without_ambient_credentials(moto, monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AMBIENT-SHOULD-NOT-BE-USED")
    provider = _provider(moto)
    assert provider.client._request_signer._credentials.access_key == "testing"
    assert sdk_versions()["boto3"] == "1.43.106"


def test_conditional_writes(moto):
    provider = _provider(moto)
    etag = provider.put_bytes("probe.json", b"{}", "application/json", "no-cache", if_none_match=True)
    with pytest.raises(PublishingError) as error:
        provider.put_bytes("probe.json", b"{}", "application/json", "no-cache", if_none_match=True)
    assert error.value.code == "Q2VT_PUB_ACTIVATION_CONFLICT"
    provider.put_bytes("probe.json", b'{"a":1}', "application/json", "no-cache", if_match=etag)
    with pytest.raises(PublishingError):
        provider.put_bytes("probe.json", b"{}", "application/json", "no-cache", if_match=etag)  # stale


def test_multipart_and_listing(moto, tmp_path):
    provider = _provider(moto)
    provider.part_size = 5 * 1024 * 1024
    data = os.urandom(11 * 1024 * 1024 + 7)
    path = tmp_path / "big.bin"
    path.write_bytes(data)
    provider.put_file("releases/r-20260101T000000Z-aaaaaaaa/data/big.bin", str(path),
                      "application/octet-stream", "public, max-age=31536000, immutable", "cafe")
    head = provider.head("releases/r-20260101T000000Z-aaaaaaaa/data/big.bin")
    assert head["size"] == len(data) and head["metadata"]["sha256"] == "cafe"
    assert provider.list_releases() == ["r-20260101T000000Z-aaaaaaaa"]
    assert provider.delete_release("r-20260101T000000Z-aaaaaaaa") == 1
    assert provider.list_releases() == []


def test_full_publish_against_moto(moto, tmp_path):
    bundle = fixture_bundle(str(tmp_path / "export"))
    profile = PublicationProfile(title="Moto", slug="moto")
    profile.destination = DestinationConfig(kind="s3", endpoint=moto, bucket="pub", public_base_url=f"{moto}/pub")
    release = build_release(bundle, profile, str(tmp_path / "local" / "moto"))
    provider = _provider(moto, bucket="pub", prefix="maps/moto")
    # Public read of the bucket (on R2: the connected public custom domain).
    import json  # pylint: disable=import-outside-toplevel
    provider.client.put_bucket_policy(Bucket="pub", Policy=json.dumps({
        "Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": "*",
                                                "Action": ["s3:GetObject", "s3:HeadObject"],  # moto: HEAD is its own action
                                                "Resource": "arn:aws:s3:::pub/*"}]}))
    result = publish(release, profile, provider, str(tmp_path / "work"))
    failed = [(c.name, c.detail) for c in result.checks.checks if not c.ok]
    assert result.state == ReleaseState.PUBLISHED, (result.message, failed)
    assert {"range:0-126", "tile", "manifest"} <= {c.name for c in result.checks.checks}
