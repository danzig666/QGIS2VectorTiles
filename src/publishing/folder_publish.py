"""
Unversioned publication into one remote folder (SSH / SFTP destination).

The built release's content — what the offline ZIP holds: a self-contained
site with its own ``index.html`` — is written straight into the folder the
user chose. No ``releases/<id>/`` folders, no ``current.json``, no
retention or rollback: the previous site is replaced file by file.

    one sftp session: create the folder, read its state file, list it ->
    plan (new / changed / unchanged / removed files) -> one sftp session:
    state file marking the files about to change, uploads to temporary
    names, renames over the targets (index.html last), deletion of the
    removed files, the new state file -> check the public URL (optional)

The state file (``.q2vt-files.json`` in the folder) lists what QWebMap
uploaded there: relative path -> SHA-256 and size. Only files it lists are
ever deleted; anything else in the folder stays (a file at the same path as
one of the map's is replaced, which the log counts). Unchanged files are not
uploaded again, unless the listing shows them missing or with another size.
A failure or cancel midway leaves the previous site with only the already
renamed files changed; those are listed without a hash, so the next publish
uploads them again and completes the folder. A deletion that fails stays
listed, so the next publish tries again. Another publication's state file
is taken over: its files count as QWebMap's.
"""

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote, urlsplit, urlunsplit

from .bundle import sha256_path
from .deployments import PublishResult
from .errors import Cancelled, PublishingError
from .models import PublicationProfile, ReleaseState
from .progress import Progress
from .providers.ssh import STATE_NAME, TMP_SUFFIX, batch_arg, batch_line, local_path, tmp_name
from .public_verify import Http, verify_release
from .validation import safe_relative_path, scan_bundle
from .web_builder import walk_files

STATE_FORMAT = "qwebmap-folder-state"
ENTRY = "index.html"
LARGE = 4 * 1024 * 1024


@dataclass
class SyncPlan:
    upload: List[str] = field(default_factory=list)     # new or changed (entry last)
    unchanged: List[str] = field(default_factory=list)
    delete: List[str] = field(default_factory=list)     # listed before, not in the new site
    leftovers: List[str] = field(default_factory=list)  # their temporary files may be left over
    replaced: List[str] = field(default_factory=list)   # existing files QWebMap had not uploaded
    repaired: List[str] = field(default_factory=list)   # listed, but missing or another size there
    folders: List[str] = field(default_factory=list)    # to create (mkdir)
    new_folders: List[str] = field(default_factory=list)  # known to be missing: chmod after mkdir
    blocked: List[str] = field(default_factory=list)    # folders the site needs, files there
    pending: dict = field(default_factory=dict)         # state written before anything changes
    final: dict = field(default_factory=dict)           # state written at the end


def site_files(release_dir: str) -> Dict[str, dict]:
    """``{path: {sha256, size}}`` of the release folder's content (the
    release.json inventory plus release.json itself); refuses a folder that
    changed after validation."""
    path = os.path.join(release_dir, "release.json")
    with open(path, encoding="utf-8") as handle:
        inventory = json.load(handle)
    files = {item["path"]: {"sha256": item["sha256"], "size": int(item["size"])}
             for item in inventory["files"]}
    actual = set(walk_files(release_dir)) - {"release.json"}
    if set(files) != actual:  # never upload anything that is not in the validated inventory
        raise PublishingError("Q2VT_PUB_BUNDLE_INVALID", "The release folder changed after validation: "
                              f"{sorted(set(files) ^ actual)[:5]}")
    files["release.json"] = {"sha256": sha256_path(path), "size": os.path.getsize(path)}
    return files


def read_state(data: Optional[bytes]) -> Tuple[Dict[str, dict], str]:
    """The files a previous publish listed (``{}``: none, or an unreadable
    state) and the publication that wrote it. Entries that are not safe
    relative paths are dropped, so a tampered file can never make QWebMap
    delete anything outside the folder."""
    if not data:
        return {}, ""
    try:
        state = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}, ""
    if not isinstance(state, dict) or state.get("format") != STATE_FORMAT or \
            not isinstance(state.get("files"), dict):
        return {}, ""
    files = {}
    for path, entry in state["files"].items():
        try:
            path = safe_relative_path(path)
        except PublishingError:
            continue
        if path == STATE_NAME or path.endswith(TMP_SUFFIX) or not isinstance(entry, dict):
            continue
        files[path] = {"sha256": str(entry.get("sha256") or ""), "size": entry.get("size")}
        if entry.get("foreign") is True:
            files[path]["foreign"] = True
    return files, str(state.get("publicationId") or "")


def _state(files: Dict[str, dict], publication_id: str, release_id: str, complete: bool) -> dict:
    return {"format": STATE_FORMAT, "version": 1, "publicationId": publication_id, "releaseId": release_id,
            "complete": complete, "files": {path: dict(files[path]) for path in sorted(files)}}


def plan_sync(previous: Dict[str, dict], files: Dict[str, dict], publication_id: str = "",
              release_id: str = "", remote: Optional[Dict[str, Tuple[str, int]]] = None) -> SyncPlan:
    """What to upload, keep and delete. A previous entry without a hash (an
    interrupted publish) never matches: that file is uploaded again.
    ``remote`` (the folder's listing, ``{path: (type, size)}``; None:
    unknown) also re-uploads listed files that are missing or have another
    size there, and tells which existing files QWebMap had not uploaded are
    replaced and which folders are new.

    An entry marked ``foreign`` (a file that was not QWebMap's when an
    interrupted publish planned to replace it) is never deleted: only its
    temporary file is removed."""
    plan = SyncPlan()
    for path in sorted(files):
        old = previous.get(path) or {}
        same = old.get("sha256") and old.get("sha256") == files[path]["sha256"] \
            and old.get("size") == files[path]["size"]
        if same and remote is not None and remote.get(path) != ("-", files[path]["size"]):
            same = False
            plan.repaired.append(path)
        (plan.unchanged if same else plan.upload).append(path)
    plan.upload.sort(key=lambda p: (p == ENTRY, p))
    # Not QWebMap's (yet): new paths that may exist there (all of them when the listing is unknown);
    # a folder of the user's there too (the rename fails on it, but it must never count as QWebMap's).
    foreign = {p for p in plan.upload if (p not in previous or previous[p].get("foreign"))
               and (remote is None or p in remote)}
    if remote is not None:
        plan.replaced = sorted(p for p in foreign if remote[p][0] != "d")
    plan.delete = sorted(p for p in previous if p not in files and not previous[p].get("foreign"))
    plan.leftovers = sorted(p for p, entry in previous.items() if not entry.get("sha256") and p not in files)
    needed = _parents(plan.upload)
    existing = {p for p, (kind, _) in (remote or {}).items() if kind in ("d", "l")}
    plan.folders = sorted(needed - existing, key=lambda f: (f.count("/"), f))
    plan.new_folders = [f for f in plan.folders if remote is not None and f not in remote]
    plan.blocked = sorted(f for f in _parents(files) if f in (remote or {}) and f not in existing)
    pending = {p: previous[p] for p in previous if p not in plan.upload}
    pending.update({p: {"sha256": "", "size": files[p]["size"]} for p in plan.upload})
    for path in foreign:  # QWebMap's only once renamed: never deleted on the strength of this entry
        pending[path]["foreign"] = True
    plan.pending = _state(pending, publication_id, release_id, False)
    plan.final = _state(files, publication_id, release_id, True)
    return plan


def _parents(paths: Iterable[str]) -> set:
    found = set()
    for path in paths:
        parts = path.split("/")[:-1]
        found.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
    return found


def state_lines(state_name: str, atomic: bool) -> List[str]:
    """Replace the state file by the local ``state_name`` (in the current
    local folder): upload to a temporary name, rename over it."""
    tmp = STATE_NAME + TMP_SUFFIX
    lines = [batch_line("put", state_name, tmp)]
    if not atomic:
        lines.append(batch_line("rm", STATE_NAME, ignore_errors=True))
    return lines + [batch_line("rename", tmp, STATE_NAME)]


def upload_batch(plan: SyncPlan, remote_dir: str, release_dir: str, state_dir: str,
                 files: Dict[str, dict], atomic: bool = True,
                 folder_mode: int = 0o755) -> List[Tuple[str, int, str]]:
    """The upload session's sftp batch as ``(line, bytes it uploads, file)``.
    ``state_dir`` holds ``pending.json`` and ``final.json``. Without an
    atomic rename (no posix-rename on the server) a target is deleted just
    before the rename. New folders get ``folder_mode`` (ssh.folder_mode)."""
    lines: List[Tuple[str, int, str]] = []

    def add(line: str, size: int = 0, path: str = ""):
        lines.append((line, size, path))

    def replace(source: str, target: str):
        if not atomic:
            add(batch_line("rm", target, ignore_errors=True))
        add(batch_line("rename", source, target))

    add(batch_line("cd", remote_dir))
    add(batch_line("lcd", local_path(state_dir)))
    for line in state_lines("pending.json", atomic):
        add(line)
    add(batch_line("lcd", local_path(release_dir)))
    for folder in plan.folders:
        add(batch_line("mkdir", folder, ignore_errors=True))
        if folder in plan.new_folders:  # readable by the web server whatever the server's umask
            add(batch_line(f"chmod {folder_mode:o}", folder, ignore_errors=True))
    for path in sorted(plan.upload, key=lambda p: (files[p]["size"], p)):  # small files first
        add(batch_line("put", path, tmp_name(path)), files[path]["size"], path)
        add(batch_line("chmod 644", tmp_name(path), ignore_errors=True))  # readable by the web server
    for path in plan.upload:  # index.html last: visitors never get a new page with old data
        replace(tmp_name(path), path)
    for path in plan.delete:
        add(batch_line("rm", path, ignore_errors=True))
    for path in plan.leftovers:
        add(batch_line("rm", tmp_name(path), ignore_errors=True))
    # Folders that held only removed files (rmdir fails, harmlessly, on any other content).
    for folder in sorted(_parents(plan.delete) - _parents(files), key=lambda f: (-f.count("/"), f)):
        add(batch_line("rmdir", folder, ignore_errors=True))
    add(batch_line("lcd", local_path(state_dir)))
    for line in state_lines("final.json", atomic):
        add(line)
    return lines


def failed_deletes(stderr: str, cwd: str, paths: Iterable[str]) -> Tuple[Dict[str, str], int]:
    """``({path: reason}, unattributed)`` of the ``rm`` lines that failed;
    a file that is already gone counts as deleted. sftp names the absolute
    path (``remote delete <cwd>/<path>: <reason>``; ``stderr`` with
    OpenSSH's escapes undone, ssh.unvis); older clients do not, and a line
    that matches no path also counts (``unattributed``)."""
    failed = {}
    lines = [line for line in stderr.splitlines() if "no such file" not in line.lower()]
    matched = set()
    for path in paths:
        prefix = f"remote delete {cwd.rstrip('/')}/{batch_arg(path)}: " if cwd else None
        for index, line in enumerate(lines):
            if prefix and line.startswith(prefix):
                failed[path] = line[len(prefix):].strip()
                matched.add(index)
    unattributed = sum(1 for index, line in enumerate(lines) if index not in matched and line.lower().startswith(
        ("couldn't delete file:", "remote delete ")))
    return failed, unattributed


def _names(paths: List[str]) -> str:
    return ", ".join(paths[:5]) + (", …" if len(paths) > 5 else "")


def _write_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)


def folder_url(profile: PublicationProfile) -> str:
    """The public address of the folder (``…/``), or "" when not given; its
    path percent-encoded (``/web maps/térkép`` → ``/web%20maps/t%C3%A9rk%C3%A9p``)."""
    base = (profile.destination.public_base_url or "").strip().rstrip("/")
    if not base:
        return ""
    parts = urlsplit(base)
    path = quote(parts.path, safe="/%:@!$&'()*+,;=~")
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")) + "/"


def publish_folder(release, profile: PublicationProfile, provider, work_dir: str, progress=None,
                   secrets: Iterable[str] = (), http: Optional[Http] = None,
                   verify: bool = True) -> PublishResult:
    """Write a validated local ``release`` into the provider's folder (see
    the module docstring). Never raises for an expected outcome."""
    progress = progress if isinstance(progress, Progress) else Progress(progress)
    url = folder_url(profile)
    result = PublishResult(ReleaseState.FAILED, release.release_id, url, "")
    local = ""
    try:
        files = site_files(release.release_dir)
        problems = scan_bundle(release.release_dir, sorted(files), list(secrets) + provider.secrets())
        leaked = [p.rsplit(": ", 1)[0] for p in problems
                  if p.endswith((": credential value", ": secret/canary bytes"))]
        if leaked and provider.secrets():  # the SSH password: a word a person chose
            raise PublishingError(
                "Q2VT_PUB_SECRET_LEAK", f"The SSH password (or the key's passphrase) appears in the map's files "
                f"({_names(leaked)}): publishing them would make it public. If the map contains it by chance "
                "(a place name, say), use another password or a key; otherwise remove it from the data.")
        if problems:
            raise PublishingError("Q2VT_PUB_SECRET_LEAK", "; ".join(problems[:5]))
        os.makedirs(work_dir, exist_ok=True)
        local = tempfile.mkdtemp(prefix="sftp-", dir=work_dir)
        progress.update(0.0, f"Connecting to {provider.describe()}…", force=True)
        remote = provider.prepare(local, folders=sorted(_parents(files)), progress=progress.sub(0.0, 0.03))
        previous, owner = read_state(remote.state)
        if owner and owner != profile.publication_id and previous:
            progress.info(f"This folder held another QWebMap map (publication {owner}); this map takes "
                          f"it over: the {len(previous)} files QWebMap uploaded for it are replaced or "
                          "deleted.")
        plan = plan_sync(previous, files, profile.publication_id, release.release_id, remote.listing)
        if plan.blocked:  # never deleted (not QWebMap's): the user decides
            raise PublishingError("Q2VT_PUB_DESTINATION", "The map needs a folder where the server's folder has a "
                                  f"file: {_names(plan.blocked)}. Rename or remove that file, then publish again.")
        size = sum(files[p]["size"] for p in plan.upload)
        progress.info(f"{len(plan.upload)} new or changed files ({size / 1e6:.1f} MB) to upload, "
                      f"{len(plan.unchanged)} unchanged, {len(plan.delete)} to delete")
        if plan.replaced:
            progress.info(f"Existing files QWebMap had not uploaded, replaced by the map's: "
                          f"{len(plan.replaced)} ({_names(plan.replaced)})")
        if plan.repaired:
            progress.info(f"Missing or changed on the server, uploaded again: {len(plan.repaired)} "
                          f"({_names(plan.repaired)})")
        if not remote.atomic:
            result.warnings.append("The server cannot rename over an existing file (no posix-rename): "
                                   "each changed file was briefly missing while it was replaced.")
        _write_json(os.path.join(local, "pending.json"), plan.pending)
        _write_json(os.path.join(local, "final.json"), plan.final)
        batch = upload_batch(plan, provider.remote_dir, release.release_dir, local, files, remote.atomic,
                             remote.folder_mode)
        labels = {index: f"Uploading {path} ({size / 1e6:.1f} MB)…"
                  for index, (_, size, path) in enumerate(batch) if size > LARGE}
        _, stderr = provider.run([line for line, _, _ in batch], progress.sub(0.03, 0.9),
                                 [size for _, size, _ in batch], labels)
        failed, unattributed = failed_deletes(stderr, remote.cwd, plan.delete)
        # Still QWebMap's: listed again, so the next publish tries again (all of them when sftp did
        # not say which; a file already gone is then simply dropped).
        retry = plan.delete if unattributed else sorted(failed)
        if retry:
            kept = dict(plan.final["files"], **{p: previous[p] for p in retry})
            _write_json(os.path.join(local, "final.json"),
                        _state(kept, profile.publication_id, release.release_id, True))
            listed = f"They stay listed in {STATE_NAME} and the next publish tries again."
            try:
                provider.run([batch_line("cd", provider.remote_dir), batch_line("lcd", local_path(local))]
                             + state_lines("final.json", remote.atomic))
            except PublishingError as error:
                listed = f"They could not be listed for the next publish either ({error.message})."
            named = "; ".join(f"{p}: {why}" for p, why in sorted(failed.items())[:5])
            if unattributed:
                named = "; ".join(filter(None, [named, f"{unattributed} more: sftp did not say which"]))
            result.warnings.append(f"{len(failed) + unattributed} files of the previous map could not be "
                                   f"deleted from the server ({named}). {listed}")
        result.uploaded_bytes = size
        result.state = ReleaseState.PUBLISHED
        result.message = f"Published into {provider.describe()}."
        if verify and url:
            progress.update(0.92, "Checking the public URL...", force=True)
            try:
                result.checks = verify_release(url, release.release_dir, None, http)
            except PublishingError as error:
                result.warnings.append(f"The public URL could not be checked: {error.message}")
            if not result.checks.ok:
                result.code = result.checks.failure_code()
                result.warnings.append(
                    "The files are on the server, but " + url + " does not serve them as the map needs "
                    "(wrong public URL, a cache, or the web server's settings – see HOSTING.md): "
                    + "; ".join(f"{c.name}: {c.detail}" for c in result.checks.checks if not c.ok)[:1500])
        progress.update(1.0, "Published.", force=True)
        return result
    except Cancelled:
        result.state = ReleaseState.CANCELLED
        result.message = ("Cancelled. Files already replaced on the server stay; publish again to "
                          "complete the folder.")
        return result
    except PublishingError as error:
        result.code = error.code
        again = " Publish again to complete the folder." if getattr(error, "retryable", False) else ""
        result.message = error.message + again + (f" ({error.detail})" if error.detail else "")
        return result
    finally:
        if local:
            shutil.rmtree(local, ignore_errors=True)
