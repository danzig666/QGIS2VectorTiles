"""SSH / SFTP destination: one folder, not versioned. Batch-file quoting,
the askpass environment, the state-file plan (upload / keep / delete,
repair, files replaced, a taken-over folder), the batch order (state first,
index.html renamed last, removed files deleted, state last), the folder
listing, failed deletions, error messages, profile validation; and end to
end against a real local OpenSSH server (skipped without sshd / the OpenSSH
client): publishing into a folder with a space in its name that holds
unrelated files, under a restrictive umask, keys with a passphrase through
askpass, host key change, cancel midway, public URL check."""

import hashlib
import json
import os
import subprocess

import pytest

import publishing_sshd
from publishing.errors import PublishingError
from publishing.folder_publish import (failed_deletes, plan_sync, publish_folder, read_state, site_files,
                                       upload_batch)
from publishing.models import DestinationConfig, PublicationProfile, ReleaseState
from publishing.profile import (disclosure_fingerprint, dumps, load_profile, normalize_remote_dir,
                                validate)
from publishing.providers import provider_for
from publishing.providers.base import Credentials
from publishing.providers import ssh as ssh_module
from publishing.providers.ssh import (SECRET_ENV, STATE_NAME, SshProvider, askpass_env, ancestors,
                                      batch_line, parse_listing, parse_target, sftp_quote, tmp_name,
                                      write_askpass)

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
    # sftp reads a leading "-" as a flag, even in quotes ("cd: Invalid flag -m")
    assert batch_line("cd", "-maps") == 'cd "./-maps"' and batch_line("put", "-a", "/x/-b") == 'put "./-a" "/x/-b"'
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
    assert normalize_remote_dir("/.") == "/" and normalize_remote_dir("/srv/x/../map") == "/srv/map"


@pytest.mark.parametrize("text, expected", [
    ("deploy@www.example.com:/var/www/map", {"host": "www.example.com", "user": "deploy",
                                             "remote_dir": "/var/www/map"}),
    ("deploy@[2001:db8::5]:/srv/map", {"host": "2001:db8::5", "user": "deploy", "remote_dir": "/srv/map"}),
    ("sftp://deploy@www.example.com:2222/srv/my%20map", {"host": "www.example.com", "user": "deploy",
                                                         "port": 2222, "remote_dir": "/srv/my map"}),
    ("sftp://www.example.com/~/public_html", {"host": "www.example.com", "remote_dir": "public_html"}),
    ("www.example.com:/srv/map", {"host": "www.example.com", "remote_dir": "/srv/map"}),
    ("deploy@www.example.com:public_html/map", {"host": "www.example.com", "user": "deploy",
                                                "remote_dir": "public_html/map"}),
    ("deploy@www.example.com", {"host": "www.example.com", "user": "deploy"}),
    ("www.example.com:2222", {"host": "www.example.com", "port": 2222}),  # a port, never a folder
    ("[2001:db8::5]:2222", {"host": "2001:db8::5", "port": 2222}),
    ("www.example.com:public_html/map", {}),  # not explicit enough: stays as typed
    ("2001:db8::10", {}), ("fe80::1%eth0", {}), ("192.0.2.7", {}),  # addresses stay as typed
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
    with monkeypatch.context() as windows:  # the .cmd helper prints UTF-8, not the console code page
        windows.setattr(ssh_module.os, "name", "nt")
        os.makedirs(tmp_path / "win")
        with open(write_askpass(str(tmp_path / "win")), encoding="ascii") as handle:
            text = handle.read()
    assert text.index("chcp 65001 >nul") < text.index(f"echo(!{SECRET_ENV}!")
    monkeypatch.setattr(ssh_module, "find_client", lambda name: f"/opt/ssh/{name}")
    destination = DestinationConfig(kind="ssh", host="www.example.com", user="deploy", remote_dir="/srv/map")
    with_password = SshProvider(destination, Credentials("deploy", secret)).command("batch.txt")
    assert secret not in " ".join(with_password)
    # BatchMode=no before -b (sftp adds BatchMode=yes for -b; ssh keeps the first value).
    assert with_password.index("BatchMode=no") < with_password.index("-b")
    assert with_password[-2:] == ["--", "www.example.com"] and "User=deploy" in with_password
    assert "StrictHostKeyChecking=accept-new" in with_password
    assert "ControlMaster=no" in with_password and "ControlPath=none" in with_password  # no master keeps it
    keys_only = SshProvider(destination).command("batch.txt")
    assert "BatchMode=yes" in keys_only and "BatchMode=no" not in keys_only
    key = tmp_path / "keys 100%" / 'my "id"'
    key.parent.mkdir()
    key.write_text("key")
    keyed = SshProvider(DestinationConfig(kind="ssh", host="www.example.com", remote_dir="m",
                                          identity_file=str(key))).command("b")
    # ssh expands %d, %h… in key paths: "%" doubled, and -o (sftp -i checks the unexpanded path)
    assert f'IdentityFile="{tmp_path}/keys 100%%/my \\"id\\""' in keyed and "-i" not in keyed
    ipv6 = SshProvider(DestinationConfig(kind="ssh", host="2001:db8::5", remote_dir="m")).command("b")
    assert ipv6[-1] == "[2001:db8::5]"


def test_password_needs_openssh_8_4_on_windows(monkeypatch):
    monkeypatch.setattr(ssh_module, "find_client", lambda name: f"/opt/ssh/{name}")
    monkeypatch.setattr(ssh_module.os, "name", "nt")
    destination = DestinationConfig(kind="ssh", host="www.example.com", remote_dir="map")
    monkeypatch.setattr(ssh_module, "client_version", lambda ssh: (8, 1))  # Windows 10's built-in client
    with pytest.raises(PublishingError) as error:
        SshProvider(destination, Credentials("deploy", "pw")).command("b")
    assert error.value.code == "Q2VT_PUB_DEPENDENCY" and "8.4" in error.value.message
    assert "ssh-agent" in error.value.message
    assert SshProvider(destination).command("b")  # keys need no askpass: fine
    monkeypatch.setattr(ssh_module, "client_version", lambda ssh: (9, 5))
    assert SshProvider(destination, Credentials("deploy", "pw")).command("b")


def test_missing_client_is_explained(monkeypatch):
    monkeypatch.setattr(ssh_module.shutil, "which", lambda name: None)
    monkeypatch.setattr(ssh_module.os, "name", "posix")
    provider = SshProvider(DestinationConfig(kind="ssh", host="www.example.com", remote_dir="map"))
    with pytest.raises(PublishingError) as error:
        provider.command("batch.txt")
    assert error.value.code == "Q2VT_PUB_DEPENDENCY" and "OpenSSH Client" in error.value.message
    assert "Apps → Optional features (Windows 10)" in error.value.message
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


def test_plan_with_the_folder_listing():
    """The listing re-uploads listed files that are missing or have another
    size there, counts the existing files QWebMap had not uploaded (replaced,
    as the user wants), and names the folders to create (mode 755)."""
    files = {"index.html": _entry(b"page"), "style.json": _entry(b"{}"), "data/map.pmtiles": _entry(b"tiles"),
             "glyphs/Sans/0-255.pbf": _entry(b"g"), "assets/app.mjs": _entry(b"js")}
    previous = {"style.json": _entry(b"{}"), "data/map.pmtiles": _entry(b"tiles"),
                "assets/app.mjs": _entry(b"js"), "old.json": _entry(b"o")}
    remote = {"index.html": ("-", 11), "style.json": ("-", 2), "data": ("d", 4096), "assets": ("d", 4096),
              "assets/app.mjs": ("-", 999), "old.json": ("-", 1), "notes.txt": ("-", 5)}
    plan = plan_sync(previous, files, PUBLICATION, "r-1", remote)
    assert plan.repaired == ["assets/app.mjs", "data/map.pmtiles"]  # another size; deleted on the server
    assert plan.unchanged == ["style.json"]
    assert plan.replaced == ["index.html"]  # the user's own index.html: replaced, and counted
    assert plan.folders == plan.new_folders == ["glyphs", "glyphs/Sans"]  # data/, assets/ exist
    assert plan.delete == ["old.json"]  # notes.txt was never QWebMap's
    pending = plan.pending["files"]
    assert pending["index.html"] == {"sha256": "", "size": 4, "foreign": True}
    assert "foreign" not in pending["glyphs/Sans/0-255.pbf"]  # not there before: QWebMap's at once
    assert "foreign" not in pending["assets/app.mjs"] and all("foreign" not in e for e in plan.final["files"].values())
    unknown = plan_sync(previous, files, PUBLICATION, "r-1", None)  # listing failed: trust the state
    assert unknown.repaired == [] and unknown.replaced == [] and unknown.new_folders == []
    assert unknown.folders == ["glyphs", "glyphs/Sans"]  # mkdir (errors ignored), no chmod
    assert unknown.pending["files"]["index.html"]["foreign"] is True  # may be the user's


def test_interrupted_replacement_of_a_foreign_file_never_deletes_it():
    """A publish planned to replace the user's report.pdf and was interrupted
    before the rename: the next site lacks report.pdf, so only QWebMap's
    temporary file goes, never report.pdf itself."""
    previous, _ = read_state(json.dumps({"format": "qwebmap-folder-state", "files": {
        "index.html": _entry(b"page"), "report.pdf": {"sha256": "", "size": 3, "foreign": True}}}).encode())
    assert previous["report.pdf"]["foreign"] is True
    plan = plan_sync(previous, {"index.html": _entry(b"page")}, PUBLICATION, "r-2",
                     {"index.html": ("-", 4), "report.pdf": ("-", 3)})
    assert plan.delete == [] and plan.leftovers == ["report.pdf"]
    lines = [line for line, _, _ in upload_batch(plan, "m", "/rel", "/st", {"index.html": _entry(b"page")})]
    assert '-rm ".report.pdf.q2vt-tmp"' in lines and '-rm "report.pdf"' not in lines


def test_state_file_is_read_defensively_and_another_map_is_taken_over():
    state = {"format": "qwebmap-folder-state", "publicationId": PUBLICATION, "files": {
        "ok.json": _entry(b"a"), "../escape.txt": _entry(b"b"), "/etc/passwd": _entry(b"c"),
        "a\\b": _entry(b"d"), STATE_NAME: _entry(b"e"), ".x.q2vt-tmp": _entry(b"f"), "bad": "x"}}
    data = json.dumps(state).encode("utf-8")
    assert read_state(data) == ({"ok.json": _entry(b"a")}, PUBLICATION)  # another map's: taken over
    assert read_state(b"not json") == ({}, "") and read_state(None) == ({}, "")
    assert read_state(b'{"files": {"x": {}}}') == ({}, "")  # not our format: nothing is deleted


def test_parse_listing_of_ls_lan():
    stdout = "\n".join([
        'sftp> cd "/srv/web maps"', "sftp> pwd", "Remote working directory: /srv/web maps",
        "sftp> -ls -lan",
        "drwxr-xr-x    ? 1000     1000         4096 Oct  9 23:20 .",
        "drwxr-xr-x    ? 1000     1000         4096 Oct  9 23:20 ..",
        "-rw-r--r--    1 1000     1000           10 Oct  9 23:20 a b.txt",
        "-rw-r--r--    1 33       33              1 Jan  1  2024 l\\303\\251gende [1].json",
        "drwxr-xr-x    ? 1000     1000         4096 Oct  9 23:20 -mdir",
        "lrwxrwxrwx    1 0        0              12 Oct  9 23:20 data",
        'sftp> -ls -lan "./-mdir"',
        "-rw-r--r--    ? 1000     1000          123 Oct  9 23:20 ./-mdir/x 12 Oct  9 1.json",
        "drwxr-xr-x    ? 1000     1000         4096 Oct  9 23:20 ./-mdir/.",
        'sftp> put "probe.txt" ".q2vt-probe-1"',
        "-rw-r--r--    1 1000     1000            5 Oct  9 23:20 not-a-listing"])
    assert parse_listing(stdout) == {"a b.txt": ("-", 10), "légende [1].json": ("-", 1), "-mdir": ("d", 4096),
                                     "data": ("l", 12), "-mdir/x 12 Oct  9 1.json": ("-", 123)}
    assert parse_listing("sftp> -ls -lan\n") is None  # the folder itself was not listed: unknown


def test_failed_deletions_are_named():
    stderr = "\n".join([
        "remote delete /srv/web maps/gone.json: No such file or directory",   # already gone: deleted
        "remote delete /srv/web maps/old/only here.json: Permission denied",
        "remote delete /srv/web maps/./-x.json: Failure"])
    failed, unattributed = failed_deletes(stderr, "/srv/web maps", ["gone.json", "old/only here.json", "-x.json",
                                                                    "fine.json"])
    assert failed == {"old/only here.json": "Permission denied", "-x.json": "Failure"} and unattributed == 0
    assert failed_deletes("Couldn't delete file: Permission denied\nCouldn't delete file: No such file", "",
                          ["a"]) == ({}, 1)  # older clients do not name the file


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
    assert lines.index('-mkdir "glyphs"') < lines.index('-mkdir "glyphs/Noto Sans"')
    assert '-chmod 755 "glyphs/Noto Sans"' not in lines  # unknown listing: maybe not created by QWebMap
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
    listed = plan_sync(previous, files, PUBLICATION, "r-2", {"glyphs": ("d", 4096), "index.html": ("-", 3),
                                                             "keep.json": ("-", 1)})
    lines = [line for line, _, _ in upload_batch(listed, "/srv/web maps", str(tmp_path / "rel"),
                                                 str(tmp_path / "state"), files)]
    # Only the folders QWebMap creates become 755 (a web server reads them whatever the umask).
    assert '-mkdir "glyphs"' not in lines and "-chmod 755 \"glyphs\"" not in lines
    for folder in ("glyphs/Noto Sans", "data"):
        assert lines.index(f'-mkdir "{folder}"') + 1 == lines.index(f'-chmod 755 "{folder}"')


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
        ("Connection closed s3cret", 255, "", "Q2VT_PUB_UPLOAD", "failed or was lost"),
        ("subsystem request failed on channel 0\nConnection closed", 255, "", "Q2VT_PUB_DESTINATION",
         "offers no SFTP"),
        ('Load key "/home/me/key.ppk": error in libcrypto\ndeploy@www.example.com: Permission denied '
         "(publickey).", 255, "", "Q2VT_PUB_CREDENTIALS", "PuTTYgen"),
        ("@@@\n@ WARNING: UNPROTECTED PRIVATE KEY FILE! @\nLoad key \"/k\": bad permissions\n"
         "deploy@www.example.com: Permission denied (publickey).", 255, "", "Q2VT_PUB_CREDENTIALS", "chmod 600"),
        ("Bad permissions. Try removing permissions for user: NT AUTHORITY\\Authenticated Users on file "
         "C:/k.\nLoad key \"C:/k\": bad permissions\nPermission denied (publickey).", 255, "",
         "Q2VT_PUB_CREDENTIALS", "Properties → Security"),
        ("ssh_askpass: exec(/tmp/q/q2vt-askpass.sh): Permission denied\ndeploy@www.example.com: Permission "
         "denied (publickey,password).", 255, "", "Q2VT_PUB_CREDENTIALS", "password helper"),
    ]
    for stderr, code, failed, expected, text in cases:
        error = provider.classify(provider._redact(stderr), code, failed)  # pylint: disable=protected-access
        assert error.code == expected and text in error.message, (stderr, error.message)
        assert "s3cret" not in error.message + error.detail
    refused = provider.classify("Warning: Permanently added 'x' to the list of known hosts.\nsign_and_send_pubkey: "
                                "signing failed\ndeploy@www.example.com: Permission denied (publickey).\n"
                                "Connection closed", 255)
    assert refused.where == "login" and refused.said == [  # Test connection shows ssh's own words
        "sign_and_send_pubkey: signing failed", "deploy@www.example.com: Permission denied (publickey)."]
    closed = provider.classify("ssh: connect to host www.example.com port 22: Connection refused\n"
                               "Connection closed", 255)
    assert "(ssh: connect to host www.example.com port 22: Connection refused)" in closed.message


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
    assert validate(_ssh_profile(host="2001:db8::5")) == [] and validate(_ssh_profile(host="[2001:db8::5]")) == []
    assert validate(_ssh_profile(prefix="Not A Prefix!")) == []  # a bucket setting, hidden for SSH
    assert validate(_ssh_profile(identity_file="/home/me/keys 100%/id")) == []
    local = PublicationProfile()
    local.destination.port = "abc"  # every kind: the window would fail on it
    assert any("destination.port" in p for p in validate(local))
    for settings, message in [({"host": ""}, "destination.host"),
                              ({"host": "-oProxyCommand=x"}, "destination.host"),
                              ({"host": "a b"}, "destination.host"), ({"port": 0}, "destination.port"),
                              ({"user": "-l"}, "destination.user"), ({"remote_dir": ""}, "destination.remoteDir"),
                              ({"remote_dir": "/"}, "destination.remoteDir"),
                              ({"remote_dir": "/."}, "destination.remoteDir"),
                              ({"remote_dir": "/srv/.."}, "destination.remoteDir"),
                              ({"host": "www.example.com:2222"}, "destination.host"),
                              ({"identity_file": "/keys/${HOME}/id"}, "destination.identityFile"),
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
    try:  # a hardened server's umask: new files 0640, folders 0750 unless QWebMap fixes them
        running = publishing_sshd.SshServer(str(tmp_path / "sshd"), sftp_umask="027").start()
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
          "assets/légende [1].json": b'{"a": 1}', "old/only here.json": b"bye", "-legend.json": b"[]"}


def _mode(path):
    return os.stat(path).st_mode & 0o777


@needs_sshd
def test_publish_twice_into_a_folder_with_other_files(server, tmp_path):
    remote = tmp_path / "server" / "web root" / "town maps"
    remote.mkdir(parents=True)
    remote.chmod(0o750)  # the user's own setting: never changed
    (remote / "notes of the webmaster.txt").write_text("not ours")
    (remote / "index.html").write_text("MY HOMEPAGE")  # replaced by the map's, as the user wants
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
    assert any("8 new or changed files" in m for m in messages if m)
    assert [m for m in messages if m and "QWebMap had not uploaded" in m] == [
        "Existing files QWebMap had not uploaded, replaced by the map's: 1 (index.html)"]
    # Under umask 027 the folders QWebMap created are still readable by the web server.
    for folder in ("data", "glyphs", "glyphs/Noto Sans Regular", "assets", "old"):
        assert _mode(remote / folder) == 0o755, folder
    assert _mode(remote) == 0o750 and _mode(on_server["data/map.pmtiles"]) == 0o644
    before = {rel: os.stat(path) for rel, path in on_server.items()}

    site_2 = dict(SITE_1, **{"index.html": b"<html>second</html>"})
    del site_2["old/only here.json"]
    messages.clear()
    second = publish_folder(_Release(tmp_path / "rel-2", site_2, "r-20260102T000000Z-0000000b"), profile,
                            provider, str(tmp_path / "work"), lambda p, m: messages.append(m), verify=False)
    assert second.state == ReleaseState.PUBLISHED, second.message
    assert second.message.startswith("Published into ") and not second.warnings
    on_server = _remote_files(remote)
    assert set(on_server) == set(site_2) | {"release.json", STATE_NAME, "notes of the webmaster.txt"}
    assert not (remote / "old").exists()  # its only file was ours
    assert (remote / "notes of the webmaster.txt").read_text() == "not ours"
    assert (remote / "index.html").read_bytes() == b"<html>second</html>"
    assert any("2 new or changed files" in m and "5 unchanged, 1 to delete" in m for m in messages if m)
    assert not [m for m in messages if m and "QWebMap had not uploaded" in m]
    for rel in ("style.json", "data/map.pmtiles", "glyphs/Noto Sans Regular/0-255.pbf",
                "assets/légende [1].json", "-legend.json"):
        now = os.stat(on_server[rel])  # not uploaded again: same file, same time
        assert (now.st_ino, now.st_mtime_ns) == (before[rel].st_ino, before[rel].st_mtime_ns), rel
    assert os.stat(on_server["index.html"]).st_ino != before["index.html"].st_ino
    assert not [p for p in on_server if "q2vt-tmp" in p or "q2vt-probe" in p]
    state = json.loads((remote / STATE_NAME).read_text(encoding="utf-8"))
    assert state["complete"] and state["releaseId"] == "r-20260102T000000Z-0000000b"
    assert set(state["files"]) == set(site_2) | {"release.json"}
    assert _mode(on_server["index.html"]) == 0o644


@needs_sshd
def test_repair_failed_deletion_and_another_maps_folder(server, tmp_path):
    """A file deleted on the server by hand is uploaded again; a deletion
    that fails stays listed (warning) and is retried; a folder of another
    QWebMap publication is taken over (one log line), never refused."""
    remote = tmp_path / "server" / "map"
    profile = PublicationProfile(title="Town map", slug="town-map", publication_id=PUBLICATION)
    profile.destination = server.destination(str(remote))
    provider = server.provider(profile.destination)
    work = str(tmp_path / "work")
    assert publish_folder(_Release(tmp_path / "rel-1", SITE_1), profile, provider, work,
                          verify=False).state == ReleaseState.PUBLISHED
    (remote / "data" / "map.pmtiles").unlink()  # removed by hand on the server
    (remote / "old" / "only here.json").unlink()  # its path now holds something rm cannot delete
    (remote / "old" / "only here.json").mkdir()
    (remote / "old" / "only here.json" / "keep.txt").write_text("x")
    other = PublicationProfile(title="Another map", slug="another",
                               publication_id="11111111-2222-4333-8444-555555555555")
    other.destination = profile.destination
    site = dict(SITE_1)
    del site["old/only here.json"]
    messages = []
    taken = publish_folder(_Release(tmp_path / "rel-2", site, "r-20260102T000000Z-0000000b"), other, provider,
                           work, lambda p, m: messages.append(m), verify=False)
    assert taken.state == ReleaseState.PUBLISHED, taken.message
    assert [m for m in messages if m and "takes it over" in m] == [
        f"This folder held another QWebMap map (publication {PUBLICATION}); this map takes it over: the 8 "
        "files QWebMap uploaded for it are replaced or deleted."]
    assert "Missing or changed on the server, uploaded again: 1 (data/map.pmtiles)" in messages
    assert (remote / "data" / "map.pmtiles").read_bytes() == SITE_1["data/map.pmtiles"]
    [warning] = taken.warnings
    assert "could not be deleted" in warning and "old/only here.json: Failure" in warning
    state = json.loads((remote / STATE_NAME).read_text(encoding="utf-8"))
    assert state["publicationId"] == other.publication_id and "old/only here.json" in state["files"]
    (remote / "old" / "only here.json" / "keep.txt").unlink()  # fixed by hand
    (remote / "old" / "only here.json").rmdir()
    again = publish_folder(_Release(tmp_path / "rel-2", site, "r-20260102T000000Z-0000000b"), other, provider,
                           work, verify=False)
    assert again.state == ReleaseState.PUBLISHED and not again.warnings, (again.message, again.warnings)
    state = json.loads((remote / STATE_NAME).read_text(encoding="utf-8"))
    assert "old/only here.json" not in state["files"] and not (remote / "old").exists()
    # Without posix-rename the state is replaced by rm + rename: interrupted in between, only the
    # temporary state is there, and it still tells which files are QWebMap's.
    (remote / STATE_NAME).rename(remote / (STATE_NAME + ".q2vt-tmp"))
    del site["-legend.json"]
    assert publish_folder(_Release(tmp_path / "rel-3", site, "r-20260103T000000Z-0000000c"), other, provider,
                          work, verify=False).state == ReleaseState.PUBLISHED
    assert not (remote / "-legend.json").exists() and (remote / STATE_NAME).exists()
    assert not (remote / (STATE_NAME + ".q2vt-tmp")).exists()


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
    keys = tmp_path / "keys 100%"  # ssh expands %… in key paths
    keys.mkdir()
    with open(server.client_key, "rb") as source, open(keys / "id key", "wb") as target:
        target.write(source.read())
    (keys / "id key").chmod(0o600)
    destination = server.destination(str(remote), identity_file=str(keys / "id key"))
    checks = server.provider(destination).inspect()
    assert [ok for _, ok, _ in checks] == [True, True, True], checks
    assert ": created; a test file was written and deleted" in checks[1][2]
    assert remote.is_dir() and os.listdir(remote) == []  # created; the test file is gone
    for folder in (remote, remote.parent, remote.parent.parent):  # created under umask 027: still 755
        assert _mode(folder) == 0o755, folder
    assert ": exists;" in server.provider(destination).inspect()[1][2]
    locked = server.destination(str(remote), identity_file=server.locked_key)
    [(name, ok, message)] = server.provider(locked).inspect()
    assert name == "login" and not ok and "refused the login" in message
    assert "\nssh said: " in message and "Permission denied (publickey)" in message  # the real cause
    os.chmod(server.client_key, 0o644)  # a key other users can read: ssh ignores it
    try:
        [(name, ok, message)] = server.provider(server.destination(str(remote))).inspect()
    finally:
        os.chmod(server.client_key, 0o600)
    assert name == "login" and not ok and "other users can read it" in message, message
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
