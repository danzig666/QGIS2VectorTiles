"""
Unversioned publication into one remote folder (SSH / SFTP destination).

The built release's content — what the offline ZIP holds: a self-contained
site with its own ``index.html`` — is written straight into the folder the
user chose. No ``releases/<id>/`` folders, no ``current.json``, no
retention or rollback: the previous site is replaced file by file.

    read the folder's state file -> plan (new / changed / unchanged /
    removed files) -> one sftp session: state file marking the files about
    to change, uploads to temporary names, renames over the targets
    (index.html last), deletion of the removed files, the new state file
    -> check the public URL (optional)

The state file (``.q2vt-files.json`` in the folder) lists what QWebMap
uploaded there: relative path -> SHA-256 and size. Only files it lists are
ever deleted; anything else in the folder stays. Unchanged files are not
uploaded again. A failure or cancel midway leaves the previous site with
only the already renamed files changed; those are listed without a hash, so
the next publish uploads them again and completes the folder.
"""

import json
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

from .bundle import sha256_path
from .deployments import PublishResult
from .errors import Cancelled, PublishingError
from .models import PublicationProfile, ReleaseState
from .progress import Progress
from .providers.ssh import STATE_NAME, TMP_SUFFIX, batch_line, local_path, tmp_name
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


def read_state(data: Optional[bytes], publication_id: str) -> Dict[str, dict]:
    """The files a previous publish listed (``{}``: none, or an unreadable
    state). Entries that are not safe relative paths are dropped, so a
    tampered file can never make QWebMap delete anything outside the folder.
    Raises when the folder holds another publication."""
    if not data:
        return {}
    try:
        state = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(state, dict) or state.get("format") != STATE_FORMAT or \
            not isinstance(state.get("files"), dict):
        return {}
    owner = state.get("publicationId")
    if owner and owner != publication_id and state["files"]:
        raise PublishingError(
            "Q2VT_PUB_DESTINATION",
            f"This folder holds another web map published by QWebMap (publication {owner}). Choose "
            f"another folder, or delete {STATE_NAME} there to let this map take the folder over.")
    files = {}
    for path, entry in state["files"].items():
        try:
            path = safe_relative_path(path)
        except PublishingError:
            continue
        if path == STATE_NAME or path.endswith(TMP_SUFFIX) or not isinstance(entry, dict):
            continue
        files[path] = {"sha256": str(entry.get("sha256") or ""), "size": entry.get("size")}
    return files


def _state(files: Dict[str, dict], publication_id: str, release_id: str, complete: bool) -> dict:
    return {"format": STATE_FORMAT, "version": 1, "publicationId": publication_id, "releaseId": release_id,
            "complete": complete, "files": {path: dict(files[path]) for path in sorted(files)}}


def plan_sync(previous: Dict[str, dict], files: Dict[str, dict], publication_id: str = "",
              release_id: str = "") -> SyncPlan:
    """What to upload, keep and delete. A previous entry without a hash (an
    interrupted publish) never matches: that file is uploaded again."""
    plan = SyncPlan()
    for path in sorted(files):
        old = previous.get(path) or {}
        same = old.get("sha256") and old.get("sha256") == files[path]["sha256"] \
            and old.get("size") == files[path]["size"]
        (plan.unchanged if same else plan.upload).append(path)
    plan.upload.sort(key=lambda p: (p == ENTRY, p))
    plan.delete = sorted(p for p in previous if p not in files)
    plan.leftovers = sorted(p for p, entry in previous.items() if not entry.get("sha256") and p not in files)
    pending = {p: previous[p] for p in previous if p not in plan.upload}
    pending.update({p: {"sha256": "", "size": files[p]["size"]} for p in plan.upload})
    plan.pending = _state(pending, publication_id, release_id, False)
    plan.final = _state(files, publication_id, release_id, True)
    return plan


def _parents(paths: Iterable[str]) -> set:
    found = set()
    for path in paths:
        parts = path.split("/")[:-1]
        found.update("/".join(parts[:index]) for index in range(1, len(parts) + 1))
    return found


def upload_batch(plan: SyncPlan, remote_dir: str, release_dir: str, state_dir: str,
                 files: Dict[str, dict], atomic: bool = True) -> List[Tuple[str, int, str]]:
    """The upload session's sftp batch as ``(line, bytes it uploads, file)``.
    ``state_dir`` holds ``pending.json`` and ``final.json``. Without an
    atomic rename (no posix-rename on the server) a target is deleted just
    before the rename."""
    lines: List[Tuple[str, int, str]] = []

    def add(line: str, size: int = 0, path: str = ""):
        lines.append((line, size, path))

    def replace(source: str, target: str):
        if not atomic:
            add(batch_line("rm", target, ignore_errors=True))
        add(batch_line("rename", source, target))

    state_tmp = STATE_NAME + TMP_SUFFIX
    add(batch_line("cd", remote_dir))
    add(batch_line("lcd", local_path(state_dir)))
    add(batch_line("put", "pending.json", state_tmp))
    replace(state_tmp, STATE_NAME)
    add(batch_line("lcd", local_path(release_dir)))
    for folder in sorted(_parents(plan.upload), key=lambda f: (f.count("/"), f)):
        add(batch_line("mkdir", folder, ignore_errors=True))
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
    add(batch_line("put", "final.json", state_tmp))
    replace(state_tmp, STATE_NAME)
    return lines


def _write_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1, sort_keys=True)


def folder_url(profile: PublicationProfile) -> str:
    """The public address of the folder (``…/``), or "" when not given."""
    base = (profile.destination.public_base_url or "").strip().rstrip("/")
    return base + "/" if base else ""


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
        if problems:
            raise PublishingError("Q2VT_PUB_SECRET_LEAK", "; ".join(problems[:5]))
        os.makedirs(work_dir, exist_ok=True)
        local = tempfile.mkdtemp(prefix="sftp-", dir=work_dir)
        progress.update(0.0, f"Connecting to {provider.describe()}…", force=True)
        data, atomic = provider.prepare(local, read_state=True, progress=progress.sub(0.0, 0.03))
        plan = plan_sync(read_state(data, profile.publication_id), files, profile.publication_id,
                         release.release_id)
        size = sum(files[p]["size"] for p in plan.upload)
        progress.info(f"{len(plan.upload)} new or changed files ({size / 1e6:.1f} MB) to upload, "
                      f"{len(plan.unchanged)} unchanged, {len(plan.delete)} to delete")
        if not atomic:
            result.warnings.append("The server cannot rename over an existing file (no posix-rename): "
                                   "each changed file was briefly missing while it was replaced.")
        _write_json(os.path.join(local, "pending.json"), plan.pending)
        _write_json(os.path.join(local, "final.json"), plan.final)
        batch = upload_batch(plan, provider.remote_dir, release.release_dir, local, files, atomic)
        labels = {index: f"Uploading {path} ({size / 1e6:.1f} MB)…"
                  for index, (_, size, path) in enumerate(batch) if size > LARGE}
        provider.run([line for line, _, _ in batch], progress.sub(0.03, 0.9),
                     [size for _, size, _ in batch], labels)
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
        result.message = error.message + (f" ({error.detail})" if error.detail else "")
        return result
    finally:
        if local:
            shutil.rmtree(local, ignore_errors=True)
