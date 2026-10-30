#!/usr/bin/env python3
# state_writer: false
"""The retention report (workflow-2.20.0, `retention-report`, D-Surface,
D-Finished, D-Report, D-Output, D-Writes).

Read-only: which work items of this repository are finished by the rule of
`docs/RETENTION.md`, how large each one's untracked `.ai-review/<id>/` folder
is, and whether an archive of it exists outside the repository and is older
than the time to live.

    python3 -I scripts/workflow_retention.py report [--json] [--archive-root DIR]
                                                    [--ttl-days N] [--repo-root DIR]

It writes nothing: no archive, no removal, no state write, no stored fact. An
item is finished only when a fresh pull-request query says `merged`; every other
outcome, a failed query included, puts it in `not_finished` with a reason, and
an item that is not at `MILESTONE_COMPLETE` is never queried.

Bytecode. Run as a script (the form above) the module sets
`sys.dont_write_bytecode` before it imports `workflow_forge`, so no `__pycache__`
is written. When imported for `build_report`, the caller must set
`sys.dont_write_bytecode = True` (or run with `-B`, or set
`PYTHONDONTWRITEBYTECODE`) before the first import of this module: the cache
file is written while the import compiles the module, before any assignment
the module makes. An import without it may leave `__pycache__` in the scripts
directory, by Python's doing and not by a run of the report.

The archive (workflow-2.21.0, `retention-archive`):

    python3 -I scripts/workflow_retention.py archive <work-item-id> [--apply] [--json]
                                                     [--archive-root DIR] [--repo-root DIR]

packs the untracked `.ai-review/<id>/` folder of an item the report lists as
finished into `<archive-root>/<repo>/<id>.tar.zst` (outside the repository),
verifies it, links it into place without overwriting, appends one line to
`index.jsonl` and makes everything it created durable. A dry run without
`--apply`. It never writes, renames, moves or removes anything in the folder or
the repository; the folder stays. Needs the `zstd` program on the PATH.

The removal (workflow-2.22.0, `retention-removal`):

    python3 -I scripts/workflow_retention.py remove <work-item-id> [--apply] [--json]
                                                    [--archive-root DIR] [--repo-root DIR]

removes the untracked `.ai-review/<id>/` folder of a finished, archived item: one
atomic rename into `<archive-root>/<repo>/.removing/<id>`, a comparison of every
file's SHA-256 with the archive, and then the delete entry by entry, or the
rename back. A dry run without `--apply`. Nothing is deleted that is not proven
present in the archive with matching contents. Needs the `zstd` program.

The prune (workflow-2.24.0, `retention-prune`):

    python3 -I scripts/workflow_retention.py prune <work-item-id>... [--apply] [--json] [--ttl-days N]
                                                   [--archive-root DIR] [--repo-root DIR]

removes the named archives `<archive-root>/<repo>/<id>.tar.zst` that the report
lists as past the time to live: one `unlink` each through the archive
directory's descriptor, checked just before and made durable. A dry run without
`--apply`. The index is never opened and no other file is touched.

Stdlib-only plus `workflow_forge`.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse  # noqa: E402
import datetime  # noqa: E402
import errno  # noqa: E402
import fcntl  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import stat  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import tarfile  # noqa: E402

import workflow_forge  # noqa: E402

SCHEMA_VERSION = 1
KEEP_RECENT = 3
DEFAULT_TTL_DAYS = 30
MAX_TTL_DAYS = 3650
COMPLETE = "MILESTONE_COMPLETE"
STATE_PATH = "docs/ai-workflow/WORKFLOW_STATE.json"
ARCHIVE_SUFFIX = ".tar.zst"
DEFAULT_ARCHIVE_ROOT = "~/.local/share/workflow-archive"

RESERVED_IDS = ("runtime", "archive")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class RetentionError(Exception):
    """A refusal carrying a stable code, what is wrong and the one next step."""

    def __init__(self, code: str, message: str, next_step: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_step = next_step


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise RetentionError("usage", message, "run `python3 scripts/workflow_retention.py --help`")


def _iso(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_state(repo_root: Path) -> dict:
    path = repo_root / STATE_PATH
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RetentionError("state_missing", f"{STATE_PATH} does not exist in {repo_root}",
                             "run it in a Workflow repository, or pass --repo-root") from None
    except OSError as exc:
        raise RetentionError("state_invalid", f"{STATE_PATH} cannot be read: {exc}",
                             "repair the state file") from None
    try:
        state = json.loads(text)
    except ValueError as exc:
        raise RetentionError("state_invalid", f"{STATE_PATH} is not JSON: {exc}", "repair the state file") from None
    if not isinstance(state, dict) or not isinstance(state.get("work_items"), dict):
        raise RetentionError("state_invalid", f"{STATE_PATH} has no work_items object", "repair the state file")
    return state


def _anchor(item: dict) -> str | None:
    """The commit `/apply-pr-review` queries (`workflow_gate_policy.anchor_of`'s
    own two-field condition, without its review-identity computation)."""
    approval = item.get("technical_approval")
    commit = None
    if isinstance(approval, dict) and approval.get("reviewed_content_commit") \
            and approval.get("approved_review_content_id"):
        commit = approval["reviewed_content_commit"]
    elif item.get("reviewed_implementation_head"):
        commit = item["reviewed_implementation_head"]
    return commit if isinstance(commit, str) and _COMMIT_RE.match(commit) else None


def _tracked_counts(repo_root: Path) -> dict[str, int]:
    """Paths under `.ai-review/<id>/` that Git tracks, per id (one call)."""
    try:
        result = subprocess.run(["git", "ls-files", "-z", "--", ".ai-review"], cwd=repo_root,
                                capture_output=True, text=True)
    except OSError as exc:
        raise RetentionError("repository_unknown", f"git could not run: {exc}", "install Git") from None
    if result.returncode != 0:
        raise RetentionError("repository_unknown", f"git ls-files failed: {result.stderr.strip()}",
                             "run it inside a Git repository, or pass --repo-root")
    counts: dict[str, int] = {}
    for path in result.stdout.split("\0"):
        parts = path.split("/")
        if len(parts) >= 3 and parts[0] == ".ai-review":
            counts[parts[1]] = counts.get(parts[1], 0) + 1
    return counts


def _unreadable(errors: dict[str, str], path, exc: OSError) -> None:
    """Records a read failure that is not plain absence, once per path."""
    errors.setdefault(str(path), f"{type(exc).__name__}: {exc.strerror or exc}")


def _absent(exc: OSError) -> bool:
    return isinstance(exc, (FileNotFoundError, NotADirectoryError))


def _platform_ok() -> bool:
    """Descriptor-relative access without following links (D-Confine)."""
    needed = {"open", "stat", "mkdir", "link", "unlink", "rmdir"}
    return (needed <= {fn.__name__ for fn in os.supports_dir_fd} and hasattr(os, "fwalk")
            and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"))


def _open_review_root(repo_root: Path, errors: dict[str, str]) -> int | None:
    """`.ai-review` opened without following a link; None when absent or not
    readable as a real directory (the latter recorded in `errors`)."""
    path = repo_root / ".ai-review"
    try:
        return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            errors.setdefault(str(path), "symlink: not followed")
        elif exc.errno == errno.ENOTDIR:
            try:
                linked = stat.S_ISLNK(os.lstat(path).st_mode)
            except OSError:
                linked = False
            errors.setdefault(str(path), "symlink: not followed" if linked else "not a directory")
        elif not _absent(exc):
            _unreadable(errors, path, exc)
        return None


def _review_folder(ai_fd: int | None, repo_root: Path, item_id: str, tracked: dict[str, int],
                   errors: dict[str, str]) -> dict:
    """Reads only through the `.ai-review` descriptor, so a link in an ancestor
    of the folder is never followed (D-Confine)."""
    folder = repo_root / ".ai-review" / item_id
    files = size = 0
    present = False
    complete = True
    if ai_fd is not None:
        try:
            present = stat.S_ISDIR(os.stat(item_id, dir_fd=ai_fd, follow_symlinks=False).st_mode)
        except OSError as exc:
            if not _absent(exc):
                _unreadable(errors, folder, exc)
                complete = False
    if present:
        here = item_id

        def walk_failed(exc: OSError) -> None:
            nonlocal complete
            name = exc.filename if isinstance(exc.filename, str) else None
            where = folder if name is None else repo_root / ".ai-review" / here / name
            _unreadable(errors, where, exc)
            complete = False

        for directory, _, names, dirfd in os.fwalk(item_id, dir_fd=ai_fd, follow_symlinks=False,
                                                    onerror=walk_failed):
            here = directory
            for name in names:
                try:
                    info = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
                except OSError as exc:
                    if not _absent(exc):
                        _unreadable(errors, repo_root / ".ai-review" / directory / name, exc)
                        complete = False
                    continue
                if stat.S_ISREG(info.st_mode):
                    files += 1
                    size += info.st_size
    return {"present": present, "complete": complete, "files": files, "bytes": size,
            "tracked_files": tracked.get(item_id, 0)}


_ABSENT_ARCHIVE = {"present": False, "readable": True, "bytes": None, "modified": None, "age_days": None,
                   "prunable": False}


def _archive_fact(path: Path, now: datetime.datetime, ttl_days: int, errors: dict[str, str]) -> dict:
    """`readable` is false when the archive could not be looked at, which is not
    the same as absent: its `present` then says nothing."""
    absent = dict(_ABSENT_ARCHIVE)
    try:
        info = os.lstat(path)
    except OSError as exc:
        if not _absent(exc):
            _unreadable(errors, path, exc)
            return {**absent, "readable": False}
        return absent
    if not stat.S_ISREG(info.st_mode):
        return absent
    modified = datetime.datetime.fromtimestamp(info.st_mtime, datetime.timezone.utc)
    age = max(0, int((now - modified).total_seconds() // 86400))
    return {"present": True, "readable": True, "bytes": info.st_size, "modified": _iso(modified), "age_days": age,
            "prunable": age > ttl_days}


def _query(repo_root: Path, commit: str, run, resolve) -> dict | str:
    """The fresh facts, or the `query_failed:<code>` reason."""
    kwargs = {}
    if run is not None:
        kwargs["run"] = run
    if resolve is not None:
        kwargs["resolve"] = resolve
    try:
        return workflow_forge.query_forge_pr_facts(repo_root, commit, **kwargs)["facts"]
    except workflow_forge.ForgeError as exc:
        return f"query_failed:{exc.code}"
    except Exception:  # noqa: BLE001 - one failed query must not stop the others
        return "query_failed:unexpected"


def build_report(repo_root, *, archive_root, ttl_days: int = DEFAULT_TTL_DAYS, now: datetime.datetime,
                 run=None, resolve=None, review_root_fd: int | None = None) -> dict:
    """`review_root_fd` is an open descriptor of `.ai-review` (opened without
    following a link); the report then reads the folders only through it and
    does not close it. With None it opens `.ai-review` itself the same way."""
    repo_root = Path(repo_root)
    if not _platform_ok():
        raise RetentionError("path_unsafe", "this platform has no descriptor-relative file access",
                             "run it on Linux or macOS")
    state = _load_state(repo_root)
    try:
        repository = workflow_forge.origin_repository(repo_root)
    except workflow_forge.ForgeError as exc:
        raise RetentionError("repository_unknown", str(exc),
                             "add a github.com `origin` remote, or pass --repo-root") from None
    repo_name = repository.split("/", 1)[1]
    if repo_name in (".", ".."):
        raise RetentionError("repository_unknown", f"the repository name {repo_name!r} cannot name a directory",
                             "fix the `origin` remote, or pass --repo-root")
    archive_dir = Path(archive_root).expanduser() / repo_name
    items = state["work_items"]

    def phase_of(key):
        entry = items.get(key)
        return entry.get("phase") if isinstance(entry, dict) else None

    children: dict[str, list] = {}
    for key, entry in items.items():
        parent = entry.get("parent_work_item_id") if isinstance(entry, dict) else None
        if isinstance(parent, str):
            children.setdefault(parent, []).append(key)

    not_finished: list[dict] = []
    candidates: list[tuple[str, str, dict]] = []

    def reject(key, entry, reason):
        phase = entry.get("phase") if isinstance(entry, dict) else None
        not_finished.append({"work_item_id": key, "phase": phase if isinstance(phase, str) else None,
                             "reason": reason})

    for key, entry in items.items():
        if not _ID_RE.match(key) or key in RESERVED_IDS:
            reject(key, entry, "invalid_id")
        elif not isinstance(entry, dict):
            reject(key, entry, "invalid_entry")
        elif entry.get("phase") != COMPLETE:
            reject(key, entry, "open_phase")
        else:
            parent = entry.get("parent_work_item_id")
            if (parent is not None and phase_of(parent) != COMPLETE) \
                    or any(phase_of(child) != COMPLETE for child in children.get(key, ())):
                reject(key, entry, "family_open")
            elif _anchor(entry) is None:
                reject(key, entry, "no_anchor")
            else:
                last = entry.get("last_transition")
                candidates.append((last if isinstance(last, str) else "", key, entry))

    candidates.sort(key=lambda c: c[1])
    candidates.sort(key=lambda c: c[0], reverse=True)
    merged: list[tuple[str, str, dict, dict]] = []
    for last, key, entry in candidates:
        facts = _query(repo_root, _anchor(entry), run, resolve)
        if isinstance(facts, str):
            reject(key, entry, facts)
        elif facts.get("state") == "merged":
            merged.append((last, key, entry, facts))
        elif facts.get("state") == "none":
            reject(key, entry, "no_pull_request")
        else:
            reject(key, entry, f"pr_not_merged:{facts.get('state')}")

    tracked = _tracked_counts(repo_root)
    errors: dict[str, str] = {}
    own_fd = None
    if review_root_fd is None:
        own_fd = review_root_fd = _open_review_root(repo_root, errors)
    try:
        return _assemble(repo_root, repository, archive_dir, review_root_fd, tracked, errors, merged, not_finished,
                         ttl_days, now)
    finally:
        if own_fd is not None:
            os.close(own_fd)


def _removing_ids(archive_dir: Path, errors: dict[str, str]) -> list[str]:
    """The directories of `<archive-dir>/.removing/` (workflow-2.22.0): the
    leftovers of interrupted removals. A `.removing` that is a symlink, is not a
    directory or cannot be read is one `read_errors` entry."""
    path = archive_dir / ".removing"
    try:
        info = os.lstat(path)
    except OSError as exc:
        if not _absent(exc):
            _unreadable(errors, path, exc)
        return []
    if stat.S_ISLNK(info.st_mode):
        errors.setdefault(str(path), "symlink: not followed")
        return []
    if not stat.S_ISDIR(info.st_mode):
        errors.setdefault(str(path), "not a directory")
        return []
    try:
        with os.scandir(path) as entries:
            return sorted(e.name for e in entries if e.is_dir(follow_symlinks=False))
    except OSError as exc:
        _unreadable(errors, path, exc)
        return []


def _assemble(repo_root, repository, archive_dir, ai_fd, tracked, errors, merged, not_finished, ttl_days, now):
    archive_dir_linked = False
    try:
        archive_dir_linked = stat.S_ISLNK(os.lstat(archive_dir).st_mode)
    except OSError:
        pass
    if archive_dir_linked:
        errors.setdefault(str(archive_dir), "symlink: not followed")

    def listing(last, key, facts):
        pr = facts.get("pr") if isinstance(facts.get("pr"), dict) else {}
        return {"work_item_id": key, "last_transition": last or None,
                "pull_request": {"number": pr.get("number"), "url": pr.get("url")},
                "review_folder": _review_folder(ai_fd, repo_root, key, tracked, errors)}

    kept_recent = [listing(last, key, facts) for last, key, _, facts in merged[:KEEP_RECENT]]
    finished = []
    for last, key, _, facts in merged[KEEP_RECENT:]:
        record = listing(last, key, facts)
        record["archive"] = dict(_ABSENT_ARCHIVE) if archive_dir_linked \
            else _archive_fact(archive_dir / f"{key}{ARCHIVE_SUFFIX}", now, ttl_days, errors)
        finished.append(record)

    classes = {record["work_item_id"]: "finished" for record in finished}
    classes.update({record["work_item_id"]: "recent" for record in kept_recent})
    classes.update({record["work_item_id"]: "open" for record in not_finished})
    archives = []
    names: list[str] = []
    try:
        if not archive_dir_linked and stat.S_ISDIR(os.lstat(archive_dir).st_mode):
            names = os.listdir(archive_dir)
    except OSError as exc:
        if not _absent(exc):
            _unreadable(errors, archive_dir, exc)
    for name in names:
        if name.endswith(ARCHIVE_SUFFIX) and name != ARCHIVE_SUFFIX:
            fact = _archive_fact(archive_dir / name, now, ttl_days, errors)
            fact.pop("readable")
            if not fact.pop("present"):
                continue
            stem = name[:-len(ARCHIVE_SUFFIX)]
            archives.append({"name": name, "work_item_id": stem, **fact, "item": classes.get(stem, "other")})
    archives.sort(key=lambda a: a["name"])
    not_finished.sort(key=lambda r: r["work_item_id"])
    removing = [] if archive_dir_linked else _removing_ids(archive_dir, errors)

    return {
        "schema_version": SCHEMA_VERSION, "kind": "retention-report", "generated_at": _iso(now),
        "repository": repository, "archive_dir": str(archive_dir), "ttl_days": ttl_days,
        "keep_recent": KEEP_RECENT, "finished": finished, "kept_recent": kept_recent,
        "not_finished": not_finished, "archives": archives, "removing": removing, "complete": not errors,
        "read_errors": [{"path": path, "error": errors[path]} for path in sorted(errors)],
        "totals": {"finished": len(finished),
                   "finished_folder_bytes": sum(r["review_folder"]["bytes"] for r in finished),
                   "archives": len(archives), "prunable": sum(1 for a in archives if a["prunable"])},
    }


# --------------------------------------------------------------------------
# workflow-2.21.0: the archive creation (`retention-archive`)
# --------------------------------------------------------------------------

_FLAGS_DIR = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
_INDEX_NAME = "index.jsonl"
_CHUNK = 1 << 20


def _refuse(code: str, message: str, next_step: str) -> RetentionError:
    return RetentionError(code, message, next_step)


def _entry_error(path: str, kind: str) -> RetentionError:
    return _refuse("unsupported_entry", f"{path} is {kind}, which the archive does not store",
                   "move it out of the review folder by hand, or leave the item unarchived")


def _kind_of(mode: int) -> str | None:
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    if stat.S_ISLNK(mode):
        return "symlink"
    return None


def _walk_folder(ai_fd: int, item_id: str):
    """Yields `(rel, dirfd, dir_stat, others)` per directory, topdown, sorted:
    `others` is the sorted list of `(name, lstat, kind)` of everything that is
    not a real subdirectory (a symlink to a directory included, never
    descended). Everything is read through descriptors; a link is never followed."""
    def failed(exc: OSError) -> None:
        raise _refuse("unreadable", f"{exc.filename or item_id} cannot be read: {exc.strerror or exc}",
                      "grant read access to the review folder, or leave the item unarchived")

    try:
        top = os.stat(item_id, dir_fd=ai_fd, follow_symlinks=False)
    except OSError as exc:
        raise _refuse("folder_changed", f"the review folder vanished: {exc}", "run it again") from None
    if not stat.S_ISDIR(top.st_mode):
        raise _refuse("folder_changed", "the review folder is no longer a directory", "run it again")
    seen = False
    for dirpath, dirnames, filenames, dirfd in os.fwalk(item_id, dir_fd=ai_fd, follow_symlinks=False,
                                                         onerror=failed):
        seen = True
        rel = dirpath[len(item_id) + 1:]
        others, real_dirs = [], []
        for name in sorted(set(dirnames) | set(filenames)):
            shown = f"{rel}/{name}" if rel else name
            try:
                info = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
            except OSError:
                raise _refuse("folder_changed", f"{shown} vanished while the folder was read",
                              "run it again when nothing writes to the review folder") from None
            kind = _kind_of(info.st_mode)
            if kind is None:
                raise _entry_error(shown, "a socket, FIFO or device")
            if kind == "file" and info.st_nlink > 1:
                raise _entry_error(shown, "a file with more than one hard link")
            if kind == "dir":
                real_dirs.append(name)
            else:
                others.append((name, info, kind))
        dirnames[:] = real_dirs
        yield rel, dirfd, os.fstat(dirfd), others
    if not seen:
        raise _refuse("folder_changed", "the review folder is no longer a directory", "run it again")


def _record(path: str, kind: str, info: os.stat_result, target: str | None = None, digest: str | None = None) -> dict:
    return {"path": path, "type": kind, "mode": stat.S_IMODE(info.st_mode),
            "size": info.st_size if kind != "dir" else 0, "mtime_ns": info.st_mtime_ns,
            "ctime_ns": info.st_ctime_ns, "target": target, "sha256": digest}


def _scan(ai_fd: int, item_id: str) -> list[dict]:
    """The stat-only walk: one record per entry, root included (path '')."""
    records = []
    for rel, dirfd, dir_info, others in _walk_folder(ai_fd, item_id):
        records.append(_record(rel, "dir", dir_info))
        for name, info, kind in others:
            target = os.readlink(name, dir_fd=dirfd) if kind == "symlink" else None
            records.append(_record(f"{rel}/{name}" if rel else name, kind, info, target))
    return records


def _open_file(name: str, dirfd: int, shown: str) -> int:
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    noatime = getattr(os, "O_NOATIME", 0)
    try:
        try:
            return os.open(name, flags | noatime, dir_fd=dirfd)
        except PermissionError:
            if not noatime:
                raise
            return os.open(name, flags, dir_fd=dirfd)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EPERM):
            raise _refuse("unreadable", f"{shown} cannot be read ({exc.strerror or exc})",
                          "grant read access to the review folder; the engine does not archive from a partial view"
                          ) from None
        raise _refuse("folder_changed", f"{shown} changed while the folder was read ({exc.strerror or exc})",
                      "run it again when nothing writes to the review folder") from None


class _Hashing:
    """The one read of a file: its bytes feed the hash and the tar member."""

    def __init__(self, fd: int, size: int):
        self.fd, self.size, self.count, self.digest = fd, size, 0, hashlib.sha256()
        self.error: OSError | None = None

    def read(self, n: int = -1) -> bytes:
        n = min(_CHUNK, self.size - self.count) if n is None or n < 0 else min(n, self.size - self.count)
        try:
            data = os.read(self.fd, n) if n > 0 else b""
        except OSError as exc:
            self.error = exc
            raise
        self.count += len(data)
        self.digest.update(data)
        return data


def _tarinfo(name: str, kind: str, info: os.stat_result, target: str | None) -> tarfile.TarInfo:
    ti = tarfile.TarInfo(name)
    ti.mode = stat.S_IMODE(info.st_mode)
    ti.mtime = info.st_mtime_ns // 1_000_000_000
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    if kind == "dir":
        ti.type = tarfile.DIRTYPE
    elif kind == "symlink":
        ti.type, ti.linkname = tarfile.SYMTYPE, target
    else:
        ti.type, ti.size = tarfile.REGTYPE, info.st_size
    return ti


def _pack(ai_fd: int, item_id: str, tar: tarfile.TarFile) -> list[dict]:
    """Writes the folder into `tar` and returns `W1`: the record of every entry,
    taken from the descriptor the bytes came from."""
    records = []
    for rel, dirfd, dir_info, others in _walk_folder(ai_fd, item_id):
        name = f"{item_id}/{rel}" if rel else item_id
        tar.addfile(_tarinfo(name, "dir", dir_info, None))
        records.append(_record(rel, "dir", dir_info))
        for entry, info, kind in others:
            shown = f"{rel}/{entry}" if rel else entry
            member = f"{item_id}/{shown}"
            if kind == "symlink":
                target = os.readlink(entry, dir_fd=dirfd)
                tar.addfile(_tarinfo(member, kind, info, target))
                records.append(_record(shown, kind, info, target))
                continue
            fd = _open_file(entry, dirfd, shown)
            try:
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink > 1 or \
                        (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != \
                        (info.st_size, info.st_mtime_ns, info.st_ctime_ns):
                    raise _refuse("folder_changed", f"{shown} changed after the folder was listed",
                                  "run it again when nothing writes to the review folder")
                source = _Hashing(fd, opened.st_size)
                try:
                    tar.addfile(_tarinfo(member, kind, opened, None), source)
                except BrokenPipeError:
                    raise
                except OSError:
                    if source.error is not None:
                        raise _refuse("unreadable", f"{shown} cannot be read ({source.error.strerror or source.error})",
                                      "repair it; the engine does not archive from a partial view") from None
                    if source.count == opened.st_size:
                        raise
                if source.count != opened.st_size or os.read(fd, 1):
                    raise _refuse("folder_changed", f"{shown} changed size while it was read",
                                  "run it again when nothing writes to the review folder")
                records.append(_record(shown, kind, opened, None, source.digest.hexdigest()))
            finally:
                os.close(fd)
    return records


def _expected_name(item_id: str, record: dict) -> str:
    return f"{item_id}/{record['path']}" if record["path"] else item_id


def _comparable(record: dict) -> tuple:
    if record["type"] == "dir":
        return (record["path"], "dir", record["mtime_ns"], record["ctime_ns"])
    return (record["path"], record["type"], record["size"], record["mtime_ns"], record["ctime_ns"])


def _reap(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    for pipe in (proc.stdin, proc.stdout):
        try:
            if pipe is not None:
                pipe.close()
        except OSError:
            pass
    proc.wait()
    if proc.stderr is not None:
        proc.stderr.close()


def _stderr_of(proc: subprocess.Popen) -> str:
    try:
        return proc.stderr.read().decode("utf-8", "replace").strip()[:200] if proc.stderr else ""
    except OSError:
        return ""


def _pack_to(zstd: str, tmp_fd: int, ai_fd: int, item_id: str) -> list[dict]:
    try:
        proc = subprocess.Popen([zstd, "-q", "-T1", "-c"], stdin=subprocess.PIPE, stdout=tmp_fd,
                                stderr=subprocess.PIPE, close_fds=True)
    except OSError as exc:
        raise _refuse("pack_failed", f"zstd could not start: {exc}", "check the zstd program") from None
    try:
        try:
            with tarfile.open(fileobj=proc.stdin, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                records = _pack(ai_fd, item_id, tar)
            proc.stdin.close()
        except RetentionError:
            raise
        except (OSError, tarfile.TarError, ValueError) as exc:
            proc.kill()
            raise _refuse("pack_failed", f"packing failed: {exc}; {_stderr_of_dead(proc)}",
                          "check free space and the zstd program") from None
        if proc.wait() != 0:
            raise _refuse("pack_failed", f"zstd exited {proc.returncode}: {_stderr_of(proc)}",
                          "check free space and the zstd program")
        return records
    finally:
        _reap(proc)


def _stderr_of_dead(proc: subprocess.Popen) -> str:
    proc.wait()
    return _stderr_of(proc)


def _verify(zstd: str, adir_fd: int, tmp_name: str, item_id: str, records: list[dict]) -> None:
    def bad(why: str) -> RetentionError:
        return _refuse("verify_failed", f"the archive does not match the folder: {why}",
                       "nothing was placed; check the zstd program and the free space, then run it again")

    try:
        rfd = os.open(tmp_name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=adir_fd)
    except OSError as exc:
        raise bad(f"the temporary cannot be read back: {exc}") from None
    try:
        proc = subprocess.Popen([zstd, "-dc"], stdin=rfd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                close_fds=True)
    except OSError as exc:
        raise bad(f"zstd could not start: {exc}") from None
    finally:
        os.close(rfd)
    try:
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                members = iter(tar)
                for record in records:
                    member = next(members, None)
                    if member is None:
                        raise bad(f"{_expected_name(item_id, record)} is missing")
                    if member.name != _expected_name(item_id, record):
                        raise bad(f"found {member.name}, expected {_expected_name(item_id, record)}")
                    if record["type"] == "dir":
                        ok = member.isdir()
                    elif record["type"] == "symlink":
                        ok = member.issym() and member.linkname == record["target"]
                    else:
                        ok = member.isreg() and member.size == record["size"]
                    if not ok:
                        raise bad(f"{member.name} differs in type, size or target")
                    if record["type"] == "file":
                        digest, count = hashlib.sha256(), 0
                        source = tar.extractfile(member)
                        while True:
                            data = source.read(_CHUNK)
                            if not data:
                                break
                            digest.update(data)
                            count += len(data)
                        if count != record["size"] or digest.hexdigest() != record["sha256"]:
                            raise bad(f"{member.name} differs in content")
                extra = next(members, None)
                if extra is not None:
                    raise bad(f"{extra.name} is not in the folder")
            while proc.stdout.read(_CHUNK):
                pass
        except RetentionError:
            raise
        except (OSError, EOFError, tarfile.TarError, ValueError) as exc:
            raise bad(f"the archive cannot be read: {exc}") from None
        if proc.wait() != 0:
            raise bad(f"zstd exited {proc.returncode}: {_stderr_of(proc)}")
    finally:
        _reap(proc)


# --- the archive side -------------------------------------------------------

def _chain_parts(path: str) -> list[str]:
    return [part for part in path.split(os.sep) if part]


def _unsafe_component(path: str, exc: OSError) -> RetentionError:
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        return _refuse("archive_dir_unsafe", f"{path} is a symlink or not a directory",
                       f"replace {path} by a real directory, or choose another --archive-root")
    return _refuse("unreadable", f"{path} cannot be opened for reading: {exc.strerror or exc}",
                   f"grant read access to {path} for this user, or choose another --archive-root")


def _walk_chain(parts: list[str], fds: list[int], created: list[tuple[int, str]] | None) -> bool:
    """Opens `/` and each component, one at a time, without following a link.
    Appends the descriptors to `fds`. With `created` (apply) a missing component
    is made and recorded; without it (the dry run) the walk stops at the first
    missing one and returns False."""
    fds.append(os.open(os.sep, _FLAGS_DIR))
    shown = ""
    for part in parts:
        shown = f"{shown}{os.sep}{part}"
        parent = fds[-1]
        try:
            fds.append(os.open(part, _FLAGS_DIR, dir_fd=parent))
            continue
        except FileNotFoundError:
            if created is None:
                return False
        except OSError as exc:
            raise _unsafe_component(shown, exc) from None
        try:
            os.mkdir(part, 0o700, dir_fd=parent)
            created.append((parent, part))
        except FileExistsError:
            pass
        except OSError as exc:
            raise _refuse("place_failed", f"{shown} cannot be created: {exc.strerror or exc}",
                          "check the permissions and free space of the archive root") from None
        try:
            fds.append(os.open(part, _FLAGS_DIR, dir_fd=parent))
        except OSError as exc:
            raise _unsafe_component(shown, exc) from None
    return True


def _lstat_raw(name: str, adir_fd: int) -> os.stat_result | None:
    """The `lstat` of an entry; None when it is absent, any other `OSError` goes through."""
    try:
        return os.stat(name, dir_fd=adir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _lstat_at(name: str, adir_fd: int, *, shown: str, code: str = "unreadable",
              after: str = "nothing was written or removed") -> os.stat_result | None:
    """`_lstat_raw`, but a stat error that is not "not found" is a coded refusal
    naming `shown`, the full path of the entry; it never reads as "absent"."""
    try:
        return _lstat_raw(name, adir_fd)
    except OSError as exc:
        raise _refuse(code, f"{shown} cannot be examined: {errno.errorcode.get(exc.errno, exc.errno)} "
                      f"{exc.strerror or exc}",
                      f"check the permissions of {shown} and of the directory that holds it; {after}") from None


def _index_unsafe(why: str) -> RetentionError:
    return _refuse("index_unsafe", f"{_INDEX_NAME} {why}",
                   f"replace {_INDEX_NAME} by an ordinary file with one link, by hand; the engine never follows or "
                   "rewrites it")


def _index_ids(fd: int, size: int) -> set[str]:
    """The work item ids of a valid `index.jsonl`; `index_invalid` otherwise."""
    data = b""
    while len(data) < size:
        chunk = os.pread(fd, size - len(data), len(data))
        if not chunk:
            break
        data += chunk
    if not data:
        return set()
    if not data.endswith(b"\n"):
        raise _refuse("index_invalid", f"{_INDEX_NAME} does not end in a newline (a half line)",
                      "repair or remove its last line by hand, then run it again")
    ids = set()
    for number, line in enumerate(data.split(b"\n")[:-1], 1):
        try:
            record = json.loads(line)
        except ValueError:
            record = None
        if not isinstance(record, dict):
            raise _refuse("index_invalid", f"line {number} of {_INDEX_NAME} is not a JSON object",
                          "repair or remove that line by hand, then run it again")
        if isinstance(record.get("work_item_id"), str):
            ids.add(record["work_item_id"])
    return ids


def _open_index(adir_fd: int, *, write: bool, create: bool = False) -> tuple[int | None, bool]:
    """The index opened without following a link, checked to be an ordinary file
    with one link before anything is read or written. `(None, False)` when it is
    absent and not being created."""
    flags = os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    flags |= (os.O_RDWR | os.O_APPEND) if write else os.O_RDONLY
    created = False
    try:
        try:
            fd = os.open(_INDEX_NAME, flags, dir_fd=adir_fd)
        except FileNotFoundError:
            if not create:
                return None, False
            fd = os.open(_INDEX_NAME, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=adir_fd)
            created = True
    except FileExistsError:
        raise _index_unsafe("appeared while the archive was being placed") from None
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.EISDIR, errno.ENXIO):
            raise _index_unsafe("is a symlink, a directory or not an ordinary file") from None
        if write:
            raise _refuse("index_failed", f"{_INDEX_NAME} cannot be opened for appending: {exc.strerror or exc}",
                          "check its permissions and the free space, then run it again") from None
        raise _refuse("unreadable", f"{_INDEX_NAME} cannot be read: {exc.strerror or exc}",
                      "grant read access to it") from None
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise _index_unsafe("is not an ordinary file with exactly one link")
    return fd, created


def _judge_archive_side(adir_fd: int | None, item_id: str, resolved: str, *, check_partial: bool,
                        after: str = "nothing was written or removed") -> None:
    """D-Selection step 7, after the walk: the index's integrity first, then the
    archive, then the index line, then the temporary."""
    if adir_fd is None:
        return
    ids: set[str] = set()
    fd, _ = _open_index(adir_fd, write=False)
    if fd is not None:
        try:
            ids = _index_ids(fd, os.fstat(fd).st_size)
        finally:
            os.close(fd)
    final = f"{item_id}{ARCHIVE_SUFFIX}"
    if _lstat_at(final, adir_fd, shown=os.path.join(resolved, final), after=after) is not None:
        if item_id in ids:
            raise _refuse("already_archived", f"{final} exists and {_INDEX_NAME} has its line",
                          "the item is archived; nothing to do")
        raise _refuse("already_archived", f"{final} exists but {_INDEX_NAME} has no line for {item_id}",
                      f"the index has no line for {item_id}, so a run was killed after placing the archive; the "
                      f"folder is intact: compare it with the archive (`zstd -dc {final} | tar -tvf -`), then remove "
                      f"{final} by hand (and a `.{item_id}.partial` beside it) and run it again")
    if item_id in ids:
        raise _refuse("already_indexed", f"{_INDEX_NAME} has a line for {item_id} but there is no {final}",
                      f"the archive was pruned or removed by hand; the engine never rewrites {_INDEX_NAME}, "
                      "so the owner decides")
    partial = f".{item_id}.partial"
    if check_partial and _lstat_at(partial, adir_fd, shown=os.path.join(resolved, partial)) is not None:
        raise _refuse("partial_present", f".{item_id}.partial exists in the archive directory",
                      "confirm no other `archive` run is active, remove that file by hand and run it again")


def _git_tracked(repo_root: Path, item_id: str) -> bool:
    try:
        result = subprocess.run(["git", "ls-files", "-z", "--", f".ai-review/{item_id}"], cwd=repo_root,
                                capture_output=True)
    except OSError as exc:
        raise _refuse("unreadable", f"git could not run: {exc}", "install Git") from None
    if result.returncode != 0:
        raise _refuse("unreadable", "git ls-files failed", "run it inside a Git repository")
    return bool(result.stdout.strip(b"\0"))


def _underneath(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _fsync(fd: int, what: str, code: str = "place_failed", where: str = "nothing was kept") -> None:
    try:
        os.fsync(fd)
    except OSError as exc:
        raise _refuse(code, f"{what} could not be made durable: {exc.strerror or exc}",
                      f"check the file system and free space; {where}") from None


def _remove_quietly(action, problems: list[str], what: str) -> None:
    try:
        action()
    except FileNotFoundError:
        pass
    except OSError as exc:
        problems.append(f"{what}: {exc.strerror or exc}")


_DONE = {"archive": "archived", "remove": "removed"}
_ABLE = {"archive": "archivable", "remove": "removable"}


def _check_id(item_id, verb: str, next_step: str = "pass the id exactly as the report lists it") -> None:
    if not isinstance(item_id, str) or not _ID_RE.match(item_id) or item_id in RESERVED_IDS:
        raise _refuse("invalid_id", f"{item_id!r} is not a work item id the {verb} accepts", next_step)


def _select_destination(root, report: dict, archive_base: str, fds: list[int], verb: str):
    """The destination checks every subcommand needs: the directory is outside
    the checkout, is not a symlink or a non-directory, has no `read_errors`
    entry beneath it, and its chain of components opens without following a
    link. Returns `(resolved, adir_fd)`; `adir_fd` is None when absent."""
    repo_name = report["repository"].split("/", 1)[1]
    unresolved = os.path.join(archive_base, repo_name)
    resolved = os.path.realpath(unresolved)
    if _underneath(resolved, str(root)):
        raise _refuse("archive_inside_repository", f"the archive directory {resolved} is inside the repository",
                      "choose an --archive-root outside the checkout")
    info = _lstat_at_path(unresolved)
    if info is not None and not stat.S_ISDIR(info.st_mode):
        raise _refuse("archive_dir_unsafe", f"{unresolved} is a symlink or not a directory",
                      f"replace {unresolved} by a real directory, or choose another --archive-root")
    for entry in report["read_errors"]:
        if _underneath(entry["path"], unresolved):
            raise _refuse("unreadable", f"{entry['path']} cannot be read ({entry['error']})",
                          f"repair it; the engine does not {verb} from a partial view")
    complete = _walk_chain(_chain_parts(resolved), fds, None)
    return resolved, (fds[-1] if complete else None)


def _select_finished(repo_root, item_id: str, archive_root, now, run, resolve, fds: list[int], verb: str,
                     needs: tuple = ()):
    """D-Selection 1 to 5, shared by `archive` and `remove`: the id, the platform,
    the anchor, the report, the finished record and the destination up to the
    archive directory. The caller owns every descriptor: each one opened here,
    `.ai-review` included, is appended to `fds` before anything can refuse, and
    the caller's `finally` closes them. Returns `(root, ai_fd, report, record,
    resolved, adir_fd)`; `ai_fd` and `adir_fd` are None when absent."""
    _check_id(item_id, verb)
    supported = {fn.__name__ for fn in os.supports_dir_fd}
    if not _platform_ok() or not set(needs) <= supported:
        raise _refuse("path_unsafe", "this platform has no descriptor-relative file access",
                      "run it on Linux or macOS")
    root = Path(os.path.realpath(repo_root))
    errors: dict[str, str] = {}
    ai_fd = _open_review_root(root, errors)
    if ai_fd is not None:
        fds.append(ai_fd)
    if ai_fd is None and errors:
        unsafe = any(why in ("symlink: not followed", "not a directory") for why in errors.values())
        if not unsafe:
            raise _refuse("unreadable", f"{root / '.ai-review'} cannot be read", "grant read access to it")
        raise _refuse("path_unsafe", f"{root / '.ai-review'} is not a real directory",
                      "replace it by a real directory; the engine reads nothing through a link")
    archive_base = os.path.abspath(os.path.expanduser(str(archive_root)))
    report = build_report(root, archive_root=archive_base, now=now, run=run, resolve=resolve,
                          review_root_fd=ai_fd)
    record = next((r for r in report["finished"] if r["work_item_id"] == item_id), None)
    if record is None:
        reasons = {r["work_item_id"]: r["reason"] for r in report["not_finished"]}
        if item_id in reasons:
            raise _refuse(f"not_finished:{reasons[item_id]}", f"{item_id} is not finished ({reasons[item_id]})",
                          f"only an item the report lists as finished can be {_DONE[verb]}")
        if any(r["work_item_id"] == item_id for r in report["kept_recent"]):
            raise _refuse("kept_recent", f"{item_id} is one of the {KEEP_RECENT} most recent merged items",
                          f"it becomes {_ABLE[verb]} when newer items are merged")
        raise _refuse("unknown_item", f"{item_id} is not a work item of this repository",
                      "pass an id the report lists")
    resolved, adir_fd = _select_destination(root, report, archive_base, fds, verb)
    return root, ai_fd, report, record, resolved, adir_fd


def archive_item(repo_root, item_id: str, *, apply: bool = False, archive_root=DEFAULT_ARCHIVE_ROOT,
                 now: datetime.datetime, run=None, resolve=None, zstd: str | None = None) -> dict:
    """Packs the untracked review folder of a finished item into a verified
    tarball outside the repository. Dry run unless `apply`. Never writes to the
    folder or the repository."""
    fds: list[int] = []
    created: list[tuple[int, str]] = []
    try:
        root, ai_fd, report, record, resolved, adir_fd = _select_finished(
            repo_root, item_id, archive_root, now, run, resolve, fds, "archive")
        _judge_archive_side(adir_fd, item_id, resolved, check_partial=True)
        folder = root / ".ai-review" / item_id
        if ai_fd is None or not _is_real_dir(ai_fd, item_id):
            raise _refuse("review_folder_absent", f"{folder} is not a real directory",
                          "there is nothing to archive")
        if _git_tracked(root, item_id):
            raise _refuse("tracked_files", f"Git tracks an entry at or under .ai-review/{item_id}",
                          "this command archives the untracked folder only")
        for entry in report["read_errors"]:
            if _underneath(entry["path"], str(folder)):
                raise _refuse("unreadable", f"{entry['path']} cannot be read ({entry['error']})",
                              "repair it; the engine does not archive from a partial view")
        entries = _scan(ai_fd, item_id)
        program = zstd or shutil.which("zstd")
        if not program:
            raise _refuse("zstd_missing", "the zstd program was not found on PATH",
                          "install zstd so that it is on the PATH")
        files = [e for e in entries if e["type"] == "file"]
        result = {
            "schema_version": SCHEMA_VERSION, "kind": "retention-archive",
            "mode": "apply" if apply else "dry-run", "generated_at": _iso(now),
            "repository": report["repository"], "work_item_id": item_id, "pull_request": record["pull_request"],
            "review_folder": {"path": str(folder), "files": len(files), "bytes": sum(e["size"] for e in files),
                              "entries": [{"path": e["path"], "type": e["type"], "bytes": e["size"]}
                                          for e in sorted(entries, key=lambda e: e["path"]) if e["path"]]},
            "target": {"archive": os.path.join(resolved, f"{item_id}{ARCHIVE_SUFFIX}"),
                       "temporary": os.path.join(resolved, f".{item_id}.partial"),
                       "index": os.path.join(resolved, _INDEX_NAME)},
            "zstd": program, "result": {"archived": False},
        }
        if apply:
            result["result"], packed = _apply(ai_fd, item_id, program, resolved, fds, created, record, now)
            packed_files = [r for r in packed if r["type"] == "file"]
            result["review_folder"].update(
                files=len(packed_files), bytes=sum(r["size"] for r in packed_files),
                entries=[{"path": r["path"], "type": r["type"], "bytes": r["size"]}
                         for r in sorted(packed, key=lambda r: r["path"]) if r["path"]])
        return result
    finally:
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError:
                pass


def _lstat_at_path(path: str) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise _unsafe_component(path, exc) from None


def _is_real_dir(ai_fd: int, item_id: str) -> bool:
    try:
        return stat.S_ISDIR(os.stat(item_id, dir_fd=ai_fd, follow_symlinks=False).st_mode)
    except OSError:
        return False


def _apply(ai_fd, item_id, program, resolved, fds, created, record, now) -> tuple[dict, list[dict]]:
    """D-Place: durable hierarchy, pack and verify into the temporary, lock,
    link, append, fsync, release. Any failure undoes what this run created."""
    final = f"{item_id}{ARCHIVE_SUFFIX}"
    tmp_name = f".{item_id}.partial"
    state = {"tmp": False, "placed": False, "index_size": None, "index_created": False, "index_fd": None,
             "locked": False}
    adir_fd = None
    try:
        # 0. the hierarchy, whoever made it, made durable from the root down
        fds_apply: list[int] = []
        try:
            _walk_chain(_chain_parts(resolved), fds_apply, created)
        finally:
            fds.extend(fds_apply)
        adir_fd = fds_apply[-1]
        for index in range(1, len(fds_apply)):
            _fsync(fds_apply[index], f"the directory {resolved}")
            _fsync(fds_apply[index - 1], "its parent directory")
        # 1. pack and verify into the temporary
        try:
            tmp_fd = os.open(tmp_name,
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                             0o600, dir_fd=adir_fd)
        except FileExistsError:
            raise _refuse("partial_present", f"{tmp_name} appeared in the archive directory",
                          "confirm no other `archive` run is active, remove that file by hand and run it again"
                          ) from None
        except OSError as exc:
            raise _refuse("place_failed", f"{tmp_name} cannot be created: {exc.strerror or exc}",
                          "check the permissions and free space of the archive directory") from None
        state["tmp"] = True
        try:
            records = _pack_to(program, tmp_fd, ai_fd, item_id)
            _verify(program, adir_fd, tmp_name, item_id, records)
            if [_comparable(r) for r in _scan(ai_fd, item_id)] != [_comparable(r) for r in records]:
                raise _refuse("folder_changed", "the review folder changed while it was packed",
                              "nothing was placed; run it again when nothing writes to the review folder")
            size = os.fstat(tmp_fd).st_size
            try:
                fcntl.flock(adir_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise _refuse("archive_busy", "another `archive` run holds this repository's archive directory",
                              "wait for it to finish, then run it again") from None
            state["locked"] = True
            _judge_archive_side(adir_fd, item_id, resolved, check_partial=False,
                                after="nothing was archived; the temporary and the directories this run created "
                                      "were removed")
            _fsync(tmp_fd, tmp_name)
        finally:
            os.close(tmp_fd)
        # 2. place without clobbering
        try:
            os.link(tmp_name, final, src_dir_fd=adir_fd, dst_dir_fd=adir_fd)
        except FileExistsError:
            raise _refuse("already_archived", f"{final} appeared while the archive was being placed",
                          "the item is archived by another run; nothing to do") from None
        except OSError as exc:
            raise _refuse("place_failed", f"{final} cannot be linked into place: {exc.strerror or exc}",
                          "the file system needs hard links; nothing was kept") from None
        state["placed"] = True
        try:
            os.unlink(tmp_name, dir_fd=adir_fd)
        except OSError as exc:
            raise _refuse("place_failed", f"{tmp_name} cannot be removed: {exc.strerror or exc}",
                          "nothing was kept") from None
        state["tmp"] = False
        _fsync(adir_fd, "the archive directory")
        # 3. the index line, durable
        line_obj = {"archived_at": _iso(now), "bytes": size, "files": sum(1 for r in records if r["type"] == "file"),
                    "pull_request": record["pull_request"], "work_item_id": item_id}
        line = (json.dumps(line_obj, sort_keys=True) + "\n").encode("utf-8")
        fd, created_index = _open_index(adir_fd, write=True, create=True)
        state["index_fd"], state["index_created"] = fd, created_index
        before = os.fstat(fd).st_size
        state["index_size"] = before
        _index_ids(fd, before)
        view = memoryview(line)
        try:
            while view:
                view = view[os.write(fd, view):]
        except OSError as exc:
            raise _refuse("index_failed", f"{_INDEX_NAME} could not be appended to: {exc.strerror or exc}",
                          "check the free space, then run it again; nothing was kept") from None
        _fsync(fd, _INDEX_NAME, "index_failed")
        if created_index:
            _fsync(adir_fd, "the archive directory", "index_failed")
        # 4. release; only now is the item archived
        fcntl.flock(adir_fd, fcntl.LOCK_UN)
        state["locked"] = False
        return {"archived": True, "bytes": size, "index_line": line_obj}, records
    except BaseException:
        _undo(adir_fd, item_id, final, tmp_name, state, created)
        raise
    finally:
        if state["index_fd"] is not None:
            os.close(state["index_fd"])


def _undo(adir_fd, item_id, final, tmp_name, state, created) -> None:
    problems: list[str] = []
    if adir_fd is not None:
        if state["index_fd"] is not None and state["index_size"] is not None:
            def restore():
                if state["index_created"]:
                    os.unlink(_INDEX_NAME, dir_fd=adir_fd)
                    os.fsync(adir_fd)
                else:
                    os.ftruncate(state["index_fd"], state["index_size"])
                    os.fsync(state["index_fd"])
            _remove_quietly(restore, problems, _INDEX_NAME)
        if state["placed"]:
            def drop():
                os.unlink(final, dir_fd=adir_fd)
                os.fsync(adir_fd)
            _remove_quietly(drop, problems, final)
        if state["tmp"]:
            _remove_quietly(lambda: os.unlink(tmp_name, dir_fd=adir_fd), problems, tmp_name)
    for parent, name in reversed(created):
        def rmdir(parent=parent, name=name):
            try:
                os.rmdir(name, dir_fd=parent)
            except OSError as exc:
                if exc.errno == errno.ENOTEMPTY:
                    return
                raise
            os.fsync(parent)
        _remove_quietly(rmdir, problems, name)
    if adir_fd is not None and state["locked"]:
        _remove_quietly(lambda: fcntl.flock(adir_fd, fcntl.LOCK_UN), problems, "the lock")
    if problems:
        sys.stderr.write("warning: the undo could not remove: " + "; ".join(problems) + "\n")


# --------------------------------------------------------------------------
# workflow-2.22.0: the removal (`retention-removal`)
# --------------------------------------------------------------------------

_REMOVING = ".removing"
_rename = os.rename


def _same_device(fd_a: int, fd_b: int) -> bool:
    return os.fstat(fd_a).st_dev == os.fstat(fd_b).st_dev


def _bad_archive(name: str, why: str, item_id: str) -> RetentionError:
    return _refuse("unreadable", f"the archive {name} cannot be read: {why}",
                   f"repair the archive or its access, then run `remove {item_id} --apply` again")


def _archive_table(zstd: str, adir_fd: int, name: str, item_id: str) -> dict:
    """D-Compare: the archive read into `{path: (type, size, target, sha256)}`,
    the item's own entry as the path ''. The bytes of each file are hashed as
    they stream; the memory is the table."""
    try:
        rfd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), dir_fd=adir_fd)
    except OSError as exc:
        raise _bad_archive(name, str(exc.strerror or exc), item_id) from None
    try:
        proc = subprocess.Popen([zstd, "-dc"], stdin=rfd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                close_fds=True)
    except OSError as exc:
        raise _bad_archive(name, f"zstd could not start: {exc}", item_id) from None
    finally:
        os.close(rfd)
    table: dict[str, tuple] = {}
    try:
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as tar:
                for member in tar:
                    if member.name == item_id:
                        rel = ""
                    elif member.name.startswith(item_id + "/"):
                        rel = member.name[len(item_id) + 1:]
                    else:
                        raise _bad_archive(name, f"{member.name} is outside {item_id}", item_id)
                    if rel in table:
                        raise _bad_archive(name, f"{member.name} is listed twice", item_id)
                    if member.isdir():
                        table[rel] = ("dir", 0, None, None)
                    elif member.issym():
                        table[rel] = ("symlink", len(member.linkname.encode("utf-8", "surrogateescape")),
                                      member.linkname, None)
                    elif member.isreg():
                        digest, count = hashlib.sha256(), 0
                        source = tar.extractfile(member)
                        while True:
                            data = source.read(_CHUNK)
                            if not data:
                                break
                            digest.update(data)
                            count += len(data)
                        table[rel] = ("file", count, None, digest.hexdigest())
                    else:
                        raise _bad_archive(name, f"{member.name} is not a file, directory or symlink", item_id)
            while proc.stdout.read(_CHUNK):
                pass
        except RetentionError:
            raise
        except (OSError, EOFError, tarfile.TarError, ValueError) as exc:
            raise _bad_archive(name, str(exc), item_id) from None
        if proc.wait() != 0:
            raise _bad_archive(name, f"zstd exited {proc.returncode}: {_stderr_of(proc)}", item_id)
    finally:
        _reap(proc)
    return table


def _hash_folder(dir_fd: int, item_id: str) -> list[dict]:
    """D-Compare: the `_record`s of the folder `item_id` in `dir_fd`, with a
    SHA-256 for every file (one read each; the same size and link checks as the
    pack)."""
    records = []
    for rel, dirfd, dir_info, others in _walk_folder(dir_fd, item_id):
        records.append(_record(rel, "dir", dir_info))
        for name, info, kind in others:
            shown = f"{rel}/{name}" if rel else name
            if kind == "symlink":
                records.append(_record(shown, kind, info, os.readlink(name, dir_fd=dirfd)))
                continue
            fd = _open_file(name, dirfd, shown)
            try:
                opened = os.fstat(fd)
                if not stat.S_ISREG(opened.st_mode) or opened.st_nlink > 1 or \
                        (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != \
                        (info.st_size, info.st_mtime_ns, info.st_ctime_ns):
                    raise _refuse("folder_changed", f"{shown} changed after the folder was listed",
                                  "run it again when nothing writes to the review folder")
                digest, count = hashlib.sha256(), 0
                try:
                    while count < opened.st_size:
                        data = os.read(fd, min(_CHUNK, opened.st_size - count))
                        if not data:
                            break
                        digest.update(data)
                        count += len(data)
                    extra = os.read(fd, 1)
                except OSError as exc:
                    raise _refuse("unreadable", f"{shown} cannot be read ({exc.strerror or exc})",
                                  "repair it; the engine does not remove from a partial view") from None
                if count != opened.st_size or extra:
                    raise _refuse("folder_changed", f"{shown} changed size while it was read",
                                  "run it again when nothing writes to the review folder")
                records.append(_record(shown, kind, opened, None, digest.hexdigest()))
            finally:
                os.close(fd)
    return records


def _differences(table: dict, records: list[dict], allow_missing: bool) -> tuple[list[tuple[str, str]], int]:
    """D-Compare: `([(path, why)], missing)`. An entry of the folder that is not in
    the archive, or differs in type, size, target or hash, is a difference; an
    archived entry the folder lacks is one too unless `allow_missing`, in which
    case it is counted in `missing`."""
    diffs: list[tuple[str, str]] = []
    seen = set()
    for record in records:
        seen.add(record["path"])
        entry = table.get(record["path"])
        if entry is None:
            diffs.append((record["path"], "not in archive"))
            continue
        kind, size, target, digest = entry
        if record["type"] != kind or record["target"] != target or \
                (kind == "file" and (record["size"] != size or record["sha256"] != digest)):
            diffs.append((record["path"], "differs"))
    missing = 0
    for path in table:
        if path not in seen:
            if allow_missing:
                missing += 1
            else:
                diffs.append((path, "missing from folder"))
    diffs.sort()
    return diffs, missing


def _incomplete_note(missing: int, archive: str) -> str:
    return (f"{missing} archived entries are missing because an earlier run already deleted them; they are in "
            f"{archive}; extract them with `zstd -dc {archive} | tar -x -C <scratch>` and merge by hand")


def _mismatch(diffs: list[tuple[str, str]], missing: int, archive: str) -> RetentionError:
    shown = "; ".join(f"{path or '.'} ({why})" for path, why in diffs[:10])
    more = f" (the first 10 of {len(diffs)})" if len(diffs) > 10 else ""
    step = ("the differing entries exist only in this folder, keep them (copy them out first); a later `remove` "
            "stays refused while they differ")
    if missing:
        step += "; " + _incomplete_note(missing, archive)
    return _refuse("archive_mismatch",
                   f"the review folder differs from its archive in {len(diffs)} entr"
                   f"{'y' if len(diffs) == 1 else 'ies'}{more}: {shown}", step)


def _stopped(code: str, why: str, deleted: int, place: str, item_id: str) -> RetentionError:
    extra = "; check the permissions of `.removing/%s`" % item_id if code == "remove_failed" else ""
    return _refuse(code, f"{why}; stopped after deleting {deleted} entries, each proven archived; the rest is in "
                         f"{place}", f"run `remove {item_id} --apply` again{extra}")


def _remove_tree(removing_fd: int, item_id: str, records: list[dict], *, place: str) -> int:
    """D-Delete: the renamed folder deleted entry by entry, each checked against
    the record verified a moment before; a new or changed entry stops the walk
    and is kept. Returns the number of entries deleted."""
    known = {r["path"]: r for r in records}
    deleted = 0
    for dirpath, dirnames, filenames, dirfd in os.fwalk(item_id, topdown=False, dir_fd=removing_fd,
                                                         follow_symlinks=False):
        rel = dirpath[len(item_id) + 1:]
        for name in sorted(set(dirnames) | set(filenames)):
            shown = f"{rel}/{name}" if rel else name
            try:
                info = os.stat(name, dir_fd=dirfd, follow_symlinks=False)
            except OSError as exc:
                raise _stopped("folder_changed", f"{shown} vanished ({exc.strerror or exc})", deleted, place,
                               item_id) from None
            if stat.S_ISDIR(info.st_mode):
                if shown not in known or known[shown]["type"] != "dir":
                    raise _stopped("folder_changed", f"{shown} is a directory that was not verified", deleted,
                                   place, item_id)
                try:
                    os.rmdir(name, dir_fd=dirfd)
                except OSError as exc:
                    code = "folder_changed" if exc.errno == errno.ENOTEMPTY else "remove_failed"
                    raise _stopped(code, f"{shown} cannot be removed ({exc.strerror or exc})", deleted, place,
                                   item_id) from None
                deleted += 1
                continue
            kind = _kind_of(info.st_mode)
            expected = known.get(shown)
            if kind is None or expected is None or _comparable(_record(shown, kind, info, expected["target"])) \
                    != _comparable(expected) or (kind == "symlink" and os.readlink(name, dir_fd=dirfd)
                                                 != expected["target"]):
                raise _stopped("folder_changed", f"{shown} is not the entry that was verified", deleted, place,
                               item_id)
            try:
                os.unlink(name, dir_fd=dirfd)
            except OSError as exc:
                raise _stopped("remove_failed", f"{shown} cannot be deleted ({exc.strerror or exc})", deleted,
                               place, item_id) from None
            deleted += 1
    try:
        os.rmdir(item_id, dir_fd=removing_fd)
    except OSError as exc:
        code = "folder_changed" if exc.errno == errno.ENOTEMPTY else "remove_failed"
        raise _stopped(code, f"{item_id} cannot be removed ({exc.strerror or exc})", deleted, place,
                       item_id) from None
    return deleted + 1


def _drop_container(adir_fd: int) -> None:
    """Removes the empty `.removing` this run created, after a refusal that left
    the folder in its place or after a clean rename back. Best effort: a
    container that is not empty is somebody's leftover and stays."""
    try:
        os.rmdir(_REMOVING, dir_fd=adir_fd)
        os.fsync(adir_fd)
    except OSError:
        pass


def _restore(removing_fd: int, ai_fd: int | None, item_id: str, *, removing_place: str, ai_place: str) -> None:
    """D-Restore: renames the folder back, never replacing anything. Returns on
    a completed, durable rename; otherwise raises `restore_failed` naming the
    place the folder is in."""
    if ai_fd is None:
        raise _refuse("restore_failed", f"{ai_place.rsplit(os.sep, 1)[0]} is absent, so the folder was not moved "
                      f"back: it stays in {removing_place}",
                      f"create `.ai-review`, then `mv` the folder from {removing_place} to {ai_place}")
    if _lstat_at(item_id, ai_fd, shown=ai_place, code="restore_failed",
                 after=f"the folder stays in {removing_place}; once it is readable `mv` it to {ai_place}") is not None:
        raise _refuse("restore_failed", f"{ai_place} exists, so nothing was moved back: the folder stays in "
                      f"{removing_place}", f"compare the two by hand, move one aside, then `mv` {removing_place} "
                      f"to {ai_place}")
    try:
        _rename(item_id, item_id, src_dir_fd=removing_fd, dst_dir_fd=ai_fd)
    except OSError as exc:
        if exc.errno in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOTDIR):
            raise _refuse("restore_failed", f"{ai_place} is in the way, so nothing was moved back: the folder "
                          f"stays in {removing_place}", f"compare the two by hand, move one aside, then `mv` "
                          f"{removing_place} to {ai_place}") from None
        raise _refuse("restore_failed", f"the folder could not be renamed back ({exc.strerror or exc}): it is in "
                      f"{removing_place}", f"`mv` {removing_place} to {ai_place} by hand") from None
    where = f"the folder is back in {ai_place}, not confirmed durable"
    _fsync(ai_fd, ".ai-review", "restore_failed", where)
    _fsync(removing_fd, _REMOVING, "restore_failed", where)


def _open_removing(adir_fd: int, fds: list[int], *, create: bool = False) -> int | None:
    """`.removing` opened without following a link; None when absent (and not
    created). The caller owns the descriptor through `fds`."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(_REMOVING, flags, dir_fd=adir_fd)
    except FileNotFoundError:
        if not create:
            return None
        try:
            os.mkdir(_REMOVING, 0o700, dir_fd=adir_fd)
            fd = os.open(_REMOVING, flags, dir_fd=adir_fd)
        except OSError as exc:
            raise _refuse("place_failed", f"{_REMOVING} cannot be created: {exc.strerror or exc}",
                          "check the permissions and free space of the archive directory") from None
    except OSError as exc:
        raise _refuse("unreadable", f"{_REMOVING} cannot be opened: {exc.strerror or exc}",
                      "repair it; the engine does not remove from a partial view") from None
    fds.append(fd)
    return fd


def _judge_removal_archive(adir_fd: int | None, item_id: str, resolved: str) -> int:
    """D-Selection 6: the index's integrity first, then the archive file and the
    index line. Returns the archive's size."""
    ids: set[str] = set()
    if adir_fd is not None:
        fd, _ = _open_index(adir_fd, write=False)
        if fd is not None:
            try:
                ids = _index_ids(fd, os.fstat(fd).st_size)
            finally:
                os.close(fd)
    final = f"{item_id}{ARCHIVE_SUFFIX}"
    info = _lstat_at(final, adir_fd, shown=os.path.join(resolved, final)) if adir_fd is not None else None
    has_file = info is not None and stat.S_ISREG(info.st_mode)
    if not has_file or item_id not in ids:
        missing = ("the archive file" if not has_file else "") + (" and " if not has_file and item_id not in ids
                                                                  else "") + \
                  (f"its line in {_INDEX_NAME}" if item_id not in ids else "")
        raise _refuse("not_archived", f"{item_id} has no verified archive: {missing} is missing",
                      f"run `archive {item_id} --apply` first; the engine never removes a folder without an archive")
    return info.st_size


def _leftover_note(resolved: str, item_id: str) -> str:
    return (f"an earlier run's leftover is untouched in {os.path.join(resolved, _REMOVING, item_id)}; once this is "
            f"resolved, run `remove {item_id} --apply` again")


def _has_leftover(adir_fd: int | None, item_id: str) -> bool:
    if adir_fd is None:
        return False
    try:
        rm = os.open(_REMOVING, _FLAGS_DIR, dir_fd=adir_fd)
    except OSError:
        return False
    try:
        info = _lstat_raw(item_id, rm)
    except OSError:
        return False
    finally:
        os.close(rm)
    return info is not None and stat.S_ISDIR(info.st_mode)


def _select_removal(repo_root, item_id, archive_root, now, run, resolve, zstd, fds) -> dict:
    """D-Selection 1 to 11 for `remove`; nothing is written. Returns the facts the
    dry run and the flow share."""
    root, ai_fd, report, record, resolved, adir_fd = _select_finished(
        repo_root, item_id, archive_root, now, run, resolve, fds, "remove", needs=("rename",))
    try:
        archive_bytes = _judge_removal_archive(adir_fd, item_id, resolved)                       # 6
        if _git_tracked(root, item_id):                                                # 7
            raise _refuse("tracked_files", f"Git tracks an entry at or under .ai-review/{item_id}",
                          "this command removes the untracked folder only")
        removing_fd = _open_removing(adir_fd, fds)                                     # 8
        left_place = os.path.join(resolved, _REMOVING, item_id)
        leftover = removing_fd is not None and _lstat_at(item_id, removing_fd, shown=left_place) is not None
        in_place = ai_fd is not None and _lstat_at(item_id, ai_fd, shown=str(root / ".ai-review" / item_id)) \
            is not None
        if leftover:
            real = stat.S_ISDIR(_lstat_at(item_id, removing_fd, shown=left_place).st_mode)
            if not real or in_place:
                why = "it is not a real directory" if not real else f"{root / '.ai-review' / item_id} exists too"
                raise _refuse("leftover_conflict", f"{os.path.join(resolved, _REMOVING, item_id)} cannot be "
                              f"resumed: {why}", "compare the two by hand and move one aside; nothing was moved or "
                              "deleted")
        state = "L1" if leftover else "L0"
        folder_fd = removing_fd if leftover else ai_fd
        folder_path = os.path.join(resolved, _REMOVING, item_id) if leftover else str(root / ".ai-review" / item_id)
        if not leftover:                                                               # 9
            if ai_fd is None or not _is_real_dir(ai_fd, item_id):
                raise _refuse("review_folder_absent", f"{folder_path} is not a real directory",
                              "there is nothing to remove")
            for entry in report["read_errors"]:
                if _underneath(entry["path"], folder_path):
                    raise _refuse("unreadable", f"{entry['path']} cannot be read ({entry['error']})",
                                  "repair it; the engine does not remove from a partial view")
        entries = _scan(folder_fd, item_id)
        if state == "L0" and not _same_device(ai_fd, adir_fd):                         # 10
            raise _refuse("cross_device", ".ai-review and the archive directory are on different file systems",
                          "keep the archive store on the repository's file system (--archive-root), or leave the "
                          "folder; nothing was moved")
        program = zstd or shutil.which("zstd")                                         # 11
        if not program:
            raise _refuse("zstd_missing", "the zstd program was not found on PATH",
                          "install zstd so that it is on the PATH")
    except RetentionError as exc:
        if _has_leftover(adir_fd, item_id) and exc.code != "leftover_conflict":
            exc.next_step += "; " + _leftover_note(resolved, item_id)
        raise
    return {"root": root, "ai_fd": ai_fd, "adir_fd": adir_fd, "removing_fd": removing_fd, "report": report,
            "record": record, "resolved": resolved, "state": state, "folder_fd": folder_fd,
            "folder_path": folder_path, "entries": entries, "program": program, "archive_bytes": archive_bytes}


def remove_item(repo_root, item_id: str, *, apply: bool = False, archive_root=DEFAULT_ARCHIVE_ROOT,
                now: datetime.datetime, run=None, resolve=None, zstd: str | None = None) -> dict:
    """Removes the untracked review folder of a finished, archived item: one
    atomic rename out of the repository, a contents comparison with the archive,
    then the delete entry by entry, or the rename back. Dry run unless `apply`."""
    fds: list[int] = []
    try:
        sel = _select_removal(repo_root, item_id, archive_root, now, run, resolve, zstd, fds)
        resolved = sel["resolved"]
        archive = os.path.join(resolved, f"{item_id}{ARCHIVE_SUFFIX}")
        name = f"{item_id}{ARCHIVE_SUFFIX}"
        removing_place = os.path.join(resolved, _REMOVING, item_id)
        ai_place = str(sel["root"] / ".ai-review" / item_id)
        leftover = sel["state"] == "L1"

        def note(exc: RetentionError) -> RetentionError:
            if _has_leftover(sel["adir_fd"], item_id) and exc.code != "leftover_conflict":
                exc.next_step += "; " + _leftover_note(resolved, item_id)
            return exc

        if not apply:
            try:                                                                       # 12
                records = _hash_folder(sel["folder_fd"], item_id)
                table = _archive_table(sel["program"], sel["adir_fd"], name, item_id)
                diffs, missing = _differences(table, records, leftover)
                if diffs:
                    raise _mismatch(diffs, missing, archive)
            except RetentionError as exc:
                raise note(exc) from None
            removed, folder = {"removed": False}, sel["entries"]
        else:
            removed, folder = _flow(sel, item_id, name, archive, removing_place, ai_place, note, fds, repo_root,
                                    archive_root, now, run, resolve, zstd)
        record = sel["record"]
        files = [e for e in folder if e["type"] == "file"]
        return {
            "schema_version": SCHEMA_VERSION, "kind": "retention-remove",
            "mode": "apply" if apply else "dry-run", "generated_at": _iso(now),
            "repository": sel["report"]["repository"], "work_item_id": item_id,
            "pull_request": record["pull_request"],
            "review_folder": {"path": sel["folder_path"], "state": "leftover" if leftover else "in_place",
                              "files": len(files), "bytes": sum(e["size"] for e in files),
                              "entries": [{"path": e["path"], "type": e["type"], "bytes": e["size"]}
                                          for e in sorted(folder, key=lambda e: e["path"]) if e["path"]]},
            "archive": {"path": archive, "bytes": sel["archive_bytes"], "indexed": True},
            "target": {"removing": removing_place}, "verify": {"matches": True}, "zstd": sel["program"],
            "result": removed,
        }
    finally:
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError:
                pass


def _flow(sel, item_id, name, archive, removing_place, ai_place, note, fds, repo_root, archive_root, now, run,
          resolve, zstd) -> tuple[dict, list[dict]]:
    """D-Flow 1 to 7. Returns the result and the verified records."""
    adir_fd, leftover = sel["adir_fd"], sel["state"] == "L1"
    created = False
    try:                                                                               # 1
        try:
            fcntl.flock(adir_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise _refuse("archive_busy", "another `archive` or `remove` run holds this repository's archive "
                          "directory", "wait for it to finish, then run it again") from None
        again = _select_removal(repo_root, item_id, archive_root, now, run, resolve, zstd, fds)
        held = os.fstat(adir_fd)
        now_adir = os.fstat(again["adir_fd"])
        if (held.st_dev, held.st_ino) != (now_adir.st_dev, now_adir.st_ino) or again["state"] != sel["state"]:
            raise _refuse("place_failed", "the archive directory or the leftover changed while the lock was taken",
                          "run it again")
        sel = again
        removing_fd = sel["removing_fd"]                                               # 2
        if removing_fd is None:
            removing_fd = _open_removing(adir_fd, fds, create=True)
            created = True
            _fsync(removing_fd, _REMOVING)
            _fsync(adir_fd, "the archive directory")
    except RetentionError as exc:
        if created:
            _drop_container(adir_fd)
        raise note(exc) from None
    ai_fd = sel["ai_fd"]

    def leave_in_place(exc: RetentionError) -> RetentionError:
        if created:
            _drop_container(adir_fd)
        return exc

    before = None
    if not leftover:                                                                   # 3
        try:
            present = _lstat_at(item_id, removing_fd, shown=removing_place,
                                after="nothing was moved or deleted") is not None
        except RetentionError as exc:
            raise leave_in_place(exc) from None
        if present:
            raise leave_in_place(_refuse("leftover_conflict", f"{removing_place} appeared after the check",
                                         "compare it by hand and move one aside; nothing was moved or deleted"))
        try:
            before = os.stat(item_id, dir_fd=ai_fd, follow_symlinks=False)
        except OSError:
            raise leave_in_place(_refuse("folder_changed", f"{ai_place} vanished before it was moved",
                                         "nothing was moved; run it again")) from None
        try:
            _rename(item_id, item_id, src_dir_fd=ai_fd, dst_dir_fd=removing_fd)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                raise leave_in_place(_refuse("cross_device", ".ai-review and the archive directory are on different "
                                             "file systems", "keep the archive store on the repository's file system "
                                             "(--archive-root); nothing was moved")) from None
            raise leave_in_place(_refuse("place_failed", f"the folder could not be moved: {exc.strerror or exc}",
                                         "nothing was moved")) from None
        where = (f"the rename is done: the folder is whole in {removing_place}, not confirmed durable, nothing was "
                 f"renamed back or deleted; run `remove {item_id} --apply` again")
        _fsync(ai_fd, ".ai-review", "place_failed", where)
        _fsync(removing_fd, _REMOVING, "place_failed", where)
    resume = f"once it is readable run `remove {item_id} --apply` again"
    info = _lstat_at(item_id, removing_fd, shown=removing_place,                       # 4
                     after=(f"the leftover is untouched in {removing_place}, it may be incomplete and the missing "
                            f"entries exist only in the archive; nothing was renamed or deleted; {resume}") if leftover
                     else f"the folder is whole in {removing_place}; nothing was renamed back or deleted; {resume}")
    if info is None or not stat.S_ISDIR(info.st_mode) or \
            (before is not None and (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino)):
        raise _refuse("folder_changed", f"{removing_place} is not the folder that was moved",
                      f"nothing was renamed back or deleted; inspect {removing_place} by hand")
    compared, missing = False, 0                                                       # 5
    try:
        records = _hash_folder(removing_fd, item_id)
        table = _archive_table(sel["program"], adir_fd, name, item_id)
        diffs, missing = _differences(table, records, leftover)
        compared = True
        if diffs:
            raise _mismatch(diffs, missing, archive)
        if [_comparable(r) for r in _scan(removing_fd, item_id)] != [_comparable(r) for r in records]:
            raise _refuse("folder_changed", "the folder changed while it was verified",
                          "nothing was deleted; run it again when nothing writes to the folder")
    except RetentionError as exc:                                                      # 6, a refusal
        if exc.code not in ("archive_mismatch", "folder_changed", "unreadable", "unsupported_entry"):
            raise
        if leftover and not compared:
            exc.message += f"; the folder is in {removing_place}, it may be incomplete and the missing entries " \
                           f"exist only in the archive"
            exc.next_step += f"; repair the cause and run `remove {item_id} --apply` again"
            raise
        try:
            _restore(removing_fd, ai_fd, item_id, removing_place=removing_place, ai_place=ai_place)
        except RetentionError as failed:
            raise _refuse("restore_failed", f"{failed.message}; the refusal was {exc.code}: {exc.message}",
                          f"{failed.next_step}; {exc.next_step}") from None
        if created:
            _drop_container(adir_fd)
        exc.message += f"; the folder is back in {ai_place}"
        if exc.code != "archive_mismatch" and missing:
            exc.next_step += "; " + _incomplete_note(missing, archive)
        raise
    _remove_tree(removing_fd, item_id, records, place=removing_place)                  # 6, equal
    _fsync(removing_fd, _REMOVING, "remove_failed", "the folder is deleted, every entry proven archived, durability "
           "unconfirmed; there is nothing to restore")
    fcntl.flock(adir_fd, fcntl.LOCK_UN)                                                # 7
    files = [r for r in records if r["type"] == "file"]
    return {"removed": True, "files": len(files), "bytes": sum(r["size"] for r in files)}, records


# --------------------------------------------------------------------------
# workflow-2.24.0: the archive prune (`retention-prune`)
# --------------------------------------------------------------------------

_PRUNE_IDS_NEXT = "pass the ids exactly as the report's `archives` lists them"
_PRUNE_AGAIN = "run `prune` again for a fresh preview"


def _seen_by_report(entry: dict, info: os.stat_result | None) -> bool:
    """D-Check: a regular file of the size and modification time the report saw,
    the time formatted by the report's own expression."""
    if info is None or not stat.S_ISREG(info.st_mode) or info.st_size != entry["bytes"]:
        return False
    modified = datetime.datetime.fromtimestamp(info.st_mtime, datetime.timezone.utc)
    return _iso(modified) == entry["modified"]


def _prune_select(repo_root, ids: list[str], archive_root, ttl_days: int, now, run, resolve, fds: list[int]):
    """D-Selection 1 to 9 for `prune`; nothing is written. Returns
    `(report, resolved, adir_fd, entries, seen)`."""
    for item_id in ids:                                                                # 1
        _check_id(item_id, "prune", _PRUNE_IDS_NEXT)
    supported = {fn.__name__ for fn in os.supports_dir_fd}
    if not _platform_ok() or not {"unlink", "stat"} <= supported:                      # 2
        raise _refuse("path_unsafe", "this platform has no descriptor-relative file access",
                      "run it on Linux or macOS")
    root = Path(os.path.realpath(repo_root))
    archive_base = os.path.abspath(os.path.expanduser(str(archive_root)))
    report = build_report(root, archive_root=archive_base, ttl_days=ttl_days, now=now, run=run,  # 3
                          resolve=resolve)
    resolved, adir_fd = _select_destination(root, report, archive_base, fds, "prune")  # 4
    listed = {a["work_item_id"]: a for a in report["archives"]}
    for item_id in ids:                                                                # 5
        if item_id not in listed:
            raise _refuse("not_in_report", f"{item_id}{ARCHIVE_SUFFIX} is not an archive the report lists",
                          "prune only archives the report lists")
    for item_id in ids:                                                                # 6
        if item_id in report["removing"]:
            raise _refuse("removal_interrupted", f"{item_id} has an interrupted removal under "
                          f"{os.path.join(resolved, _REMOVING, item_id)}",
                          f"finish it first with `remove {item_id} --apply`; the archive may be the only copy of "
                          "the leftover's missing entries")
    for item_id in ids:                                                                # 7
        entry = listed[item_id]
        if not entry["prunable"]:
            raise _refuse("not_prunable", f"{entry['name']} is {entry['age_days']} days old, not above the time to "
                          f"live of {ttl_days} days", "it becomes prunable when it is older than the time to live")
    for item_id in ids:                                                                # 8
        if listed[item_id]["item"] in ("open", "recent"):
            raise _refuse("item_not_finished", f"{item_id} is {listed[item_id]['item']}: its archive may be the "
                          "only copy of its review folder",
                          "wait until the report classes the item `finished`")
    seen = {}
    for item_id in ids:                                                                # 9
        entry = listed[item_id]
        try:
            info = _lstat_raw(entry["name"], adir_fd) if adir_fd is not None else None
        except OSError as exc:
            raise _unexaminable(exc, entry, [], resolved, ids) from None
        if not _seen_by_report(entry, info):
            raise _refuse("archive_changed", f"{entry['name']} is not the file the report saw",
                          f"{_PRUNE_AGAIN}; nothing was removed")
        seen[item_id] = (info.st_size, info.st_mtime_ns)
    return report, resolved, adir_fd, [listed[i] for i in ids], seen


def _unchanged(entry: dict, adir_fd: int, seen: tuple) -> bool:
    info = _lstat_raw(entry["name"], adir_fd)
    return info is not None and stat.S_ISREG(info.st_mode) and (info.st_size, info.st_mtime_ns) == seen


def _unexaminable(exc: OSError, entry: dict, removed: list[str], resolved: str, pending: list[str]) -> RetentionError:
    """`prune_failed` for an archive whose `lstat` failed, with the progress so far."""
    return _refuse("prune_failed", _stop_after(removed, pending, f"{entry['name']} could not be examined: "
                                                f"{errno.errorcode.get(exc.errno, exc.errno)} "
                                                f"{exc.strerror or exc}"),
                   f"check the permissions of {resolved}; {_PRUNE_AGAIN}")


def _check_unchanged(entry: dict, adir_fd: int, seen: tuple, removed: list[str], pending: list[str],
                     resolved: str) -> None:
    """The check just before an unlink: `archive_changed` when the file differs, `prune_failed` (with the
    progress so far) when it cannot be examined."""
    try:
        same = _unchanged(entry, adir_fd, seen)
    except OSError as exc:
        raise _unexaminable(exc, entry, removed, resolved, pending) from None
    if not same:
        raise _refuse("archive_changed", _stop_after(removed, pending, f"{entry['name']} changed after the "
                                                      "preview"), _PRUNE_AGAIN)


def _stop_after(removed: list[str], pending: list[str], why: str) -> str:
    return f"{why}; removed: {', '.join(removed) or 'none'}; not removed: {', '.join(pending) or 'none'}"


def _prune_delete(entries: list[dict], seen: dict, adir_fd: int, resolved: str, ids: list[str]) -> list[str]:
    """D-Delete: each archive, in order, checked immediately before its unlink
    and made durable after it. Returns the removed ids."""
    removed: list[str] = []
    for position, entry in enumerate(entries):
        item_id = entry["work_item_id"]
        pending = ids[position:]
        _check_unchanged(entry, adir_fd, seen[item_id], removed, pending, resolved)    # 13 (a)
        try:                                                                           # (b)
            os.unlink(entry["name"], dir_fd=adir_fd)
        except OSError as exc:
            raise _refuse("prune_failed", _stop_after(removed, pending, f"{entry['name']} could not be removed: "
                                                       f"{errno.errorcode.get(exc.errno, exc.errno)} "
                                                       f"{exc.strerror or exc}"),
                          f"check the permissions of {resolved}; {_PRUNE_AGAIN}") from None
        removed.append(item_id)
        try:                                                                           # (c)
            os.fsync(adir_fd)
        except OSError as exc:
            raise _refuse("prune_failed", _stop_after(removed, pending[1:], f"{entry['name']} is removed, but its "
                                                       "durability is unconfirmed: "
                                                       f"{errno.errorcode.get(exc.errno, exc.errno)} "
                                                       f"{exc.strerror or exc}"),
                          f"check the permissions of {resolved}; {_PRUNE_AGAIN}") from None
    return removed


def prune_archives(repo_root, ids, *, apply: bool = False, archive_root=DEFAULT_ARCHIVE_ROOT,
                   ttl_days: int = DEFAULT_TTL_DAYS, now: datetime.datetime, run=None, resolve=None) -> dict:
    """Removes the named archives `<id>.tar.zst` that the report lists as past
    the time to live: one `unlink` each through the archive directory's
    descriptor, checked just before and made durable. Dry run unless `apply`.
    Never opens the index, an archive's contents, a review folder or the
    repository for writing."""
    ids = sorted(set(ids))
    if not ids:
        raise RetentionError("usage", "at least one work item id is required", "pass one or more ids")
    fds: list[int] = []
    try:
        report, resolved, adir_fd, entries, seen = _prune_select(repo_root, ids, archive_root, ttl_days, now, run,
                                                                 resolve, fds)
        removed: list[str] = []
        if apply:
            try:                                                                       # 10
                fcntl.flock(adir_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise _refuse("archive_busy", "another `archive`, `remove` or `prune` run holds this repository's "
                              "archive directory", "wait for it to finish, then run it again") from None
            try:
                errors: dict[str, str] = {}                                            # 11
                leftovers = _removing_ids(Path(resolved), errors)
                for item_id in ids:
                    if item_id in leftovers:
                        raise _refuse("removal_interrupted", f"{item_id} has an interrupted removal under "
                                      f"{os.path.join(resolved, _REMOVING, item_id)}",
                                      f"finish it first with `remove {item_id} --apply`")
                if errors:
                    path, why = sorted(errors.items())[0]
                    raise _refuse("unreadable", f"{path} cannot be read ({why})",
                                  "repair it; the engine does not prune from a partial view")
                for entry in entries:                                                  # 12
                    _check_unchanged(entry, adir_fd, seen[entry["work_item_id"]], [], ids, resolved)
                removed = _prune_delete(entries, seen, adir_fd, resolved, ids)         # 13
            finally:
                _remove_quietly(lambda: fcntl.flock(adir_fd, fcntl.LOCK_UN), [], "the lock")
        return {
            "schema_version": SCHEMA_VERSION, "kind": "retention-prune",
            "mode": "apply" if apply else "dry-run", "generated_at": _iso(now),
            "repository": report["repository"], "archive_dir": resolved, "ttl_days": ttl_days,
            "archives": [{"work_item_id": e["work_item_id"], "name": e["name"],
                          "path": os.path.join(resolved, e["name"]), "bytes": e["bytes"],
                          "modified": e["modified"], "age_days": e["age_days"], "item": e["item"]}
                         for e in entries],
            "index": os.path.join(resolved, _INDEX_NAME),
            "result": {"removed": removed, "bytes": sum(e["bytes"] for e in entries if e["work_item_id"] in removed)},
        }
    finally:
        for fd in reversed(fds):
            try:
                os.close(fd)
            except OSError:
                pass


def _size(count: int) -> str:
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{count} B"


def render_text(report: dict) -> str:
    lines = [f"Retention report for {report['repository']} ({report['generated_at']})",
             f"Archive directory: {report['archive_dir']}",
             f"Time to live: {report['ttl_days']} days; the {report['keep_recent']} most recent merged items are kept",
             "", f"Finished ({len(report['finished'])}):"]
    for record in report["finished"]:
        folder, archive = record["review_folder"], record["archive"]
        if archive["present"]:
            state = f"archive {_size(archive['bytes'])}, {archive['age_days']} days old" \
                    + (", prunable" if archive["prunable"] else "")
        else:
            state = "no archive"
        shown = f"{_size(folder['bytes'])} in {folder['files']} files" if folder["present"] else "no review folder"
        lines.append(f"  {record['work_item_id']}: {shown}; {state}")
        if folder["tracked_files"]:
            lines.append(f"    {folder['tracked_files']} tracked file(s) under .ai-review/{record['work_item_id']}/")
    if not report["finished"]:
        lines.append("  none")
    if report.get("removing"):
        lines += ["", f"Interrupted removals ({len(report['removing'])}): {', '.join(report['removing'])}, "
                      "finish them with `remove <id> --apply`"]
    kept = ", ".join(r["work_item_id"] for r in report["kept_recent"]) or "none"
    lines += ["", f"Kept as recent ({len(report['kept_recent'])}): {kept}", "",
              f"Not finished ({len(report['not_finished'])}):"]
    reasons: dict[str, int] = {}
    for record in report["not_finished"]:
        reasons[record["reason"]] = reasons.get(record["reason"], 0) + 1
    lines += [f"  {reason}: {count}" for reason, count in sorted(reasons.items())] or ["  none"]
    prunable = [a for a in report["archives"] if a["prunable"]]
    lines += ["", f"Archives ({len(report['archives'])}), prunable ({len(prunable)}):"]
    lines += [f"  {a['name']}: {_size(a['bytes'])}, {a['age_days']} days old, {a['item']}" for a in prunable] \
        or ["  none prunable"]
    if not report["complete"]:
        lines += ["", f"INCOMPLETE: {len(report['read_errors'])} path(s) could not be read; the sizes, archives and "
                      "absences above are not reliable for them:"]
        lines += [f"  {e['path']}: {e['error']}" for e in report["read_errors"]]
    return "\n".join(lines) + "\n"


def render_archive_text(result: dict) -> str:
    folder, target = result["review_folder"], result["target"]
    pr = result["pull_request"]
    lines = [f"Retention archive for {result['repository']}: {result['work_item_id']} "
             f"({result['mode']}, {result['generated_at']})",
             f"Merged pull request: #{pr.get('number')} {pr.get('url')}",
             f"Review folder: {folder['path']}",
             f"  {folder['files']} file(s), {_size(folder['bytes'])}; entries:"]
    lines += [f"    {e['type']:<8} {e['bytes']:>12}  {e['path']}" for e in folder["entries"]] or ["    none"]
    lines += [f"Archive:   {target['archive']}", f"Temporary: {target['temporary']}",
              f"Index:     {target['index']}", f"zstd:      {result['zstd']}"]
    if result["result"]["archived"]:
        lines.append(f"archived {_size(result['result']['bytes'])}; the review folder was not touched")
    else:
        lines.append("dry run: nothing was written; pass --apply to archive")
    return "\n".join(lines) + "\n"


def render_remove_text(result: dict) -> str:
    folder, pr = result["review_folder"], result["pull_request"]
    lines = [f"Retention remove for {result['repository']}: {result['work_item_id']} "
             f"({result['mode']}, {result['generated_at']})",
             f"Merged pull request: #{pr.get('number')} {pr.get('url')}",
             f"Review folder ({folder['state'].replace('_', ' ')}): {folder['path']}",
             f"  {folder['files']} file(s), {_size(folder['bytes'])}; entries:"]
    lines += [f"    {e['type']:<8} {e['bytes']:>12}  {e['path']}" for e in folder["entries"]] or ["    none"]
    lines += [f"Archive:   {result['archive']['path']} ({_size(result['archive']['bytes'])}, indexed)",
              f"Moved to:  {result['target']['removing']} (the folder is renamed there, verified, then deleted)",
              f"zstd:      {result['zstd']}", "contents match the archive"]
    if result["result"]["removed"]:
        lines.append(f"removed {result['result']['files']} file(s), {_size(result['result']['bytes'])}; "
                     "the archive holds them")
    else:
        lines.append("dry run: nothing was written; pass --apply to remove")
    return "\n".join(lines) + "\n"


def render_prune_text(result: dict) -> str:
    lines = [f"Retention prune for {result['repository']} ({result['mode']}, {result['generated_at']})",
             f"Archive directory: {result['archive_dir']}",
             f"Time to live: {result['ttl_days']} days", "Archives:"]
    lines += [f"  {a['name']}: {_size(a['bytes'])}, {a['age_days']} days old, {a['item']}" for a in result["archives"]]
    lines.append(f"Index:     {result['index']} (not touched; it keeps every line)")
    if result["mode"] == "apply":
        lines.append(f"removed {len(result['result']['removed'])} archive(s), {_size(result['result']['bytes'])}")
    else:
        lines.append("dry run: nothing was written; pass --apply to remove")
    return "\n".join(lines) + "\n"


def _ttl_of(text: str) -> int:
    if not re.fullmatch(r"[0-9]{1,5}", text) or not 1 <= int(text) <= MAX_TTL_DAYS:
        raise RetentionError("usage", f"--ttl-days must be an integer from 1 to {MAX_TTL_DAYS}, got {text!r}",
                             "pass --ttl-days 30")
    return int(text)


def _parse(argv) -> argparse.Namespace:
    parser = _Parser(prog="workflow_retention.py", description=__doc__.split("\n\n", 1)[0])
    commands = parser.add_subparsers(dest="command", parser_class=_Parser)
    repo_default = str(Path(__file__).resolve().parent.parent)
    report = commands.add_parser("report", help="print the retention report (writes nothing)")
    report.add_argument("--json", action="store_true", help="print one JSON object")
    report.add_argument("--archive-root", default=DEFAULT_ARCHIVE_ROOT)
    report.add_argument("--ttl-days", default=str(DEFAULT_TTL_DAYS))
    report.add_argument("--repo-root", default=repo_default)
    archive = commands.add_parser("archive", help="pack a finished item's review folder (a dry run without --apply)")
    archive.add_argument("work_item_id")
    archive.add_argument("--apply", action="store_true", help="write the archive and its index line")
    archive.add_argument("--json", action="store_true", help="print one JSON object")
    archive.add_argument("--archive-root", default=DEFAULT_ARCHIVE_ROOT)
    archive.add_argument("--repo-root", default=repo_default)
    remove = commands.add_parser("remove", help="remove an archived finished item's review folder (a dry run "
                                                "without --apply)")
    remove.add_argument("work_item_id")
    remove.add_argument("--apply", action="store_true", help="verify against the archive, then remove the folder")
    remove.add_argument("--json", action="store_true", help="print one JSON object")
    remove.add_argument("--archive-root", default=DEFAULT_ARCHIVE_ROOT)
    remove.add_argument("--repo-root", default=repo_default)
    prune = commands.add_parser("prune", help="remove archives past the time to live (a dry run without --apply)")
    prune.add_argument("work_item_ids", nargs="+", metavar="work_item_id")
    prune.add_argument("--apply", action="store_true", help="remove the named archives")
    prune.add_argument("--json", action="store_true", help="print one JSON object")
    prune.add_argument("--archive-root", default=DEFAULT_ARCHIVE_ROOT)
    prune.add_argument("--ttl-days", default=str(DEFAULT_TTL_DAYS))
    prune.add_argument("--repo-root", default=repo_default)
    args = parser.parse_args(argv)
    if args.command is None:
        raise RetentionError("usage", "a command is required",
                             "run `workflow_retention.py report`, `archive`, `remove` or `prune`")
    if args.command in ("report", "prune"):
        args.ttl_days = _ttl_of(args.ttl_days)
    return args


def main(argv=None, *, run=None, resolve=None, now: datetime.datetime | None = None,
         zstd: str | None = None) -> int:
    try:
        args = _parse(sys.argv[1:] if argv is None else list(argv))
        moment = now or datetime.datetime.now(datetime.timezone.utc)
        if args.command == "archive":
            result = archive_item(args.repo_root, args.work_item_id, apply=args.apply, archive_root=args.archive_root,
                                  now=moment, run=run, resolve=resolve, zstd=zstd)
            if args.json:
                sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
            else:
                sys.stdout.write(render_archive_text(result))
            return 0
        if args.command == "remove":
            result = remove_item(args.repo_root, args.work_item_id, apply=args.apply,
                                 archive_root=args.archive_root, now=moment, run=run, resolve=resolve, zstd=zstd)
            if args.json:
                sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
            else:
                sys.stdout.write(render_remove_text(result))
            return 0
        if args.command == "prune":
            result = prune_archives(args.repo_root, args.work_item_ids, apply=args.apply,
                                    archive_root=args.archive_root, ttl_days=args.ttl_days, now=moment, run=run,
                                    resolve=resolve)
            if args.json:
                sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
            else:
                sys.stdout.write(render_prune_text(result))
            return 0
        report = build_report(args.repo_root, archive_root=args.archive_root, ttl_days=args.ttl_days,
                              now=moment, run=run, resolve=resolve)
    except RetentionError as exc:
        print(f"error: {exc.code}: {exc.message}", file=sys.stderr)
        print(f"next step: {exc.next_step}", file=sys.stderr)
        return 2
    if args.json:
        sys.stdout.write(json.dumps(report, sort_keys=True) + "\n")
    else:
        sys.stdout.write(render_text(report))
    if not report["complete"]:
        print(f"warning: incomplete: {len(report['read_errors'])} path(s) could not be read "
              "(see read_errors); a missing size or archive may be unreadable, not absent", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
