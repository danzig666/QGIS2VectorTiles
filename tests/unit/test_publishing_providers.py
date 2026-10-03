"""Remote publication protocol (PUB-14/15, plan A17-A20, A25) on a fake
S3 client whose bucket is served by the range-capable preview server as
the "public domain": inventory-only uploads inside the prefix, retries,
credential errors, resumable multipart, public checks before activation,
conditional activation and conflicts, ambiguous responses, rollback,
retention, leak refusal."""

import http.server
import json
import os
import threading

import pytest

from publishing.deployments import (activate, apply_retention, publish, retention_plan,
                                    rollback, upload_release)
from publishing.errors import PublishingError
from publishing.models import PublicationProfile, ReleaseState
from publishing.preview_server import PreviewServer
from publishing.providers.base import Credentials
from publishing.providers.r2 import R2Provider
from publishing.providers.s3 import Journal, S3Provider
from publishing.web_builder import build_release
from publishing_fake_s3 import ClientError, FakeS3Client
from publishing_fixtures import fixture_bundle

SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"


@pytest.fixture
def env(tmp_path):
    bundle = fixture_bundle(str(tmp_path / "export"))
    profile = PublicationProfile(title="Felhő teszt", slug="felho", locale="hu")
    profile.destination.kind = "r2"
    profile.destination.account_id = "abc123"
    profile.destination.bucket = "maps"
    root = tmp_path / "bucket-root"
    client = FakeS3Client(str(root))
    server = PreviewServer(str(root / "maps")).start()
    profile.destination.public_base_url = server.url("").rstrip("/")
    provider = R2Provider(profile.destination, Credentials("AKIAEXAMPLE", SECRET), "maps/felho",
                          client=client, sleep=lambda s: None)
    yield {"bundle": bundle, "profile": profile, "client": client, "server": server,
           "provider": provider, "tmp": tmp_path}
    server.stop()


def _release(env, name="local"):
    return build_release(env["bundle"], env["profile"], str(env["tmp"] / name / "felho"))


def _publish(env, release, **kwargs):
    return publish(release, env["profile"], env["provider"], str(env["tmp"] / "work"), **kwargs)


def test_r2_endpoint_and_region():
    from publishing.models import DestinationConfig
    provider = R2Provider(DestinationConfig(kind="r2", account_id="0123abcd", bucket="b"), None, "maps/x")
    assert provider.endpoint == "https://0123abcd.r2.cloudflarestorage.com" and provider.region == "auto"


def test_publish_uploads_verifies_and_activates(env):
    release = _release(env)
    result = _publish(env, release)
    assert result.state == ReleaseState.PUBLISHED, result.message
    assert result.checks.ok, [c for c in result.checks.checks if not c.ok]
    names = {c.name for c in result.checks.checks}
    assert {"range:0-126", "tile", "archive:head", "encoding:archive", "manifest", "activation"} <= names
    keys = env["client"].keys()
    assert all(k.startswith("maps/felho/") for k in keys)  # nothing outside the prefix
    inventory = json.load(open(os.path.join(release.release_dir, "release.json"), encoding="utf-8"))
    expected = {f"maps/felho/releases/{release.release_id}/{f['path']}" for f in inventory["files"]}
    expected |= {f"maps/felho/releases/{release.release_id}/release.json", "maps/felho/index.html",
                 "maps/felho/current.json"}
    expected |= {f"maps/felho/{n}" for n in os.listdir(release.publication_dir) if n.startswith("bootstrap.")}
    assert set(keys) == expected  # exactly the inventory: no recursive folder upload
    meta = env["client"].meta
    archive = f"maps/felho/releases/{release.release_id}/data/map.pmtiles"
    assert meta[archive]["ContentType"] == "application/vnd.pmtiles"
    assert meta[archive]["CacheControl"].endswith("immutable")
    assert meta["maps/felho/current.json"]["CacheControl"].startswith("no-cache")
    puts = [c for c in env["client"].calls if c[0] in ("put_object", "complete_multipart_upload")]
    assert puts[-1][1]["Key"] == "maps/felho/current.json"  # activation is the last write
    assert puts[-1][1]["IfNoneMatch"] == "*"  # first publication: create-if-absent
    order = [c[1]["Key"] for c in puts]
    assert order.index(f"maps/felho/releases/{release.release_id}/release.json") > \
        order.index(archive)  # inventory last
    pointer = json.loads(open(os.path.join(env["client"].root, "maps", "maps", "felho", "current.json")).read()) \
        if False else env["provider"].read_pointer()[0]
    assert pointer["releaseId"] == release.release_id


def test_second_release_replaces_conditionally_and_old_one_stays(env):
    first = _publish(env, _release(env, "a"))
    second_release = _release(env, "b")
    second = _publish(env, second_release)
    assert second.state == ReleaseState.PUBLISHED and second.previous_release == first.release_id
    put = [c for c in env["client"].calls if c[0] == "put_object" and c[1]["Key"].endswith("current.json")][-1]
    assert put[1]["IfMatch"]  # conditional replace
    assert env["provider"].list_releases() == sorted([first.release_id, second.release_id])


def test_failed_public_check_keeps_the_previous_release(env):
    """A18 remotely: a host without byte ranges -> uploaded, not active."""
    first = _publish(env, _release(env, "a"))
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=os.path.join(env["client"].root, "maps"), **k)
    plain = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=plain.serve_forever, daemon=True).start()
    try:
        env["profile"].destination.public_base_url = f"http://127.0.0.1:{plain.server_address[1]}"
        result = _publish(env, _release(env, "b"))
    finally:
        plain.shutdown()
        plain.server_close()
    assert result.state == ReleaseState.UPLOADED_NOT_ACTIVE
    assert result.code == "Q2VT_PUB_RANGE_UNSUPPORTED"
    assert env["provider"].read_pointer()[0]["releaseId"] == first.release_id


def test_concurrent_publishers_conflict_safely(env):
    """A19: a stale ETag never overwrites a newer pointer."""
    first = _publish(env, _release(env, "a"))
    pointer, etag = env["provider"].read_pointer()
    other = _release(env, "other")  # another machine publishes meanwhile
    _publish(env, other)
    mine = _release(env, "mine")
    upload_release(env["provider"], mine.release_dir, mine.release_id, str(env["tmp"] / "j.json"))
    with pytest.raises(PublishingError) as error:
        activate(env["provider"], env["profile"], mine.release_id, etag, first.release_id)
    assert error.value.code == "Q2VT_PUB_ACTIVATION_CONFLICT"
    assert env["provider"].read_pointer()[0]["releaseId"] == other.release_id
    assert mine.release_id in env["provider"].list_releases()  # kept, not current


def test_conflict_reported_by_publish(env):
    _publish(env, _release(env, "a"))
    client = env["client"]
    # Another publisher moves the pointer right after our upload started.
    client.inject("put_object", ClientError("PreconditionFailed", 412),
                  when=lambda p: p["Key"].endswith("current.json"))
    result = _publish(env, _release(env, "b"))
    assert result.state == ReleaseState.CONFLICT and result.code == "Q2VT_PUB_ACTIVATION_CONFLICT"


def test_lost_activation_response_is_reconciled(env):
    client = env["client"]
    client.inject("put_object", ClientError("InternalError", 500), times=10,
                  when=lambda p: p["Key"].endswith("current.json"), after=True)
    release = _release(env)
    result = _publish(env, release)
    assert result.state == ReleaseState.PUBLISHED, result.message  # write happened; read back
    assert env["provider"].read_pointer()[0]["releaseId"] == release.release_id


def test_transient_errors_are_retried_permission_errors_are_not(env):
    client = env["client"]
    client.inject("put_object", ClientError("SlowDown", 503), times=2)
    sleeps = []
    env["provider"].sleep = sleeps.append
    assert _publish(env, _release(env, "a")).state == ReleaseState.PUBLISHED
    assert len(sleeps) == 2
    client.inject("put_object", ClientError("AccessDenied", 403), times=99)
    sleeps.clear()
    result = _publish(env, _release(env, "b"))
    assert result.state == ReleaseState.FAILED and result.code == "Q2VT_PUB_CREDENTIALS"
    assert not sleeps
    assert SECRET not in result.message  # redacted


def test_multipart_upload_resumes_from_the_journal(env, tmp_path):
    provider = env["provider"]
    provider.part_size = 64 * 1024  # small parts for the test
    data = os.urandom(64 * 1024 * 5 + 123)
    path = tmp_path / "big.bin"
    path.write_bytes(data)
    journal = Journal(str(tmp_path / "journal.json"), {"t": 1})
    env["client"].inject("upload_part", ClientError("AccessDenied", 403), when=lambda p: p["PartNumber"] == 3)
    with pytest.raises(PublishingError):
        provider.put_file("big.bin", str(path), "application/octet-stream", "x", "deadbeef", journal=journal)
    assert sorted(journal.get("big.bin")["parts"]) == [1, 2]
    calls = len(env["client"].calls)
    journal = Journal(str(tmp_path / "journal.json"), {"t": 1})  # a new run reads it back
    provider.put_file("big.bin", str(path), "application/octet-stream", "x", "deadbeef", journal=journal)
    uploaded = [c[1]["PartNumber"] for c in env["client"].calls[calls:] if c[0] == "upload_part"]
    assert uploaded == [3, 4, 5, 6]  # parts 1-2 not sent again
    assert open(os.path.join(env["client"].root, "maps", "maps", "felho", "big.bin"), "rb").read() == data


def test_rollback_and_retention(env):
    ids = [_publish(env, _release(env, name)).release_id for name in ("a", "b", "c")]
    result = rollback(env["provider"], env["profile"], ids[0])
    assert result.state == ReleaseState.PUBLISHED and result.previous_release == ids[2]
    uploads = [c for c in env["client"].calls[-6:] if c[0] in ("upload_part", "create_multipart_upload")]
    assert not uploads  # A20: no re-upload
    plan = retention_plan(env["provider"], keep=1)
    assert ids[0] not in plan and set(plan) == {ids[1], ids[2]}
    apply_retention(env["provider"], plan)
    assert env["provider"].list_releases() == [ids[0]]
    with pytest.raises(PublishingError):
        apply_retention(env["provider"], [ids[0]])  # never the current release


def test_prefix_of_another_publication_is_refused(env):
    _publish(env, _release(env, "a"))
    stranger = PublicationProfile(title="Más", slug="felho")
    stranger.destination = env["profile"].destination
    other_release = build_release(env["bundle"], stranger, str(env["tmp"] / "s" / "felho"))
    result = publish(other_release, stranger, env["provider"], str(env["tmp"] / "work2"))
    assert result.state == ReleaseState.FAILED and result.code == "Q2VT_PUB_DESTINATION"


def test_credentials_in_files_are_never_uploaded(env):
    release = _release(env)
    with open(os.path.join(release.release_dir, "public-diagnostics.json"), "a", encoding="utf-8") as handle:
        handle.write(" ")  # (inventory now differs too; leak scan runs first)
    with open(os.path.join(release.release_dir, "public-diagnostics.json"), "w", encoding="utf-8") as handle:
        json.dump({"oops": SECRET}, handle)
    result = _publish(env, release, secrets=[SECRET])
    assert result.state == ReleaseState.FAILED and result.code == "Q2VT_PUB_SECRET_LEAK"
    assert not [c for c in env["client"].calls if c[0] == "put_object"]


def test_no_ambient_credentials():
    from publishing.models import DestinationConfig
    provider = S3Provider(DestinationConfig(kind="s3", endpoint="https://s3.example", bucket="b"),
                          None, "maps/x")
    with pytest.raises(PublishingError) as error:
        provider.client  # pylint: disable=pointless-statement
    assert error.value.code == "Q2VT_PUB_CREDENTIALS"
    assert "secret" not in repr(Credentials("AKIA1234", "secret-value")).lower()


def test_unchanged_files_are_copied_from_the_previous_release(env):
    """A new release of mostly unchanged files: the bucket copies them from
    the current release (no upload); headers and checksums are the new
    release's; a file missing from the old release is uploaded instead."""
    first_release = _release(env, "a")
    first = _publish(env, first_release)
    second_release = _release(env, "b")
    inventory = json.load(open(os.path.join(second_release.release_dir, "release.json"), encoding="utf-8"))
    old = json.load(open(os.path.join(first_release.release_dir, "release.json"), encoding="utf-8"))
    same = {(f["sha256"], f["size"]) for f in old["files"]}
    gone = f"maps/felho/releases/{first.release_id}/data/map.pmtiles"
    os.remove(env["client"]._path("maps", gone))  # pylint: disable=protected-access
    del env["client"].meta[gone]                    # e.g. removed by hand: must be uploaded
    env["client"].calls.clear()
    second = _publish(env, second_release)
    assert second.state == ReleaseState.PUBLISHED, second.message
    copies = {c[1]["Key"] for c in env["client"].calls if c[0] == "copy_object"}  # attempts
    puts = {c[1]["Key"] for c in env["client"].calls if c[0] in ("put_object", "complete_multipart_upload")}
    archive = f"maps/felho/releases/{second.release_id}/data/map.pmtiles"
    assert archive in puts
    unchanged = {f"maps/felho/releases/{second.release_id}/{f['path']}" for f in inventory["files"]
                 if (f["sha256"], f["size"]) in same} - {archive}
    changed = {f"maps/felho/releases/{second.release_id}/{f['path']}" for f in inventory["files"]} - unchanged
    assert len(unchanged) > len(changed) > 1           # e.g. manifest.json names its release
    assert unchanged <= copies and not (unchanged & puts)  # copied in the bucket, not uploaded
    assert changed <= puts and not ((changed - {archive}) & copies)
    assert archive in copies  # tried first, then uploaded because the old file was gone
    assert second.uploaded_bytes == sum(f["size"] for f in inventory["files"]
                                        if f"maps/felho/releases/{second.release_id}/{f['path']}" in changed)
    meta = env["client"].meta
    item = next(f for f in inventory["files"]
                if f"maps/felho/releases/{second.release_id}/{f['path']}" in unchanged)
    copied = meta[f"maps/felho/releases/{second.release_id}/{item['path']}"]
    assert copied["Metadata"]["sha256"] == item["sha256"] and copied["CacheControl"] == item["cacheControl"]
    assert second.checks.ok  # the copied release works at its public URL


def test_short_address_objects_on_object_storage():
    """R2/S3 have no directory index: /maps/arlo/ and /maps/arlo are objects of
    their own - the stable entry itself, and a redirect to it."""
    from publishing.models import DestinationConfig
    calls = []

    class Client:
        def put_object(self, **params):
            calls.append(params)
            return {"ETag": '"x"'}
    provider = R2Provider(DestinationConfig(kind="r2", account_id="a1", bucket="maps"),
                          Credentials("AKIAEXAMPLE", SECRET), "maps/arlo", client=Client(), sleep=lambda s: None)
    assert provider.put_entry_aliases(b"<!doctype html>entry", "no-cache")
    by_key = {c["Key"]: c for c in calls}
    assert set(by_key) == {"maps/arlo/", "maps/arlo"}
    assert by_key["maps/arlo/"]["Body"] == b"<!doctype html>entry"
    assert b'url=arlo/' in by_key["maps/arlo"]["Body"] and b"<script" not in by_key["maps/arlo"]["Body"]
    assert all(c["ContentType"].startswith("text/html") and c["CacheControl"] == "no-cache" for c in calls)


def test_short_address_is_optional(env):
    # This test bucket keeps objects as files: no key ending in "/" - the
    # publish still succeeds and the stable link stays .../index.html.
    result = _publish(env, _release(env))
    assert result.state == ReleaseState.PUBLISHED, result.message
    assert result.stable_url.endswith("/maps/felho/index.html")
