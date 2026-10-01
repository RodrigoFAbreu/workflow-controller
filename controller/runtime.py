"""The Controller-owned runtime root: resolution ladder, atomic JSON I/O,
and the containment guard that makes "the Controller never writes Workflow
state" a checked invariant rather than a promise.

See ``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, "Controller-owned runtime
state (the runtime root)".
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import secrets
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

from controller.errors import RuntimeContainmentError, RuntimeRootUnwritableError

#: Precedence ladder rows, named so evidence can report which one fired.
LADDER_RUNTIME_DIR = 1
LADDER_ENV_HOME = 2
LADDER_ORIGIN_CHECKOUT = 3
LADDER_XDG_STATE = 4

_ENV_HOME = "WORKFLOW_CONTROLLER_HOME"

#: ``identity.RUNTIME_KIND_SOURCE``, spelled here because ``identity`` is
#: later in the dependency order.
_RUNTIME_KIND_SOURCE = "source"


def _is_git_repository(path: Path) -> bool:
    """Whether ``path`` is (inside) a Git working tree, checked by asking
    Git rather than by looking for ``.git`` directly -- a worktree's own
    ``.git`` is a file, not a directory, and this must agree with every
    other ``git`` invocation in this package."""
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def resolve_runtime_root(
    *,
    runtime_dir: str | os.PathLike | None,
    origin_source_root: Path,
    runtime_kind: str,
    env: dict | None = None,
) -> tuple[Path, int]:
    """Resolve the runtime root per the stated precedence ladder. Returns
    ``(resolved_path, ladder_row)`` -- both are reported by ``status`` and
    carried in refusal evidence, never silently discarded.

    Row 3 (``<origin checkout>/.controller``) applies only to a ``"source"``
    runtime. A ``"package"`` or ``"unidentified"`` one falls through to row
    4 even when its origin sits inside a Git work tree -- a venv inside a
    checkout would otherwise resolve to ``site-packages/.controller``.

    ``env`` defaults to ``os.environ`` and is overridable only for tests
    (row 4's own ``XDG_STATE_HOME`` case, and row 2's env-var case, both
    need to be exercised without mutating the real process environment).
    """
    env = os.environ if env is None else env

    if runtime_dir is not None:
        return Path(runtime_dir).expanduser().resolve(), LADDER_RUNTIME_DIR

    home = env.get(_ENV_HOME)
    if home:
        return Path(home).expanduser().resolve(), LADDER_ENV_HOME

    if runtime_kind == _RUNTIME_KIND_SOURCE and _is_git_repository(origin_source_root):
        return (origin_source_root / ".controller").resolve(), LADDER_ORIGIN_CHECKOUT

    xdg_state = env.get("XDG_STATE_HOME")
    if xdg_state:
        base = Path(xdg_state).expanduser()
    else:
        base = Path.home() / ".local" / "state"
    return (base / "workflow-controller").resolve(), LADDER_XDG_STATE


def ensure_runtime_root(root: Path, *, ladder_row: int) -> Path:
    """Create ``root`` if it does not exist and confirm it is writable.
    Never a bare ``PermissionError``/``OSError`` -- always a named
    ``RuntimeRootUnwritableError`` carrying the resolved path and the
    ladder row that produced it."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        probe = root / f".write-probe-{secrets.token_hex(8)}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        raise RuntimeRootUnwritableError(
            f"the runtime root at {root} (ladder row {ladder_row}) cannot be created or "
            f"written: {exc}",
            evidence={"path": str(root), "ladder_row": ladder_row, "os_error": str(exc)},
        ) from exc
    return root


def _assert_contained(root: Path, path: Path) -> None:
    root = root.resolve()
    try:
        path.resolve().relative_to(root)
    except ValueError:
        raise RuntimeContainmentError(
            f"refusing to write {path} -- it is outside the permitted root {root}",
            evidence={"path": str(path), "root": str(root)},
        ) from None


def assert_contained(root: Path, path: Path) -> None:
    """Public wrapper over the containment guard, for call sites that
    already own a narrower root than the full ``runtime_root`` (e.g.
    ``identity.py``'s snapshot-materialisation sites, each scoped to its
    own ``source_dir``/``dest``) -- see
    ``docs/ai-workflow/CONTROLLER_GEN1_HARDENING_PLAN.md``'s CP1. Neither
    parameter is runtime-root-specific: ``_assert_contained`` already
    checks any ``(root, path)`` pair."""
    _assert_contained(root, path)


def _atomic_write(runtime_root: Path, rel_path: str | os.PathLike, data: bytes) -> Path:
    """The shared atomic-write mechanics :func:`write_json` and
    :func:`write_bytes` both use: containment-checked, written to a
    ``.tmp`` sibling in the same directory, ``fsync``ed, then
    ``os.replace``d onto the target -- an interruption never leaves a
    half-written record."""
    full_path = (runtime_root / rel_path)
    _assert_contained(runtime_root, full_path)
    full_path = full_path.resolve()
    full_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = full_path.parent / f".{full_path.name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, full_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return full_path


def write_json(runtime_root: Path, rel_path: str | os.PathLike, obj: dict) -> Path:
    """Atomically write ``obj`` as canonical JSON to
    ``<runtime_root>/<rel_path>``. Raises ``RuntimeContainmentError`` unless
    the resolved destination is inside ``runtime_root`` -- containment is a
    package-wide invariant, enforced everywhere by
    ``assert_contained``/``_assert_contained``; this function reaches it via
    :func:`_atomic_write`, while ``identity.py``'s snapshot-materialisation
    sites call it directly against their own narrower roots.

    Atomic: writes to a ``.tmp`` sibling in the same directory, ``fsync``s
    it, then ``os.replace``s it onto the target -- an interruption never
    leaves a half-written record."""
    data = json.dumps(obj, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    return _atomic_write(runtime_root, rel_path, data)


def write_bytes(runtime_root: Path, rel_path: str | os.PathLike, data: bytes) -> Path:
    """Atomically write raw ``data`` (not JSON) to
    ``<runtime_root>/<rel_path>``, under the same containment guard and
    atomic write/fsync/replace sequence as :func:`write_json` -- the
    sibling write path for content that is not JSON (CP6's own worker
    ``jobs/<job_id>/worker.std{out,err}`` capture)."""
    return _atomic_write(runtime_root, rel_path, data)


def create_json(runtime_root: Path, rel_path: str | os.PathLike, obj: dict) -> Path:
    """Create ``<runtime_root>/<rel_path>`` holding ``obj`` as canonical
    JSON, with ``O_EXCL`` semantics: an existing file is a
    ``FileExistsError`` and is left untouched, so two processes can never
    both create one record (``workflow-controller-trunk-branch-pr-release-
    orchestration`` CP6's binding records). Containment-checked, and as
    atomic as :func:`write_json`: the bytes are written and ``fsync``ed to
    a ``.tmp`` sibling, which is then hard-linked onto the target (a link
    never replaces an existing name)."""
    data = json.dumps(obj, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    full_path = runtime_root / rel_path
    _assert_contained(runtime_root, full_path)
    full_path = full_path.resolve()
    full_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = full_path.parent / f".{full_path.name}.{secrets.token_hex(8)}.tmp"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.link(tmp_path, full_path)
    finally:
        tmp_path.unlink(missing_ok=True)
    return full_path


def rename_exclusive(runtime_root: Path, src_rel: str | os.PathLike, dst_rel: str | os.PathLike) -> Path:
    """Rename ``<runtime_root>/<src_rel>`` to ``<runtime_root>/<dst_rel>``
    without ever replacing an existing destination (``FileExistsError``,
    nothing changed). Both paths are containment-checked. Implemented as a
    hard link followed by removing the source, so an interruption between
    the two leaves both names holding the same bytes -- never neither."""
    src, dst = runtime_root / src_rel, runtime_root / dst_rel
    _assert_contained(runtime_root, src)
    _assert_contained(runtime_root, dst)
    src, dst = src.resolve(), dst.resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    os.link(src, dst)
    src.unlink()
    return dst


def remove_file(runtime_root: Path, rel_path: str | os.PathLike) -> None:
    """Remove ``<runtime_root>/<rel_path>`` (containment-checked). Used only
    to finish an interrupted :func:`rename_exclusive`, whose destination
    already holds the same bytes."""
    full_path = runtime_root / rel_path
    _assert_contained(runtime_root, full_path)
    full_path.resolve().unlink()


def create_log_file(runtime_root: Path, rel_path: str | os.PathLike) -> Path:
    """Create ``<runtime_root>/<rel_path>`` empty, with mode ``0o600``,
    and return its resolved path -- the file a worker then writes its own
    stream into (``workflow-controller-release-runtime-observability``
    CP4's ``jobs/<job_id>/worker.std{out,err}``). Containment-checked like
    every runtime write; ``O_CREAT | O_EXCL``, so an existing file is an
    ``OSError`` rather than a log two jobs share. Parent directories are
    created as needed."""
    full_path = runtime_root / rel_path
    _assert_contained(runtime_root, full_path)
    full_path = full_path.resolve()
    full_path.parent.mkdir(parents=True, exist_ok=True)
    os.close(os.open(full_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    return full_path


def open_lock_file(root: Path, rel_path: str | os.PathLike) -> int:
    """Open ``<root>/<rel_path>`` for an ``flock``, creating it (mode
    ``0o600``, parent directories as needed) when absent, and return the
    descriptor. Containment-checked like every runtime write. The
    descriptor is ``O_CLOEXEC`` and never passed on, so a lock taken on it
    dies with this process (``workflow-controller-worker-lifecycle-
    ownership`` CP3: a job's supervisor lock, ``jobs/<job_id>/
    supervisor.lock``)."""
    full_path = root / rel_path
    _assert_contained(root, full_path)
    full_path = full_path.resolve()
    full_path.parent.mkdir(parents=True, exist_ok=True)
    return os.open(full_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)


def append_jsonl(runtime_root: Path, rel_path: str | os.PathLike, obj: dict) -> Path:
    """Append ``obj`` as one JSON line to ``<runtime_root>/<rel_path>``
    (``workflow-controller-release-runtime-observability`` CP5's lifecycle
    event logs). Containment-checked like every runtime write; the object
    is serialised before the file is opened, so a non-JSON value is a
    ``TypeError`` that leaves the log untouched. ``O_APPEND | O_CREAT``,
    mode ``0o600``, one ``write()`` per line and no ``fsync``: the log is
    presentation, never an authority. Parent directories are created as
    needed."""
    data = json.dumps(obj, sort_keys=True).encode("utf-8") + b"\n"
    full_path = runtime_root / rel_path
    _assert_contained(runtime_root, full_path)
    full_path = full_path.resolve()
    full_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(full_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return full_path


#: The one-warning-per-process latch :func:`_best_effort` sets.
_best_effort_warned = False


def _best_effort(runtime_root: Path, rel_path: str | os.PathLike, write) -> bool:
    """Run ``write()``; on any ``Exception`` (never a ``BaseException``, so
    ``KeyboardInterrupt``/``SystemExit`` still propagate) report the first
    failure in this process on stderr and return ``False``. Catching
    ``Exception`` rather than ``OSError`` is deliberate: a containment
    refusal is a ``ControllerError`` and a non-JSON detail is a
    ``TypeError``, and neither may reach the lifecycle. The warning itself
    never raises either."""
    global _best_effort_warned
    try:
        write()
    except Exception as exc:  # noqa: BLE001 -- best-effort by contract
        if not _best_effort_warned:
            _best_effort_warned = True
            try:
                print(f"workflow-controller: warning: could not write {Path(runtime_root) / rel_path}: {exc}",
                      file=sys.stderr, flush=True)
            except Exception:  # noqa: BLE001 -- a closed stderr is not a lifecycle failure
                pass
        return False
    return True


def append_jsonl_best_effort(runtime_root: Path, rel_path: str | os.PathLike, obj: dict) -> bool:
    """:func:`append_jsonl`, best-effort: every event-log append goes
    through here. Returns whether the line was written."""
    return _best_effort(runtime_root, rel_path, lambda: append_jsonl(runtime_root, rel_path, obj))


def write_json_best_effort(runtime_root: Path, rel_path: str | os.PathLike, obj: dict) -> bool:
    """:func:`write_json`, best-effort: the run record
    (``runs/<run_id>.json``) goes through here. The job record never
    does -- it stays the only authority."""
    return _best_effort(runtime_root, rel_path, lambda: write_json(runtime_root, rel_path, obj))


def read_json(path: Path) -> dict | None:
    """Read and parse a JSON file, returning ``None`` if it does not exist.
    A malformed file raises ``json.JSONDecodeError`` (or ``OSError``) --
    this function never silently substitutes a default for corrupt content."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    return json.loads(raw)


# ---------------------------------------------------------------------------
# The user settings file (workflow-controller-settings-and-telemetry CP1).
# It lives outside the runtime root, so its primitives are contained against
# the settings file's own directory.
# ---------------------------------------------------------------------------

#: The sibling lock file's suffix: ``settings.json`` is locked through
#: ``settings.json.lock`` in the same directory.
SETTINGS_LOCK_SUFFIX = ".lock"


def settings_target(path: str | os.PathLike) -> Path:
    """The file a settings path names, with symlinks resolved: the lock,
    the read and the write all act on it, so two spellings of one file
    share one lock and a symlinked file stays a symlink."""
    return Path(path).expanduser().resolve()


@contextlib.contextmanager
def settings_lock(path: str | os.PathLike) -> Iterator[Path]:
    """Hold the exclusive ``flock`` on the settings file's sibling lock
    file for the whole read-modify-write (I4), creating the directory and
    the lock file as needed. Blocks until the lock is free. Yields the
    resolved settings path. An ``OSError`` (an unwritable directory, say)
    propagates to the caller, which decides whether to skip its write."""
    target = settings_target(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd = open_lock_file(target.parent, target.name + SETTINGS_LOCK_SUFFIX)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield target
    finally:
        os.close(fd)


def read_settings_bytes(path: str | os.PathLike) -> bytes | None:
    """The settings file's bytes, or ``None`` when it does not exist. Any
    other ``OSError`` propagates."""
    try:
        return settings_target(path).read_bytes()
    except FileNotFoundError:
        return None


def write_settings_atomically(path: str | os.PathLike, obj: dict) -> Path:
    """Write ``obj`` to the settings file as canonical JSON, atomically
    (:func:`write_json`'s temporary file, ``fsync`` and rename), contained
    against the file's own directory. The caller holds
    :func:`settings_lock`."""
    target = settings_target(path)
    return write_json(target.parent, target.name, obj)
