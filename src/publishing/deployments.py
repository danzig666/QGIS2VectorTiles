"""
Remote publication protocol (plan §13): immutable releases, public
verification before activation, conditional pointer activation, rollback,
explicit retention.

    read pointer (+ETag) -> leak scan -> upload releases/<id>/... from the
    release.json inventory (resumable journal; release.json last) -> stable
    entry files -> verify through the public URL -> conditional write of
    current.json -> verify activation

A live archive is never overwritten: every release has its own folder.
Any failure before activation leaves the previous release current.
"""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

from .content_types import IMMUTABLE, NO_CACHE, content_type
from .errors import Cancelled, PublishingError
from .models import PublicationProfile, ReleaseState
from .profile import publication_prefix
from .progress import Progress
from .providers.s3 import Journal
from .public_verify import Http, VerifyResult, join_url, verify_pointer, verify_release
from .validation import scan_bundle
from .web_builder import RELEASE_ID, pointer_for, stable_files, validate_pointer, walk_files

SMALL_FILE = 4 * 1024 * 1024


@dataclass
class PublishResult:
    state: ReleaseState
    release_id: str
    stable_url: str = ""
    versioned_url: str = ""
    previous_release: Optional[str] = None
    checks: VerifyResult = field(default_factory=VerifyResult)
    message: str = ""
    code: str = ""
    uploaded_bytes: int = 0
    warnings: List[str] = field(default_factory=list)


def public_urls(profile: PublicationProfile, release_id: str):
    base = join_url(profile.destination.public_base_url, publication_prefix(profile))
    return (join_url(base, "index.html"), join_url(base, "releases", release_id, "index.html"),
            join_url(base, "releases", release_id) + "/", base)


def _check_pointer_owner(pointer: Optional[dict], profile: PublicationProfile) -> None:
    if pointer is None:
        return
    validate_pointer(pointer)
    if pointer.get("publicationId") != profile.publication_id:
        raise PublishingError(
            "Q2VT_PUB_DESTINATION",
            f"The prefix '{publication_prefix(profile)}' holds another publication "
            f"({pointer.get('publicationId')}). Choose another slug/prefix, or create the profile "
            "as an update of that publication.")


def upload_release(provider, release_dir: str, release_id: str, journal_path: str,
                   progress: Optional[Progress] = None, concurrency: int = 4) -> int:
    """Upload one immutable release from its inventory; returns bytes sent."""
    progress = progress or Progress()
    with open(os.path.join(release_dir, "release.json"), encoding="utf-8") as handle:
        inventory = json.load(handle)
    if inventory.get("releaseId") != release_id:
        raise PublishingError("Q2VT_PUB_BUNDLE_INVALID", "release.json does not match the release.")
    listed = {item["path"] for item in inventory["files"]}
    actual = set(walk_files(release_dir)) - {"release.json"}
    if listed != actual:  # never upload anything that is not in the validated inventory
        raise PublishingError("Q2VT_PUB_BUNDLE_INVALID", "The release folder changed after validation: "
                              f"{sorted(listed ^ actual)[:5]}")
    journal = Journal(journal_path, {"kind": provider.kind, "bucket": getattr(provider, "bucket", ""),
                                     "endpoint": getattr(provider, "endpoint", ""),
                                     "prefix": provider.prefix, "release": release_id})
    files = sorted(inventory["files"], key=lambda f: f["size"])
    total = sum(f["size"] for f in files) or 1
    sent = [0]
    lock = threading.Lock()

    def one(item):
        progress.check()
        rel = f"releases/{release_id}/{item['path']}"
        state = journal.get(rel)
        if state.get("done") and state.get("sha256", item["sha256"]) == item["sha256"]:
            head = provider.head(rel)
            if head and head["size"] == item["size"]:
                return item["size"]
        etag = provider.put_file(rel, os.path.join(release_dir, *item["path"].split("/")),
                                 item["contentType"], item.get("cacheControl", IMMUTABLE),
                                 item["sha256"], progress=progress.sub(0, 1) if item["size"] > SMALL_FILE
                                 else None, journal=journal)
        journal.update(rel, {"sha256": item["sha256"]})
        journal.done(rel, etag)
        with lock:
            sent[0] += item["size"]
            progress.update(sent[0] / total, f"Uploaded {sent[0] // 1024} / {total // 1024} KiB")
        return item["size"]

    small = [f for f in files if f["size"] <= SMALL_FILE]
    large = [f for f in files if f["size"] > SMALL_FILE]
    with ThreadPoolExecutor(max_workers=max(1, concurrency), thread_name_prefix="q2vt-upload") as pool:
        for future in [pool.submit(one, item) for item in small]:
            future.result()
    for item in large:
        one(item)
    # The inventory last: a listed release with release.json is complete.
    path = os.path.join(release_dir, "release.json")
    from .bundle import sha256_path  # pylint: disable=import-outside-toplevel
    provider.put_file(f"releases/{release_id}/release.json", path, content_type("release.json"),
                      IMMUTABLE, sha256_path(path))
    return sent[0]


def upload_stable_entry(provider, publication_dir: str) -> None:
    """index.html (no-cache) and the content-hashed bootstrap module."""
    from .bundle import sha256_path  # pylint: disable=import-outside-toplevel
    for name in stable_files(publication_dir):
        if name == "current.json":
            continue
        path = os.path.join(publication_dir, name)
        if name == "index.html":
            with open(path, "rb") as handle:
                provider.put_bytes(name, handle.read(), content_type(name), NO_CACHE)
        else:
            provider.put_file(name, path, content_type(name), IMMUTABLE, sha256_path(path))


def activate(provider, profile: PublicationProfile, release_id: str, expected_etag: Optional[str],
             expected_release: Optional[str]) -> dict:
    """Conditional write of current.json; reconciles ambiguous failures by
    reading the pointer back. Raises Q2VT_PUB_ACTIVATION_CONFLICT when
    another publisher moved the pointer."""
    pointer = pointer_for(profile.publication_id, release_id)
    data = json.dumps(pointer, indent=1).encode("utf-8")
    if not provider.conditional_writes:
        raise PublishingError("Q2VT_PUB_ACTIVATION", "This destination cannot do conditional writes; "
                              "activation would not be conflict-safe. Share the versioned link.")
    try:
        provider.put_bytes("current.json", data, content_type("current.json"), NO_CACHE,
                           if_match=expected_etag if expected_release is not None else None,
                           if_none_match=expected_release is None)
    except PublishingError as error:
        # Read back before deciding: a retried write whose first attempt
        # succeeded (response lost) fails its precondition against itself.
        now, _ = provider.read_pointer()
        if now and now.get("releaseId") == release_id:
            return now
        if error.code == "Q2VT_PUB_ACTIVATION_CONFLICT":
            raise
        if now and now.get("releaseId") != expected_release:
            raise PublishingError("Q2VT_PUB_ACTIVATION_CONFLICT",
                                  f"current.json now points at {now.get('releaseId')}.") from error
        raise
    return pointer


def publish(release, profile: PublicationProfile, provider, work_dir: str, progress=None,
            secrets: Iterable[str] = (), viewer_origin: Optional[str] = None,
            http: Optional[Http] = None, verify: bool = True, activate_release: bool = True) -> PublishResult:
    """Upload, verify and activate a validated local ``release``
    (web_builder.ReleaseResult). Returns the final state; never raises for
    an expected outcome (verification failure, conflict, cancel)."""
    progress = progress if isinstance(progress, Progress) else Progress(progress)
    release_id = release.release_id
    stable_url, versioned_url, release_url, _ = public_urls(profile, release_id)
    result = PublishResult(ReleaseState.FAILED, release_id, stable_url, versioned_url)
    try:
        pointer, etag = provider.read_pointer()
        _check_pointer_owner(pointer, profile)
        result.previous_release = pointer.get("releaseId") if pointer else None
        problems = scan_bundle(release.release_dir, walk_files(release.release_dir), list(secrets))
        problems += scan_bundle(release.publication_dir, [n for n in stable_files(release.publication_dir)
                                                          if n != "current.json"], list(secrets))
        if problems:
            raise PublishingError("Q2VT_PUB_SECRET_LEAK", "; ".join(problems[:5]))
        progress.info(f"Uploading release {release_id}...")
        journal = os.path.join(work_dir, f"upload-{release_id}.json")
        result.uploaded_bytes = upload_release(provider, release.release_dir, release_id, journal,
                                               progress.sub(0.0, 0.8))
        upload_stable_entry(provider, release.publication_dir)
        result.state = ReleaseState.UPLOADED_NOT_ACTIVE
        if verify:
            progress.update(0.82, "Checking the public URL...", force=True)
            result.checks = verify_release(release_url, release.release_dir, viewer_origin, http)
            if not result.checks.ok:
                result.code = result.checks.failure_code()
                result.message = "; ".join(f"{c.name}: {c.detail}" for c in result.checks.checks
                                           if not c.ok)[:2000]
                return result
        if not activate_release:
            result.message = "Uploaded and verified; not activated (versioned link only)."
            return result
        progress.update(0.92, "Activating the release...", force=True)
        activate(provider, profile, release_id, etag, result.previous_release)
        result.state = ReleaseState.PUBLISHED
        if verify:
            check = verify_pointer(join_url(profile.destination.public_base_url, publication_prefix(profile)),
                                   release_id, http)
            result.checks.checks.append(check)
            if not check.ok:
                result.warnings.append("The public current.json still shows the previous release "
                                       "(a CDN cache?). The versioned link works now. " + check.detail)
        progress.update(1.0, "Published.", force=True)
        return result
    except Cancelled:
        result.state = ReleaseState.CANCELLED if result.state != ReleaseState.PUBLISHED else result.state
        result.message = "Cancelled; the previous release stays current."
        return result
    except PublishingError as error:
        if error.code == "Q2VT_PUB_ACTIVATION_CONFLICT":
            result.state = ReleaseState.CONFLICT
            result.message = ("Another publisher activated a release first. This release stays "
                              "uploaded (not current); activate it from the history if you want.")
        else:
            result.message = error.message + (f" ({error.detail})" if error.detail else "")
        result.code = error.code
        return result


def rollback(provider, profile: PublicationProfile, release_id: str,
             http: Optional[Http] = None, verify: bool = True) -> PublishResult:
    """Point current.json at an existing, complete release (no upload)."""
    if not RELEASE_ID.match(release_id):
        raise PublishingError("Q2VT_PUB_PATH_UNSAFE", release_id)
    if provider.head(f"releases/{release_id}/release.json") is None:
        raise PublishingError("Q2VT_PUB_ACTIVATION", f"Release {release_id} is not complete remotely.")
    pointer, etag = provider.read_pointer()
    _check_pointer_owner(pointer, profile)
    stable_url, versioned_url, _, base = public_urls(profile, release_id)
    result = PublishResult(ReleaseState.FAILED, release_id, stable_url, versioned_url,
                           pointer.get("releaseId") if pointer else None)
    try:
        activate(provider, profile, release_id, etag, result.previous_release)
    except PublishingError as error:
        result.state = ReleaseState.CONFLICT if error.code == "Q2VT_PUB_ACTIVATION_CONFLICT" \
            else ReleaseState.FAILED
        result.code, result.message = error.code, error.message
        return result
    result.state = ReleaseState.PUBLISHED
    if verify:
        result.checks.checks.append(verify_pointer(base, release_id, http))
    return result


def retention_plan(provider, keep: int, protect: Iterable[str] = ()) -> List[str]:
    """Releases that retention would delete (oldest first): never the
    current one or ``protect``-ed ids. Nothing is deleted here."""
    pointer, _ = provider.read_pointer()
    protected = set(protect) | ({pointer["releaseId"]} if pointer else set())
    releases = provider.list_releases()
    removable = [r for r in releases if r not in protected]
    excess = max(0, len(releases) - max(1, keep))
    return removable[:excess]


def apply_retention(provider, release_ids: Iterable[str]) -> int:
    """Delete the given owned releases (explicitly approved by the user)."""
    pointer, _ = provider.read_pointer()
    current = pointer.get("releaseId") if pointer else None
    deleted = 0
    for release_id in release_ids:
        if release_id == current:
            raise PublishingError("Q2VT_PUB_ACTIVATION", "The current release cannot be deleted.")
        deleted += provider.delete_release(release_id)
    return deleted
