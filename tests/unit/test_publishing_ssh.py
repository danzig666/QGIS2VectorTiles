"""SSH / SFTP destination: one folder, not versioned. Batch-file quoting,
the askpass environment, the state-file plan (upload / keep / delete), the
batch order (state first, index.html renamed last, removed files deleted,
state last), error messages, profile validation; and end to end against a
real local OpenSSH server (skipped without sshd / the OpenSSH client):
publishing twice into a folder with a space in its name that holds an
unrelated file, keys with a passphrase through askpass, host key change,
cancel midway, public URL check."""

import hashlib
import json
import os
import subprocess

import pytest

import publishing_sshd
from publishing.errors import PublishingError
from publishing.folder_publish import (plan_sync, publish_folder, read_state, site_files, upload_batch)
from publishing.models import DestinationConfig, PublicationProfile, ReleaseState
from publishing.profile import (disclosure_fingerprint, dumps, load_profile, normalize_remote_dir,
                                validate)
from publishing.providers import provider_for
from publishing.providers.base import Credentials
from publishing.providers import ssh as ssh_module
from publishing.providers.ssh import (SECRET_ENV, STATE_NAME, SshProvider, askpass_env, ancestors,
                                      batch_line, parse_target, sftp_quote, tmp_name, write_askpass)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PUBLICATION = "6f1c2b8e-0d3a-4c55-9a77-1b2c3d4e5f60"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _entry(data: bytes) -> dict:
    return {"sha256": _sha(data), "size": len(data)}


# --- quoting and helpers ----------------------------------------------------------------

def test_sftp_quoting_keeps_every_character_literal():
    assert sftp_quote("plain.json") == '"plain.json"'
    assert sftp_quote("web maps/a b.json") == '"web maps/a b.json"'
    assert sftp_quote('say "hi".txt') == '"say \\"hi\\".txt"'
    assert sftp_quote("C:\\maps\\x") == '"C:\\\\maps\\\\x"'
    assert sftp_quote("térkép [1]*?.pbf") == '"térkép [1]*?.pbf"'  # sftp escapes globs in quotes itself
    for bad in ("two\nlines", "tab\there", "nul\x00", ""):
        with pytest.raises(PublishingError):
            sftp_quote(bad)
    assert batch_line("rm", "a b", ignore_errors=True) == '-rm "a b"'
    assert batch_line("chmod 644", "x") == 'chmod 644 "x"'
    with pytest.raises(PublishingError, match="too long"):
        batch_line("put", "x" * 500, "y" * 500)


def test_temporary_names_folders_and_remote_dirs():
    assert tmp_name("index.html") == ".index.html.q2vt-tmp"
    assert tmp_name("glyphs/Noto Sans/0-255.pbf") == "glyphs/Noto Sans/.0-255.pbf.q2vt-tmp"
    assert ancestors("/srv/www/my map") == ["/srv", "/srv/www", "/srv/www/my map"]
    assert ancestors("public_html/map") == ["public_html", "public_html/map"]
    assert ancestors(".") == []
    assert normalize_remote_dir(" ~/public_html/map/ ") == "public_html/map"
    assert normalize_remote_dir("~") == "." and normalize_remote_dir("/") == "/"
    assert normalize_remote_dir("/var/www/map//") == "/var/www/map"


@pytest.mark.parametrize("text, expected", [
    ("deploy@www.example.com:/var/www/map", {"host": "www.example.com", "user": "deploy",
                                             "remote_dir": "/var/www/map"}),
    ("www.example.com:public_html/map", {"host": "www.example.com", "remote_dir": "public_html/map"}),
    ("deploy@[2001:db8::5]:/srv/map", {"host": "2001:db8::5", "user": "deploy", "remote_dir": "/srv/map"}),
    ("sftp://deploy@www.example.com:2222/srv/my%20map", {"host": "www.example.com", "user": "deploy",
                                                         "port": 2222, "remote_dir": "/srv/my map"}),
    ("sftp://www.example.com/~/public_html", {"host": "www.example.com", "remote_dir": "public_html"}),
    ("www.example.com", {}),
    ("", {}),
])
def test_pasted_targets(text, expected):
    assert parse_target(text) == expected


def test_password_only_through_askpass(tmp_path, monkeypatch):
    secret = 'pa ss"word $HOME `id` é'
    helper = write_askpass(str(tmp_path))
    with open(helper, encoding="ascii") as handle:
        assert secret not in handle.read()
    env = askpass_env(secret, helper, {"PATH": "/usr/bin", SECRET_ENV: "stale"})
    assert env["SSH_ASKPASS"] == helper and env["SSH_ASKPASS_REQUIRE"] == "force"
    assert env[SECRET_ENV] == secret and [k for k, v in env.items() if v == secret] == [SECRET_ENV]
    assert SECRET_ENV not in askpass_env("", helper, {SECRET_ENV: "stale"})  # never inherited
    assert "SSH_ASKPASS" not in askpass_env("", helper, {})
    if os.name != "nt":  # the helper prints the value as is (no shell expansion)
        out = subprocess.run([helper], env=env, capture_output=True, check=True).stdout
        assert out.decode("utf-8") == secret + "\n"
    monkeypatch.setattr(ssh_module, "find_client", lambda name: f"/opt/ssh/{name}")
    destination = DestinationConfig(kind="ssh", host="www.example.com", user="deploy", remote_dir="/srv/map")
    with_password = SshProvider(destination, Credentials("deploy", secret)).command("batch.txt")
    assert secret not in " ".join(with_password)
    # BatchMode=no before -b (sftp adds BatchMode=yes for -b; ssh keeps the first value).
    assert with_password.index("BatchMode=no") < with_password.index("-b")
    assert with_password[-2:] == ["--", "www.example.com"] and "User=deploy" in with_password
    assert "StrictHostKeyChecking=accept-new" in with_password
    keys_only = SshProvider(destination).command("batch.txt")
    assert "BatchMode=yes" in keys_only and "BatchMode=no" not in keys_only
    ipv6 = SshProvider(DestinationConfig(kind="ssh", host="2001:db8::5", remote_dir="m")).command("b")
    assert ipv6[-1] == "[2001:db8::5]"


def test_missing_client_is_explained(monkeypatch):
    monkeypatch.setattr(ssh_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(ssh_module.os, "name", "posix")
    provider = SshProvider(DestinationConfig(kind="ssh", host="www.example.com", remote_dir="map"))
    with pytest.raises(PublishingError) as error:
        provider.command("batch.txt")
    assert error.value.code == "Q2VT_PUB_DEPENDENCY" and "OpenSSH Client" in error.value.message
    [(name, ok, message)] = provider.inspect()
    assert name == "client" and not ok and "OpenSSH" in message


# --- plan and batch ---------------------------------------------------------------------

def _files(**contents):
    return {name.replace("__", "/").replace("_", "."): _entry(data) for name, data in contents.items()}


def test_plan_uploads_new_and_changed_files_and_deletes_only_listed_ones():
    previous = {
        "index.html": _entry(b"old page"), "style.json": _entry(b"{}"), "old.json": _entry(b"gone"),
        "data/map.pmtiles": {"sha256": "", "size": 5},     # interrupted earlier: unknown content
        "lost.json": {"sha256": "", "size": 1},            # interrupted, no longer in the site
    }
    files = {"index.html": _entry(b"new page"), "style.json": _entry(b"{}"),
             "data/map.pmtiles": _entry(b"tiles"), "assets/new.mjs": _entry(b"x")}
    plan = plan_sync(previous, files, PUBLICATION, "r-1")
    assert plan.upload == ["assets/new.mjs", "data/map.pmtiles", "index.html"]  # entry last
    assert plan.unchanged == ["style.json"]
    assert plan.delete == ["lost.json", "old.json"] and plan.leftovers == ["lost.json"]
    pending = plan.pending["files"]
    assert pending["style.json"] == previous["style.json"] and pending["old.json"] == previous["old.json"]
    assert all(pending[p]["sha256"] == "" for p in plan.upload)  # unknown until renamed
    assert plan.pending["complete"] is False and plan.final["complete"] is True
    assert plan.final["files"] == files and plan.final["publicationId"] == PUBLICATION
    again = plan_sync(plan.final["files"], files)
    assert again.upload == [] and again.delete == [] and len(again.unchanged) == 4


def test_state_file_is_read_defensively():
    state = {"format": "qwebmap-folder-state", "publicationId": PUBLICATION, "files": {
        "ok.json": _entry(b"a"), "../escape.txt": _entry(b"b"), "/etc/passwd": _entry(b"c"),
        "a\\b": _entry(b"d"), STATE_NAME: _entry(b"e"), ".x.q2vt-tmp": _entry(b"f"), "bad": "x"}}
    data = json.dumps(state).encode("utf-8")
    assert read_state(data, PUBLICATION) == {"ok.json": _entry(b"a")}
    assert read_state(b"not json", PUBLICATION) == {} and read_state(None, PUBLICATION) == {}
    assert read_state(b'{"files": {"x": {}}}', PUBLICATION) == {}  # not our format: nothing is deleted
    with pytest.raises(PublishingError, match="another web map"):
        read_state(data, "11111111-2222-4333-8444-555555555555")


def test_upload_batch_order(tmp_path):
    previous = {"index.html": _entry(b"old"), "gone/only.json": _entry(b"x"), "keep.json": _entry(b"k")}
    files = {"index.html": _entry(b"new page"), "keep.json": _entry(b"k"),
             "glyphs/Noto Sans/0-255.pbf": _entry(b"glyph data"), "data/map.pmtiles": _entry(b"t" * 50)}
    plan = plan_sync(previous, files, PUBLICATION, "r-2")
    lines = [line for line, _, _ in upload_batch(plan, "/srv/web maps", str(tmp_path / "rel"),
                                                 str(tmp_path / "state"), files)]
    text = "\n".join(lines)
    assert lines[0] == 'cd "/srv/web maps"'
    assert lines[2:4] == ['put "pending.json" ".q2vt-files.json.q2vt-tmp"',
                          'rename ".q2vt-files.json.q2vt-tmp" ".q2vt-files.json"']
    assert '-mkdir "glyphs"' in lines and '-mkdir "glyphs/Noto Sans"' in lines
    puts = [line for line in lines if line.startswith('put "') and "json\" \".q2vt-files" not in line]
    assert puts[0] == 'put "index.html" ".index.html.q2vt-tmp"'  # smallest first
    assert puts[-1] == 'put "data/map.pmtiles" "data/.map.pmtiles.q2vt-tmp"'
    renames = [i for i, line in enumerate(lines) if line.startswith("rename")]
    assert lines[renames[-2]] == 'rename ".index.html.q2vt-tmp" "index.html"'  # entry last of the site
    assert renames[1] > lines.index(puts[-1])  # every upload done before the first site file changes
    assert '-chmod 644 "data/.map.pmtiles.q2vt-tmp"' in lines
    assert lines.index('-rm "gone/only.json"') > renames[-2]
    assert '-rmdir "gone"' in lines and "keep.json" not in text  # unchanged: not touched
    assert lines[-3:] == [f'lcd "{(tmp_path / "state").as_posix()}"',
                          'put "final.json" ".q2vt-files.json.q2vt-tmp"',
                          'rename ".q2vt-files.json.q2vt-tmp" ".q2vt-files.json"']
    legacy = [line for line, _, _ in upload_batch(plan, "/srv/web maps", str(tmp_path / "rel"),
                                                  str(tmp_path / "state"), files, atomic=False)]
    at = legacy.index('rename ".index.html.q2vt-tmp" "index.html"')
    assert legacy[at - 1] == '-rm "index.html"'  # no posix-rename: delete, then rename


def test_error_messages():
    provider = SshProvider(DestinationConfig(kind="ssh", host="www.example.com", user="deploy",
                                             remote_dir="/srv/map"), Credentials("deploy", "s3cret"))
    cases = [
        ("@@@\nWARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!\nHost key verification failed.", 255, "",
         "Q2VT_PUB_DESTINATION", "ssh-keygen -R www.example.com"),
        ("deploy@www.example.com: Permission denied (publickey,password).", 255, "",
         "Q2VT_PUB_CREDENTIALS", "refused the login"),
        ("ssh: Could not resolve hostname www.example.com: Name or service not known", 255, "",
         "Q2VT_PUB_DESTINATION", "not known"),
        ("ssh: connect to host www.example.com port 22: Connection refused", 255, "",
         "Q2VT_PUB_DESTINATION", "cannot be reached"),
        ('remote mkdir "/srv/map": Permission denied\nrealpath /srv/map: No such file', 1, 'cd "/srv/map"',
         "Q2VT_PUB_DESTINATION", "could not be created (permission denied)"),
        ('dest open "/srv/map/.index.html.q2vt-tmp": Permission denied', 1, 'put "index.html" "x"',
         "Q2VT_PUB_DESTINATION", "cannot write into /srv/map"),
        ("Connection closed s3cret", 255, "", "Q2VT_PUB_UPLOAD", "Publish again"),
    ]
    for stderr, code, failed, expected, text in cases:
        error = provider.classify(provider._redact(stderr), code, failed)  # pylint: disable=protected-access
        assert error.code == expected and text in error.message, (stderr, error.message)
        assert "s3cret" not in error.message + error.detail


# --- profile ------------------------------------------------------------------------------

def _ssh_profile(**settings):
    profile = PublicationProfile(title="Town map", slug="town-map")
    values = dict(kind="ssh", host="www.example.com", port=2222, user="deploy", remote_dir="/srv/web maps",
                  identity_file="/home/me/.ssh/id_ed25519", public_base_url="https://www.example.com/map")
    values.update(settings)
    for key, value in values.items():
        setattr(profile.destination, key, value)
    return profile


def test_profile_round_trip_validation_and_schema():
    profile = _ssh_profile()
    text = dumps(profile)
    again = load_profile(text)  # identityFile is a path, not a secret: accepted
    assert dumps(again) == text and again.destination.remote_dir == "/srv/web maps"
    assert json.loads(text)["destination"]["identityFile"] == "/home/me/.ssh/id_ed25519"
    with pytest.raises(PublishingError, match="private key file"):
        provider_for(again.destination, None, "")  # a missing key file is named at once
    again.destination.identity_file = ""
    assert provider_for(again.destination, None, "").describe() == \
        "deploy@www.example.com:/srv/web maps (port 2222)"
    jsonschema = pytest.importorskip("jsonschema")
    with open(os.path.join(ROOT, "schemas", "publishing", "profile-v1.schema.json"), encoding="utf-8") as handle:
        jsonschema.validate(profile.to_dict(), json.load(handle))
    assert validate(_ssh_profile(public_base_url="", user="")) == []  # both optional
    for settings, message in [({"host": ""}, "destination.host"),
                              ({"host": "-oProxyCommand=x"}, "destination.host"),
                              ({"host": "a b"}, "destination.host"), ({"port": 0}, "destination.port"),
                              ({"user": "-l"}, "destination.user"), ({"remote_dir": ""}, "destination.remoteDir"),
                              ({"remote_dir": "/"}, "destination.remoteDir"),
                              ({"remote_dir": "a\nb"}, "destination.remoteDir"),
                              ({"public_base_url": "ftp://x"}, "destination.publicBaseUrl")]:
        problems = validate(_ssh_profile(**settings))
        assert any(message in p for p in problems), (settings, problems)
    profile = _ssh_profile()
    profile.output.archive = "mbtiles"
    assert any("PMTiles" in p for p in validate(profile))


def test_the_folder_is_part_of_the_review():
    profile = _ssh_profile()
    profile.approval.fingerprint = disclosure_fingerprint(profile)
    profile.destination.remote_dir = "/srv/other"
    assert disclosure_fingerprint(profile) != profile.approval.fingerprint
    local = PublicationProfile()  # other destinations: unchanged fingerprint input
    before = disclosure_fingerprint(local)
    local.destination.host = "ignored.example.com"
    assert disclosure_fingerprint(local) == before


# --- end to end against a local OpenSSH server ----------------------------------------------

needs_sshd = pytest.mark.skipif(not publishing_sshd.available(),
                                reason="needs /usr/sbin/sshd and the OpenSSH client (ssh, sftp)")


@pytest.fixture
def server(tmp_path):
    try:
        running = publishing_sshd.SshServer(str(tmp_path / "sshd")).start()
    except RuntimeError as error:  # e.g. no rights for sshd's privilege separation folder
        pytest.skip(str(error))
    yield running
    running.stop()


class _Release:
    """A small synthetic site in the release layout (release.json inventory)."""

    def __init__(self, folder, files, release_id="r-20260101T000000Z-0000000a"):
        self.release_id, self.release_dir = release_id, str(folder)
        items = []
        for rel, data in files.items():
            path = os.path.join(self.release_dir, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as handle:
                handle.write(data)
            items.append({"path": rel, "size": len(data), "sha256": _sha(data)})
        with open(os.path.join(self.release_dir, "release.json"), "w", encoding="utf-8") as handle:
            json.dump({"releaseId": release_id, "files": items}, handle)


def _remote_files(folder):
    found = {}
    for base, _, names in os.walk(folder):
        for name in names:
            path = os.path.join(base, name)
            found[os.path.relpath(path, folder).replace(os.sep, "/")] = path
    return found


SITE_1 = {"index.html": b"<html>first</html>", "style.json": b'{"version": 8}',
          "data/map.pmtiles": b"P" * 4000, "glyphs/Noto Sans Regular/0-255.pbf": b"G" * 300,
          "assets/légende [1].json": b'{"a": 1}', "old/only here.json": b"bye"}


@needs_sshd
def test_publish_twice_into_a_folder_with_other_files(server, tmp_path):
    remote = tmp_path / "server" / "web root" / "town maps"
    remote.mkdir(parents=True)
    (remote / "notes of the webmaster.txt").write_text("not ours")
    profile = PublicationProfile(title="Town map", slug="town-map", publication_id=PUBLICATION)
    profile.destination = server.destination(str(remote))
    provider = server.provider(profile.destination)
    messages = []
    first = publish_folder(_Release(tmp_path / "rel-1", SITE_1), profile, provider, str(tmp_path / "work"),
                           lambda p, m: messages.append(m), verify=False)
    assert first.state == ReleaseState.PUBLISHED, first.message
    on_server = _remote_files(remote)
    assert set(on_server) == set(SITE_1) | {"release.json", STATE_NAME, "notes of the webmaster.txt"}
    for rel, data in SITE_1.items():
        with open(on_server[rel], "rb") as handle:
            assert handle.read() == data
    assert any("7 new or changed files" in m for m in messages if m)
    before = {rel: os.stat(path) for rel, path in on_server.items()}

    site_2 = dict(SITE_1, **{"index.html": b"<html>second</html>"})
    del site_2["old/only here.json"]
    messages.clear()
    second = publish_folder(_Release(tmp_path / "rel-2", site_2, "r-20260102T000000Z-0000000b"), profile,
                            provider, str(tmp_path / "work"), lambda p, m: messages.append(m), verify=False)
    assert second.state == ReleaseState.PUBLISHED, second.message
    on_server = _remote_files(remote)
    assert set(on_server) == set(site_2) | {"release.json", STATE_NAME, "notes of the webmaster.txt"}
    assert not (remote / "old").exists()  # its only file was ours
    assert (remote / "notes of the webmaster.txt").read_text() == "not ours"
    assert (remote / "index.html").read_bytes() == b"<html>second</html>"
    assert any("2 new or changed files" in m and "4 unchanged, 1 to delete" in m for m in messages if m)
    for rel in ("style.json", "data/map.pmtiles", "glyphs/Noto Sans Regular/0-255.pbf",
                "assets/légende [1].json"):
        now = os.stat(on_server[rel])  # not uploaded again: same file, same time
        assert (now.st_ino, now.st_mtime_ns) == (before[rel].st_ino, before[rel].st_mtime_ns), rel
    assert os.stat(on_server["index.html"]).st_ino != before["index.html"].st_ino
    assert not [p for p in on_server if "q2vt-tmp" in p or "q2vt-probe" in p]
    state = json.loads((remote / STATE_NAME).read_text(encoding="utf-8"))
    assert state["complete"] and state["releaseId"] == "r-20260102T000000Z-0000000b"
    assert set(state["files"]) == set(site_2) | {"release.json"}
    assert oct(os.stat(on_server["index.html"]).st_mode & 0o777) == "0o644"

    other = PublicationProfile(title="Another map", slug="another")
    other.destination = profile.destination
    refused = publish_folder(_Release(tmp_path / "rel-3", {"index.html": b"x"}), other, provider,
                             str(tmp_path / "work"), verify=False)
    assert refused.state == ReleaseState.FAILED and "another web map" in refused.message
    assert (remote / "index.html").read_bytes() == b"<html>second</html>"


@needs_sshd
def test_one_session_reports_progress_by_bytes(server, tmp_path):
    from publishing.progress import Progress
    from publishing.providers.ssh import local_path
    local, remote = tmp_path / "local", tmp_path / "progress"
    local.mkdir()
    (local / "a.bin").write_bytes(b"a" * 100)
    (local / "b.bin").write_bytes(b"b" * 300)
    provider = server.provider(server.destination(str(remote)))
    lines = [batch_line("mkdir", str(remote), ignore_errors=True), batch_line("cd", str(remote)),
             batch_line("lcd", local_path(str(local))), batch_line("put", "a.bin", "a.bin"),
             batch_line("put", "b.bin", "b.bin"), "pwd"]
    seen = []
    provider.run(lines, Progress(lambda p, m: seen.append((p, m))), [0, 0, 0, 100, 300, 0],
                 {3: "a", 4: "b", 5: "done"})
    assert [(p, m) for p, m in seen if m] == [(0.0, "a"), (25.0, "b"), (100.0, "done")]
    assert sorted(os.listdir(remote)) == ["a.bin", "b.bin"]


@needs_sshd
def test_test_connection_keys_and_passphrase_through_askpass(server, tmp_path, monkeypatch):
    remote = tmp_path / "server" / "new folder" / "map"
    destination = server.destination(str(remote))
    checks = server.provider(destination).inspect()
    assert [ok for _, ok, _ in checks] == [True, True, True], checks
    assert remote.is_dir() and os.listdir(remote) == []  # created; the test file is gone
    locked = server.destination(str(remote), identity_file=server.locked_key)
    [(name, ok, message)] = server.provider(locked).inspect()
    assert name == "login" and not ok and "refused the login" in message
    calls = []
    real_popen = subprocess.Popen

    def recording(args, **kwargs):
        calls.append((list(args), dict(kwargs.get("env") or {})))
        return real_popen(args, **kwargs)
    monkeypatch.setattr(ssh_module.subprocess, "Popen", recording)
    passphrase = publishing_sshd.PASSPHRASE
    checks = server.provider(locked, Credentials(server.user, passphrase)).inspect()
    assert [ok for _, ok, _ in checks] == [True, True, True], checks
    args, env = calls[-1]
    assert passphrase not in " ".join(args) and env[SECRET_ENV] == passphrase
    helper = env["SSH_ASKPASS"]
    assert not os.path.exists(helper) and not os.path.exists(os.path.dirname(helper))  # removed
    [(name, ok, message)] = server.provider(locked, Credentials(server.user, "wrong")).inspect()
    assert name == "login" and not ok


@needs_sshd
def test_changed_host_key_and_unreachable_server(server, tmp_path):
    destination = server.destination(str(tmp_path / "server" / "map"))
    assert all(ok for _, ok, _ in server.provider(destination).inspect())
    with open(server.known_hosts, encoding="utf-8") as handle:
        entry = handle.read().split()
    other = os.path.join(server.folder, "other_host_key")
    publishing_sshd.keygen(other)
    with open(other + ".pub", encoding="utf-8") as handle:
        kind, key = handle.read().split()[:2]
    with open(server.known_hosts, "w", encoding="utf-8") as handle:
        handle.write(f"{entry[0]} {kind} {key}\n")
    [(name, ok, message)] = server.provider(destination).inspect()
    assert not ok and "host key of 127.0.0.1 has changed" in message and "ssh-keygen -R" in message
    closed = server.destination(str(tmp_path / "server" / "map"), port=publishing_sshd.free_port())
    [(name, ok, message)] = server.provider(closed).inspect()
    assert not ok and "cannot be reached" in message


@needs_sshd
def test_cancel_midway_keeps_the_previous_site_and_the_next_publish_completes_it(server, tmp_path):
    remote = tmp_path / "server" / "map"
    profile = PublicationProfile(title="Town map", slug="town-map", publication_id=PUBLICATION)
    profile.destination = server.destination(str(remote))
    provider = server.provider(profile.destination)
    work = str(tmp_path / "work")
    assert publish_folder(_Release(tmp_path / "rel-1", SITE_1), profile, provider, work,
                          verify=False).state == ReleaseState.PUBLISHED
    site_2 = dict(SITE_1, **{"index.html": b"<html>second</html>", "data/map.pmtiles": os.urandom(48 << 20)})
    release_2 = _Release(tmp_path / "rel-2", site_2, "r-20260102T000000Z-0000000b")
    seen = []

    def feedback(percent, message):  # cancel once the big archive's upload has started
        seen.append(message)
    from publishing.progress import Progress
    progress = Progress(feedback, cancel=lambda: any(m and m.startswith("Uploading data/map.pmtiles")
                                                     for m in seen))
    cancelled = publish_folder(release_2, profile, provider, work, progress, verify=False)
    assert cancelled.state == ReleaseState.CANCELLED and "publish again" in cancelled.message
    assert (remote / "index.html").read_bytes() == b"<html>first</html>"  # the previous site works
    assert (remote / "data" / "map.pmtiles").read_bytes() == SITE_1["data/map.pmtiles"]
    state = json.loads((remote / STATE_NAME).read_text(encoding="utf-8"))
    assert state["complete"] is False and state["files"]["index.html"]["sha256"] == ""
    done = publish_folder(release_2, profile, provider, work, verify=False)
    assert done.state == ReleaseState.PUBLISHED, done.message
    assert (remote / "index.html").read_bytes() == b"<html>second</html>"
    assert _sha((remote / "data" / "map.pmtiles").read_bytes()) == _sha(site_2["data/map.pmtiles"])
    assert not [p for p in _remote_files(remote) if "q2vt-tmp" in p]
    assert json.loads((remote / STATE_NAME).read_text(encoding="utf-8"))["complete"] is True


@needs_sshd
def test_real_release_is_checked_through_its_public_url(server, tmp_path):
    from publishing.preview_server import PreviewServer
    from publishing.web_builder import build_release
    from publishing_fixtures import fixture_bundle
    profile = PublicationProfile(title="Town map", slug="town-map")
    release = build_release(fixture_bundle(str(tmp_path / "export")), profile,
                            str(tmp_path / "local" / "town-map"))
    root = tmp_path / "server" / "www"
    remote = root / "town map"
    public = PreviewServer(str(root)).start()  # the web server of the SSH host
    try:
        profile.destination = server.destination(str(remote), public_base_url=public.url("town map"))
        result = publish_folder(release, profile, server.provider(profile.destination), str(tmp_path / "work"))
        assert result.state == ReleaseState.PUBLISHED and not result.warnings, (result.message, result.warnings)
        assert result.stable_url == public.url("town map") + "/"
        assert result.checks.checks and result.checks.ok  # index.html, modules, ranges of the archive
        assert set(_remote_files(remote)) == set(site_files(release.release_dir)) | {STATE_NAME}
        assert not (remote / "releases").exists() and not (remote / "current.json").exists()
    finally:
        public.stop()
