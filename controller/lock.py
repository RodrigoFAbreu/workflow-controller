"""The per-worktree lifecycle lock (``workflow-controller-automatic-
lifecycle-orchestration`` CP5, "Concurrency and worker lifecycle").

:func:`acquire_lifecycle_lock` takes ``fcntl.flock(LOCK_EX | LOCK_NB)`` on
an ``O_RDONLY`` descriptor of the target worktree's own git directory
(``git rev-parse --absolute-git-dir``). It writes nothing into the target,
and it excludes every Controller on the machine working on the same
worktree, whatever runtime root each one uses. ``controller.job`` passes
the descriptor to the worker and to its stdin anchor (``pass_fds``;
``workflow-controller-worker-lifecycle-ownership``): the kernel keeps a
``flock`` held while *any* process holds that open file description, so
the worker and the anchor hold the lock for the job's whole owned
lifetime, including after the Controller is lost. A tool process the
worker starts does not inherit the descriptor (the harness closes it), so
it is the anchor, not a stray descendant, that keeps an orphaned job
held.

:func:`probe_lifecycle_lock` is the read-only report ``explain``/``inspect``
print. ``flock`` has no non-acquiring test, so it never acquires: it reads
``/proc/locks`` and matches a ``FLOCK`` holder entry by the device and
inode of the git directory's own descriptor, both taken from the kernel's
view of that descriptor (``/proc/self/fdinfo/<fd>``'s ``mnt_id:`` ->
``/proc/self/mountinfo``'s ``MAJ:MIN``, and ``fdinfo``'s ``ino:``) -- never
``st_dev``/``st_ino``, which differ from what ``/proc/locks`` prints on
btrfs subvolumes and overlayfs. It answers ``free`` only when read from the
init pid namespace, because ``/proc/locks`` omits an entry whose taker is
not visible in the reading namespace.

:func:`probe_file_lock` (``workflow-controller-worker-lifecycle-ownership``
CP5) is the same read-only probe for a regular file -- a job's supervisor
lock -- and also names the holders' pids, so a ``resume`` refused by an
attached Controller can say which one.

Imports nothing from the package beyond ``controller.errors``.
"""

from __future__ import annotations

import errno
import fcntl
import os
import subprocess
from pathlib import Path

from controller.errors import GitDirectoryUnresolvableError, LifecycleLockError, LifecycleWorkerActiveError

#: The probe's three answers. ``unknown`` never means ``free``.
HELD = "held"
FREE = "free"
UNKNOWN = "unknown"

#: ``/proc/self/ns/pid`` of the init pid namespace: the kernel's constant
#: ``PROC_PID_INIT_INO``. No other namespace can get this number.
INIT_PID_NAMESPACE = "pid:[4026531836]"


def _errno_name(exc: OSError) -> str:
    return errno.errorcode.get(exc.errno, str(exc.errno)) if exc.errno is not None else "None"


def resolve_git_dir(target_root: str | os.PathLike) -> Path:
    """The target worktree's own git directory, as Git names it
    (``git rev-parse --absolute-git-dir``: a linked worktree's own
    ``.git/worktrees/<name>``, never the common directory).

    Raises :class:`~controller.errors.GitDirectoryUnresolvableError` when
    Git cannot resolve it -- a lock is never silently skipped."""
    try:
        result = subprocess.run(
            ["git", "-C", str(target_root), "rev-parse", "--absolute-git-dir"],
            capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        raise GitDirectoryUnresolvableError(
            f"the git directory of {target_root} could not be resolved: {exc}",
            evidence={"target_root": str(target_root), "os_error": str(exc)},
        ) from exc
    git_dir = result.stdout.strip()
    if result.returncode != 0 or not git_dir:
        raise GitDirectoryUnresolvableError(
            f"the git directory of {target_root} could not be resolved "
            f"(git rev-parse --absolute-git-dir exited {result.returncode}: {result.stderr.strip()}), "
            f"so there is no lifecycle lock to take -- nothing was reconciled or launched",
            evidence={"target_root": str(target_root), "returncode": result.returncode,
                      "git_stderr": result.stderr.strip()},
        )
    return Path(git_dir)


def holders_hint(lock_path: Path) -> str:
    """How an operator finds every process holding the lock."""
    return f"`fuser -v {lock_path}` or `lsof +d {lock_path}` names every process holding it"


class LifecycleLock:
    """A held lifecycle lock: ``.path`` is the git directory, ``.fd`` the
    descriptor carrying the ``flock`` (``None`` once released). A context
    manager; leaving it closes the descriptor, which releases the lock
    unless a worker inherited it."""

    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self.fd: int | None = fd

    def release(self) -> None:
        if self.fd is not None:
            fd, self.fd = self.fd, None
            os.close(fd)

    def __enter__(self) -> "LifecycleLock":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def acquire_lifecycle_lock(target_root: str | os.PathLike) -> LifecycleLock:
    """Take the lifecycle lock on ``target_root``'s git directory.

    Only ``BlockingIOError`` from ``flock`` (``EWOULDBLOCK``/``EAGAIN``) is
    contention: :class:`~controller.errors.LifecycleWorkerActiveError`
    (exit 45), naming the lock path and how to find its holders. Every
    other ``OSError`` from the ``os.open`` or the ``flock`` is
    :class:`~controller.errors.LifecycleLockError` (exit 20), naming the
    path, the operation and the errno's name -- never the "worker active"
    text, and never an ``OSError`` escaping. A filesystem whose ``flock``
    emulation refuses an exclusive lock on a read-only directory descriptor
    (NFS without ``local_lock``) therefore cannot be driven: the
    fail-closed direction."""
    path = resolve_git_dir(target_root)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as exc:
        raise LifecycleLockError(
            f"the lifecycle lock on {path} could not be taken: open failed with "
            f"{_errno_name(exc)} ({exc.strerror})",
            evidence={"lock_path": str(path), "operation": "open", "errno": _errno_name(exc)},
        ) from exc
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        os.close(fd)
        raise LifecycleWorkerActiveError(
            f"another Controller or a previous worker holds the lifecycle lock on {path} -- "
            f"{holders_hint(path)}",
            evidence={"lock_path": str(path)},
        ) from exc
    except OSError as exc:
        os.close(fd)
        raise LifecycleLockError(
            f"the lifecycle lock on {path} could not be taken: flock failed with "
            f"{_errno_name(exc)} ({exc.strerror})",
            evidence={"lock_path": str(path), "operation": "flock", "errno": _errno_name(exc)},
        ) from exc
    return LifecycleLock(path, fd)


# ---------------------------------------------------------------------------
# The non-acquiring probe.
# ---------------------------------------------------------------------------


class _Unparseable(Exception):
    """A ``/proc`` read or line the probe cannot trust: answer ``unknown``."""


def _read_proc_text(rel_path: str) -> str:
    """The probe's one ``/proc`` reader (a patchable seam): the text of
    ``/proc/<rel_path>``."""
    with open(Path("/proc") / rel_path) as fh:
        return fh.read()


def read_pid_namespace() -> str | None:
    """``/proc/self/ns/pid`` (for example ``pid:[4026531836]``), or
    ``None`` where it cannot be read. The one patchable reader both the
    probe and ``controller.worker.read_process_context`` use."""
    try:
        return os.readlink("/proc/self/ns/pid")
    except OSError:
        return None


def _fdinfo_ids(fdinfo: str) -> tuple[int, int]:
    """``(mnt_id, ino)`` from an ``fdinfo`` text -- both required."""
    mnt_id = ino = None
    for line in fdinfo.splitlines():
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key = key.strip()
        if key == "mnt_id":
            mnt_id = _int(value.strip(), 10)
        elif key == "ino":
            ino = _int(value.strip(), 10)
    if mnt_id is None or ino is None:
        raise _Unparseable("fdinfo carries no mnt_id: or no ino: line")
    return mnt_id, ino


def _int(text: str, base: int) -> int:
    try:
        return int(text, base)
    except ValueError:
        raise _Unparseable(f"not an integer: {text!r}") from None


def _mount_device(mountinfo: str, mnt_id: int) -> tuple[int, int]:
    """The ``(major, minor)`` of the ``mountinfo`` line whose mount id is
    ``mnt_id`` (decimal ``MAJ:MIN``, field 3). Any line that does not parse
    is ``unknown``, as is a mount id with no line."""
    found = None
    for line in mountinfo.splitlines():
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) < 3:
            raise _Unparseable(f"mountinfo line has too few fields: {line!r}")
        line_mnt_id = _int(fields[0], 10)
        major, sep, minor = fields[2].partition(":")
        if not sep:
            raise _Unparseable(f"mountinfo MAJ:MIN does not parse: {fields[2]!r}")
        device = (_int(major, 10), _int(minor, 10))
        if line_mnt_id == mnt_id:
            found = device
    if found is None:
        raise _Unparseable(f"mount id {mnt_id} is absent from mountinfo")
    return found


def _lock_entries(locks: str) -> list[tuple[bool, str, tuple[int, int] | None, int | None, int]]:
    """Each ``/proc/locks`` line as ``(is_waiter, type, (major, minor),
    inode, pid)`` -- hexadecimal ``MAJ:MIN``, decimal inode, the taker's
    pid as the reading namespace sees it. A line whose device field is
    ``<none>`` has no inode (``None``, never a match). Any other line that
    does not parse makes the whole read ``unknown``."""
    entries = []
    for line in locks.splitlines():
        if not line.strip():
            continue
        tokens = line.split()
        if not tokens[0].endswith(":"):
            raise _Unparseable(f"/proc/locks line does not parse: {line!r}")
        rest = tokens[1:]
        is_waiter = bool(rest) and rest[0] == "->"
        if is_waiter:
            rest = rest[1:]
        # type, flavour, access, pid, maj:min:ino, start, end
        if len(rest) < 7:
            raise _Unparseable(f"/proc/locks line does not parse: {line!r}")
        lock_type, devino = rest[0], rest[4]
        pid = _int(rest[3], 10)
        if devino.startswith("<none>"):
            entries.append((is_waiter, lock_type, None, None, pid))
            continue
        parts = devino.split(":")
        if len(parts) != 3:
            raise _Unparseable(f"/proc/locks device field does not parse: {devino!r}")
        entries.append((is_waiter, lock_type, (_int(parts[0], 16), _int(parts[1], 16)), _int(parts[2], 10), pid))
    return entries


def _probe_open_path(path: Path, *, directory: bool) -> tuple[str, tuple[int, ...]]:
    """The non-acquiring probe of the ``FLOCK`` on the directory (or the
    regular file) at ``path``, opened read-only: ``(answer, holder pids)``.
    See :func:`probe_lifecycle_lock` for the three answers."""
    try:
        if directory:
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        else:
            fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    except OSError:
        return UNKNOWN, ()
    try:
        try:
            fdinfo = _read_proc_text(f"self/fdinfo/{fd}")
        finally:
            os.close(fd)
        mnt_id, ino = _fdinfo_ids(fdinfo)
        device = _mount_device(_read_proc_text("self/mountinfo"), mnt_id)
        entries = _lock_entries(_read_proc_text("locks"))
    except (OSError, _Unparseable):
        return UNKNOWN, ()
    holders = tuple(pid for is_waiter, lock_type, entry_device, entry_ino, pid in entries
                    if not is_waiter and lock_type == "FLOCK" and entry_device == device and entry_ino == ino)
    if holders:
        return HELD, holders
    if read_pid_namespace() != INIT_PID_NAMESPACE:
        return UNKNOWN, ()
    return FREE, ()


def probe_lifecycle_lock(target_root: str | os.PathLike) -> str:
    """``"held"``, ``"free"`` or ``"unknown"`` for ``target_root``'s
    lifecycle lock, **never acquiring it** -- an acquire-and-release probe
    could make a concurrent ``step`` exit 45 spuriously.

    - ``held``: a ``FLOCK`` holder entry (not a ``->`` waiter) in
      ``/proc/locks`` carries the git directory's mount device and inode,
      in any namespace -- an entry that is shown is a real holder;
    - ``free``: ``/proc/locks``, ``fdinfo``, ``mountinfo`` and the
      namespace link were all read and parsed, the reader is in the init
      pid namespace, and no holder entry matches;
    - ``unknown``: every other outcome -- an unreadable or unparseable
      read, an ``fdinfo`` without ``mnt_id:``/``ino:``, a mount id absent
      from ``mountinfo``, an unresolvable git directory, or no match read
      from outside the init pid namespace (where ``/proc/locks`` omits a
      lock whose taker has died while an inheriting worker still holds it).
    """
    try:
        path = resolve_git_dir(target_root)
    except GitDirectoryUnresolvableError:
        return UNKNOWN
    return _probe_open_path(path, directory=True)[0]


def probe_file_lock(path: str | os.PathLike) -> tuple[str, tuple[int, ...]]:
    """The same non-acquiring probe for the ``flock`` on a regular file
    (``workflow-controller-worker-lifecycle-ownership`` CP5: a job's
    supervisor lock, ``jobs/<job_id>/supervisor.lock``): ``(answer, holder
    pids)``, the pids being the lock takers ``/proc/locks`` names. A
    missing file is ``free`` -- nothing can hold it -- when read from the
    init pid namespace. It never creates the file."""
    if not os.path.lexists(path):
        return (FREE if read_pid_namespace() == INIT_PID_NAMESPACE else UNKNOWN), ()
    return _probe_open_path(Path(path), directory=False)
