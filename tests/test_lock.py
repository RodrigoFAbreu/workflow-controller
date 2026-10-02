"""Tests for the per-worktree lifecycle lock (``controller.lock``,
``workflow-controller-automatic-lifecycle-orchestration`` CP5,
"Concurrency and worker lifecycle").

``docs/ai-workflow/CONTROLLER_AUTOMATIC_LIFECYCLE_ORCHESTRATION_PLAN.md``'s
CP5 **Tests** list names the lock cases covered here:

- a second acquire on the same worktree fails while the first is held, from
  another process too;
- a subprocess that inherited the descriptor keeps it held after the parent
  lets go; a background grandchild that inherited it keeps it held after
  both the child and the parent are gone, and the exit-45 message then names
  the recorded worker *and* says another holder exists;
- every lock failure other than contention is ``LifecycleLockError``, never
  the exit-45 class or text;
- a directory that is not a git repository has no lock to take;
- ``probe_lifecycle_lock`` on the checkout's own (btrfs) filesystem, with
  the round-2 mount-device regression, and never taking the lock;
- ``probe_lifecycle_lock`` over patched ``/proc`` reads (every ``unknown``
  row, the waiter line, the ``st_dev``/``st_ino`` traps and the pid
  namespace rows);
- ``probe_lifecycle_lock`` inside a real child pid namespace.

Every process a test starts is in its own session and is ended, with its
whole process group, by the test's own cleanup (registered before the
spawn).
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from controller import job, lock  # noqa: E402
from controller.errors import (  # noqa: E402
    ControllerError,
    GitDirectoryUnresolvableError,
    LifecycleLockError,
    LifecycleWorkerActiveError,
)
from tests import fixtures, process_fixtures  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Acquires the lock on argv[2], prints its pid once it holds it, and holds
#: it until a line arrives on stdin (or it is killed).
_HOLDER_SCRIPT = r'''
import os, sys
sys.path.insert(0, sys.argv[1])
from controller import lock
held = lock.acquire_lifecycle_lock(sys.argv[2])
print(os.getpid(), flush=True)
sys.stdin.readline()
held.release()
'''

#: Tries to acquire the lock on argv[2] once and prints the outcome.
_TRY_ACQUIRE_SCRIPT = r'''
import sys
sys.path.insert(0, sys.argv[1])
from controller import lock
from controller.errors import LifecycleWorkerActiveError
try:
    lock.acquire_lifecycle_lock(sys.argv[2]).release()
    print("acquired")
except LifecycleWorkerActiveError:
    print("LifecycleWorkerActiveError")
'''

#: A process-group leader that inherited the lock descriptor (argv[1]):
#: forks a background member of its own group (which inherits it too),
#: prints the member's pid, and exits when a line arrives on stdin.
_LEADER_WITH_BACKGROUND_MEMBER = r'''
import os, sys, time
fd = int(sys.argv[1])
os.fstat(fd)  # the inherited lifecycle-lock descriptor is open here
member = os.fork()
if member == 0:
    devnull = os.open(os.devnull, os.O_RDWR)
    os.dup2(devnull, 0)
    os.dup2(devnull, 1)
    time.sleep(3600)
    os._exit(0)
print(member, flush=True)
sys.stdin.readline()
'''


def _spawn(test_case: unittest.TestCase, args: list[str], **kwargs) -> subprocess.Popen:
    """``Popen`` in a new session. The cleanup that SIGKILLs the whole group
    and reaps the child is registered before the spawn."""
    spawned: list[subprocess.Popen] = []

    def _cleanup() -> None:
        for proc in spawned:
            process_fixtures.kill_group(proc.pid)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass

    test_case.addCleanup(_cleanup)
    proc = subprocess.Popen(args, start_new_session=True, **kwargs)
    spawned.append(proc)
    return proc


def _try_acquire_in_another_process(root: Path) -> str:
    result = subprocess.run(
        [sys.executable, "-c", _TRY_ACQUIRE_SCRIPT, str(REPO_ROOT), str(root)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(f"the acquiring subprocess exited {result.returncode}: {result.stderr}")
    return result.stdout.strip()


def _start_holder(test_case: unittest.TestCase, root: Path) -> tuple[subprocess.Popen, int]:
    """Another process holding the lock on ``root`` until told to stop."""
    proc = _spawn(test_case, [sys.executable, "-c", _HOLDER_SCRIPT, str(REPO_ROOT), str(root)],
                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    line = proc.stdout.readline()
    if not line.strip():
        raise AssertionError(f"the holder never reported holding the lock (exit {proc.wait(timeout=10)})")
    return proc, int(line)


def _stop_holder(proc: subprocess.Popen) -> None:
    proc.stdin.write("\n")
    proc.stdin.flush()
    proc.wait(timeout=10)


def _kernel_ids(path: Path) -> tuple[tuple[int, int], int]:
    """``((major, minor), ino)`` of ``path`` as the kernel reports them for
    an open descriptor: ``fdinfo``'s ``mnt_id:`` -> ``mountinfo``'s decimal
    ``MAJ:MIN``, and ``fdinfo``'s ``ino:``. Parsed here independently of
    ``controller.lock``."""
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fdinfo = Path(f"/proc/self/fdinfo/{fd}").read_text()
    finally:
        os.close(fd)
    fields = {}
    for line in fdinfo.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    mnt_id, ino = int(fields["mnt_id"]), int(fields["ino"])
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        parts = line.split()
        if parts and int(parts[0]) == mnt_id:
            major, minor = parts[2].split(":")
            return (int(major), int(minor)), ino
    raise AssertionError(f"mount id {mnt_id} is not in /proc/self/mountinfo")


def _flock_holder_entries(pid: int) -> list[tuple[tuple[int, int], int]]:
    """Every ``FLOCK`` holder line (not a ``->`` waiter) of ``/proc/locks``
    taken by ``pid``, as ``((major, minor), ino)`` (hexadecimal device)."""
    entries = []
    for line in Path("/proc/locks").read_text().splitlines():
        tokens = line.split()
        if len(tokens) < 6 or tokens[1] == "->":
            continue
        if tokens[1] != "FLOCK" or tokens[4] != str(pid) or tokens[5].startswith("<none>"):
            continue
        major, minor, ino = tokens[5].split(":")
        entries.append(((int(major, 16), int(minor, 16)), int(ino)))
    return entries


def _expected_when_free() -> str:
    """``free`` read from the init pid namespace; ``unknown`` anywhere else
    (``/proc/locks`` may omit a holder there), never ``free``."""
    return lock.FREE if lock.read_pid_namespace() == lock.INIT_PID_NAMESPACE else lock.UNKNOWN


# ---------------------------------------------------------------------------
# Acquisition and contention.
# ---------------------------------------------------------------------------


class AcquireContentionTest(unittest.TestCase):
    def test_second_acquire_in_process_raises_worker_active_while_first_is_held(self) -> None:
        root = process_fixtures.scratch_git_repo(self)
        held = lock.acquire_lifecycle_lock(root)
        self.addCleanup(held.release)
        self.assertEqual(held.path, lock.resolve_git_dir(root))
        self.assertIsInstance(held.fd, int)

        with self.assertRaises(LifecycleWorkerActiveError) as ctx:
            lock.acquire_lifecycle_lock(root)
        self.assertEqual(ctx.exception.evidence["lock_path"], str(held.path))
        self.assertIn(str(held.path), ctx.exception.message)
        self.assertIn(f"fuser -v {held.path}", ctx.exception.message)
        self.assertIn(f"lsof +d {held.path}", ctx.exception.message)

        held.release()
        self.assertIsNone(held.fd)
        held.release()  # idempotent
        again = lock.acquire_lifecycle_lock(root)
        again.release()

    def test_second_acquire_from_another_process_raises_worker_active_until_released(self) -> None:
        root = process_fixtures.scratch_git_repo(self)
        with lock.acquire_lifecycle_lock(root) as held:
            self.assertEqual(_try_acquire_in_another_process(root), "LifecycleWorkerActiveError")
            self.assertIsNotNone(held.fd)
        self.assertIsNone(held.fd)  # leaving the context released it
        self.assertEqual(_try_acquire_in_another_process(root), "acquired")

    def test_this_process_is_refused_while_another_process_holds_it(self) -> None:
        root = process_fixtures.scratch_git_repo(self)
        holder, _ = _start_holder(self, root)
        with self.assertRaises(LifecycleWorkerActiveError):
            lock.acquire_lifecycle_lock(root)
        _stop_holder(holder)
        lock.acquire_lifecycle_lock(root).release()

    def test_acquire_writes_nothing_into_the_target(self) -> None:
        root = process_fixtures.scratch_git_repo(self)

        def snapshot() -> dict:
            return {str(p.relative_to(root)): p.stat().st_mtime_ns for p in root.rglob("*")}

        before = snapshot()
        with lock.acquire_lifecycle_lock(root):
            self.assertEqual(snapshot(), before)
        self.assertEqual(snapshot(), before)


class InheritedDescriptorTest(unittest.TestCase):
    def test_subprocess_that_inherited_the_descriptor_keeps_the_lock_after_parent_releases(self) -> None:
        root = process_fixtures.scratch_git_repo(self)
        held = lock.acquire_lifecycle_lock(root)
        self.addCleanup(held.release)
        sleeper = _spawn(self, [sys.executable, "-c", "import time; time.sleep(3600)"],
                         pass_fds=(held.fd,))
        self.assertTrue(process_fixtures.wait_until(lambda: process_fixtures.read_stat(sleeper.pid) is not None))

        held.release()  # the parent closes its own descriptor
        with self.assertRaises(LifecycleWorkerActiveError):
            lock.acquire_lifecycle_lock(root)
        self.assertEqual(_try_acquire_in_another_process(root), "LifecycleWorkerActiveError")

        process_fixtures.kill_group(sleeper.pid)
        sleeper.wait(timeout=10)
        lock.acquire_lifecycle_lock(root).release()

    def test_background_grandchild_keeps_the_lock_and_exit_45_names_recorded_worker_and_other_holder(self) -> None:
        root = process_fixtures.scratch_git_repo(self)
        held = lock.acquire_lifecycle_lock(root)
        self.addCleanup(held.release)

        member_box: list[int] = []

        def _await_member_gone() -> None:
            for member in member_box:
                process_fixtures.wait_until(lambda: process_fixtures.read_stat(member) is None, timeout=10)

        # Runs after _spawn's own cleanup has killed the group (LIFO order).
        self.addCleanup(_await_member_gone)
        leader = _spawn(self, [sys.executable, "-c", _LEADER_WITH_BACKGROUND_MEMBER, str(held.fd)],
                        pass_fds=(held.fd,), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        member = int(leader.stdout.readline())
        member_box.append(member)
        leader_stat = process_fixtures.read_stat(leader.pid)
        self.assertIsNotNone(leader_stat)
        leader_ticks = leader_stat[2]

        # The child (the group leader) exits and is reaped; the parent lets go.
        leader.stdin.write("\n")
        leader.stdin.flush()
        leader.wait(timeout=10)
        held.release()

        member_stat = process_fixtures.read_stat(member)
        self.assertIsNotNone(member_stat, "the background grandchild did not survive its parent")
        self.assertEqual(member_stat[1], leader.pid, "the grandchild is not in the leader's process group")
        self.assertIsNone(process_fixtures.read_stat(leader.pid))

        with self.assertRaises(LifecycleWorkerActiveError):
            lock.acquire_lifecycle_lock(root)
        self.assertEqual(_try_acquire_in_another_process(root), "LifecycleWorkerActiveError")

        # The exit-45 message, completed from a LAUNCHED record for this target.
        runtime = Path(tempfile.mkdtemp(prefix="cp5-lock-runtime-"))
        self.addCleanup(shutil.rmtree, runtime, True)
        (runtime / "jobs").mkdir()
        record = {
            "schema_version": 1,
            "job_id": "job-orphan",
            "target_repo": str(root),
            "status": "LAUNCHED",
            "worker_process": process_fixtures.worker_process_dict(leader.pid, start_ticks=leader_ticks),
        }
        (runtime / "jobs" / "job-orphan.json").write_text(json.dumps(record))
        managed_repo = fixtures.build_target_managed_repository(root)

        with self.assertRaises(LifecycleWorkerActiveError) as ctx:
            job._acquire_lifecycle_lock(runtime, managed_repo)
        message = ctx.exception.message
        git_dir = lock.resolve_git_dir(root)
        self.assertIn(f"holds the lifecycle lock on {git_dir}", message)
        self.assertIn(f"fuser -v {git_dir}", message)
        # The recorded worker, named through the member scan (its leader is gone).
        self.assertIn("job-orphan", message)
        self.assertIn(f"pid {leader.pid}", message)
        self.assertIn(f"running process(es) {member} are in its recorded process group {leader.pid}", message)
        self.assertIn("verify those processes", message)
        self.assertIn(f"kill -TERM -- -{leader.pid}", message)
        # ... and the other-holder sentence.
        self.assertIn("is that job's recorded stdin anchor (worker_anchor)", message)
        self.assertEqual(ctx.exception.evidence["lock_path"], str(git_dir))
        self.assertEqual(ctx.exception.evidence["recorded_workers"],
                         [{"job_id": "job-orphan", "verdict": "active"}])

        # Ending the group frees the lock.
        process_fixtures.kill_group(leader.pid)
        self.assertTrue(process_fixtures.wait_until(lambda: process_fixtures.read_stat(member) is None, timeout=10))
        self.assertTrue(process_fixtures.wait_until(
            lambda: _try_acquire_in_another_process(root) == "acquired", timeout=10))


    def test_exit_45_names_a_waiting_run_not_a_worker(self) -> None:
        # Functional review F3: the holder is the user's own `run`, waiting at
        # a gate; the message says so instead of describing worker process groups.
        root = process_fixtures.scratch_git_repo(self)
        held = lock.acquire_lifecycle_lock(root)
        self.addCleanup(held.release)
        runtime = Path(tempfile.mkdtemp(prefix="f3-lock-runtime-"))
        self.addCleanup(shutil.rmtree, runtime, True)
        holder = _spawn(self, [sys.executable, "-c", "import time; time.sleep(3600)"])
        self.assertTrue(process_fixtures.wait_until(lambda: process_fixtures.read_stat(holder.pid) is not None))
        (runtime / "runs" / "run-w").mkdir(parents=True)
        (runtime / "runs" / "run-w.json").write_text(json.dumps({
            "run_id": "run-w", "command": "run", "target_repo": str(root), "state": "running",
            "controller_process": process_fixtures.worker_process_dict(holder.pid)}))
        events = runtime / "runs" / "run-w" / "events.jsonl"
        events.write_text(json.dumps({"event": "run_started"}) + "\n"
                          + json.dumps({"event": "waiting", "gate": "release_pending",
                                        "deadline": "2026-10-02T12:00:00Z"}) + "\n")
        managed_repo = fixtures.build_target_managed_repository(root)

        with self.assertRaises(LifecycleWorkerActiveError) as ctx:
            job._acquire_lifecycle_lock(runtime, managed_repo)
        message = ctx.exception.message
        self.assertIn("Run run-w holds it: it is waiting at release_pending until 2026-10-02T12:00:00Z", message)
        self.assertIn("no worker is running", message)
        self.assertIn("Ctrl-C", message)
        self.assertNotIn("No recorded worker process group", message)
        self.assertNotIn("worker_anchor", message)
        self.assertEqual(ctx.exception.evidence["waiting_run"], "run-w")

        # Not waiting any more (the log's last event moved on): the worker text.
        events.write_text(events.read_text() + json.dumps({"event": "job_started"}) + "\n")
        with self.assertRaises(LifecycleWorkerActiveError) as ctx:
            job._acquire_lifecycle_lock(runtime, managed_repo)
        self.assertIn("No recorded worker process group", ctx.exception.message)
        self.assertNotIn("waiting_run", ctx.exception.evidence)


# ---------------------------------------------------------------------------
# Every other lock failure.
# ---------------------------------------------------------------------------


def _open_fds() -> set[str]:
    return set(os.listdir("/proc/self/fd"))


class LifecycleLockErrorTest(unittest.TestCase):
    def test_lifecycle_lock_error_is_a_sibling_of_worker_active(self) -> None:
        self.assertTrue(issubclass(LifecycleLockError, ControllerError))
        self.assertFalse(issubclass(LifecycleLockError, LifecycleWorkerActiveError))
        self.assertFalse(issubclass(LifecycleWorkerActiveError, LifecycleLockError))
        self.assertNotEqual(LifecycleLockError.code, LifecycleWorkerActiveError.code)

    def _assert_lock_error(self, exc: LifecycleLockError, *, operation: str, errno_name: str, path: Path) -> None:
        self.assertIs(type(exc), LifecycleLockError)
        self.assertNotIsInstance(exc, LifecycleWorkerActiveError)
        self.assertIn(errno_name, exc.message)
        self.assertIn(f"{operation} failed", exc.message)
        self.assertIn(str(path), exc.message)
        # Never exit 45's "worker active" text.
        self.assertNotIn("holds the lifecycle lock", exc.message)
        self.assertNotIn("another Controller", exc.message)
        self.assertNotIn("previous worker", exc.message)
        self.assertEqual(exc.evidence["operation"], operation)
        self.assertEqual(exc.evidence["errno"], errno_name)
        self.assertEqual(exc.evidence["lock_path"], str(path))

    def _flock_failure(self, err: int, errno_name: str) -> None:
        root = process_fixtures.scratch_git_repo(self)
        git_dir = lock.resolve_git_dir(root)
        before = _open_fds()
        with unittest.mock.patch.object(lock.fcntl, "flock", side_effect=OSError(err, os.strerror(err))):
            with self.assertRaises(LifecycleLockError) as ctx:
                lock.acquire_lifecycle_lock(root)
        self._assert_lock_error(ctx.exception, operation="flock", errno_name=errno_name, path=git_dir)
        self.assertIsInstance(ctx.exception.__cause__, OSError)
        self.assertEqual(_open_fds(), before, "the descriptor leaked after a failed flock")
        # Nothing was held: a real acquire succeeds afterwards.
        lock.acquire_lifecycle_lock(root).release()

    def test_flock_enolck_is_lifecycle_lock_error(self) -> None:
        self._flock_failure(errno.ENOLCK, "ENOLCK")

    def test_flock_ebadf_is_lifecycle_lock_error(self) -> None:
        self._flock_failure(errno.EBADF, "EBADF")

    def test_open_permission_error_is_lifecycle_lock_error(self) -> None:
        root = process_fixtures.scratch_git_repo(self)
        git_dir = lock.resolve_git_dir(root)
        real_open = os.open

        def refusing_open(path, flags, *args, **kwargs):
            if Path(path) == git_dir:
                raise PermissionError(errno.EACCES, "Permission denied", str(path))
            return real_open(path, flags, *args, **kwargs)

        with unittest.mock.patch.object(lock.os, "open", side_effect=refusing_open):
            with self.assertRaises(LifecycleLockError) as ctx:
                lock.acquire_lifecycle_lock(root)
        self._assert_lock_error(ctx.exception, operation="open", errno_name="EACCES", path=git_dir)
        self.assertIsInstance(ctx.exception.__cause__, PermissionError)


class GitDirectoryUnresolvableTest(unittest.TestCase):
    def test_non_git_directory_raises_git_directory_unresolvable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(GitDirectoryUnresolvableError) as ctx:
                lock.resolve_git_dir(td)
            self.assertEqual(ctx.exception.evidence["target_root"], td)
            self.assertNotEqual(ctx.exception.evidence["returncode"], 0)
            self.assertIn(td, ctx.exception.message)

            with self.assertRaises(GitDirectoryUnresolvableError):
                lock.acquire_lifecycle_lock(td)
            self.assertFalse(issubclass(GitDirectoryUnresolvableError, LifecycleWorkerActiveError))
            # The probe never answers free for it.
            self.assertEqual(lock.probe_lifecycle_lock(td), lock.UNKNOWN)

    def test_resolve_git_dir_names_the_worktree_git_directory(self) -> None:
        root = process_fixtures.scratch_git_repo(self)
        self.assertEqual(lock.resolve_git_dir(root), (root / ".git").resolve())


# ---------------------------------------------------------------------------
# The probe on the repository's own filesystem.
# ---------------------------------------------------------------------------


class ProbeRealFilesystemTest(unittest.TestCase):
    def test_probe_reports_held_while_another_process_holds_it_and_free_otherwise(self) -> None:
        root = process_fixtures.scratch_git_repo(self)
        self.assertEqual(lock.probe_lifecycle_lock(root), _expected_when_free())

        holder, holder_pid = _start_holder(self, root)
        self.assertEqual(lock.probe_lifecycle_lock(root), lock.HELD)

        # The round-2 regression: the /proc/locks entry carries the mount
        # device (mountinfo via fdinfo's mnt_id), not st_dev.
        git_dir = lock.resolve_git_dir(root)
        mount_device, kernel_ino = _kernel_ids(git_dir)
        st = os.stat(git_dir)
        st_device = (os.major(st.st_dev), os.minor(st.st_dev))
        entries = [entry for entry in _flock_holder_entries(holder_pid) if entry[1] == kernel_ino]
        self.assertEqual(len(entries), 1, f"no single FLOCK entry for the holder: {entries!r}")
        self.assertEqual(entries[0][0], mount_device)
        if st_device != mount_device:
            # btrfs here: st_dev is the subvolume's anonymous device.
            self.assertNotEqual(entries[0][0], st_device)
            self.assertNotIn((st_device, kernel_ino), _flock_holder_entries(holder_pid))

        _stop_holder(holder)
        self.assertEqual(lock.probe_lifecycle_lock(root), _expected_when_free())

    def test_probe_reports_held_for_this_process_own_lock(self) -> None:
        root = process_fixtures.scratch_git_repo(self)
        with lock.acquire_lifecycle_lock(root):
            self.assertEqual(lock.probe_lifecycle_lock(root), lock.HELD)
        self.assertEqual(lock.probe_lifecycle_lock(root), _expected_when_free())

    def test_probe_never_takes_the_lock(self) -> None:
        root = process_fixtures.scratch_git_repo(self)
        git_dir = lock.resolve_git_dir(root)
        stop = threading.Event()
        answers: list[str] = []
        errors: list[BaseException] = []

        def probe_loop() -> None:
            try:
                while not stop.is_set():
                    answers.append(lock.probe_lifecycle_lock(root))
            except BaseException as exc:  # reported below
                errors.append(exc)

        acquires = 0
        # Both loops skip the `git rev-parse` subprocess, so they are tight
        # and really interleave on the one git directory.
        with unittest.mock.patch.object(lock, "resolve_git_dir", new=lambda target_root: git_dir):
            thread = threading.Thread(target=probe_loop, daemon=True)
            thread.start()
            try:
                deadline = time.monotonic() + 1.5
                while time.monotonic() < deadline:
                    held = lock.acquire_lifecycle_lock(root)  # never LifecycleWorkerActiveError
                    held.release()
                    acquires += 1
            finally:
                stop.set()
                thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertGreater(acquires, 10)
        self.assertGreater(len(answers), 10)
        allowed = {lock.HELD, lock.FREE} if _expected_when_free() == lock.FREE else {lock.HELD, lock.UNKNOWN}
        self.assertLessEqual(set(answers), allowed)
        lock.acquire_lifecycle_lock(root).release()


# ---------------------------------------------------------------------------
# The probe over patched /proc reads.
# ---------------------------------------------------------------------------

_MNT_ID = 97
_INO = 7639376
_OTHER_NS = "pid:[4026532862]"

#: The mount device (decimal in mountinfo: 0:29 == 00:1d) and a different
#: st_dev-like device (0:34 == 00:22), as on the btrfs checkout.
_MOUNTINFO = (
    "22 1 0:34 / / rw,relatime shared:1 - btrfs /dev/nvme0n1p2 rw,subvol=/@\n"
    f"{_MNT_ID} 22 0:29 /@home /home rw,relatime shared:3 - btrfs /dev/nvme0n1p2 rw,subvol=/@home\n"
    "130 22 259:2 / /boot rw,relatime shared:5 - vfat /dev/nvme0n1p1 rw\n"
)
_FDINFO = f"pos:\t0\nflags:\t02100000\nmnt_id:\t{_MNT_ID}\nino:\t{_INO}\n"

#: Lines that are not this file's holder: a POSIX lock on the same inode, an
#: OFD lock, a lease, a lock with no inode, and a FLOCK on another inode.
_BACKGROUND_LOCKS = (
    f"1: POSIX  ADVISORY  WRITE 2387 00:1d:{_INO} 0 EOF\n"
    "2: OFDLCK ADVISORY  READ  -1 00:06:9391 0 EOF\n"
    "3: LEASE  ACTIVE    READ  4411 00:1d:55 0 EOF\n"
    "4: FLOCK  ADVISORY  WRITE 4412 <none>:0 0 EOF\n"
    "5: FLOCK  ADVISORY  WRITE 4413 00:1d:55 0 EOF\n"
)
_HOLDER_AT_MOUNT_DEVICE = f"23: FLOCK  ADVISORY  WRITE 1618310 00:1d:{_INO} 0 EOF\n"
_HOLDER_AT_ST_DEV = f"23: FLOCK  ADVISORY  WRITE 1618310 00:22:{_INO} 0 EOF\n"
_WAITER_AT_MOUNT_DEVICE = f"23: -> FLOCK  ADVISORY  WRITE 1618311 00:1d:{_INO} 0 EOF\n"


class ProbePatchedProcTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = process_fixtures.scratch_git_repo(self)
        self.git_dir = lock.resolve_git_dir(self.root)

    def _probe(self, *, fdinfo=_FDINFO, mountinfo=_MOUNTINFO, locks=_BACKGROUND_LOCKS,
               namespace=lock.INIT_PID_NAMESPACE) -> str:
        reads: list[str] = []

        def read(rel_path: str) -> str:
            reads.append(rel_path)
            if rel_path.startswith("self/fdinfo/"):
                value = fdinfo
            elif rel_path == "self/mountinfo":
                value = mountinfo
            elif rel_path == "locks":
                value = locks
            else:
                raise AssertionError(f"unexpected /proc read: {rel_path}")
            if isinstance(value, BaseException):
                raise value
            return value

        with unittest.mock.patch.object(lock, "_read_proc_text", side_effect=read), \
                unittest.mock.patch.object(lock, "read_pid_namespace", return_value=namespace):
            answer = lock.probe_lifecycle_lock(self.root)
        self.assertTrue(reads and reads[0].startswith("self/fdinfo/"), reads)
        return answer

    # -- held / free -------------------------------------------------------

    def test_holder_at_mount_device_and_inode_with_different_st_dev_is_held(self) -> None:
        self.assertEqual(self._probe(locks=_BACKGROUND_LOCKS + _HOLDER_AT_MOUNT_DEVICE), lock.HELD)

    def test_no_holder_read_from_init_namespace_is_free(self) -> None:
        self.assertEqual(self._probe(), lock.FREE)
        self.assertEqual(self._probe(locks=""), lock.FREE)

    def test_entry_at_st_dev_and_inode_only_is_free(self) -> None:
        self.assertEqual(self._probe(locks=_BACKGROUND_LOCKS + _HOLDER_AT_ST_DEV), lock.FREE)

    def test_only_a_waiter_line_is_free(self) -> None:
        self.assertEqual(self._probe(locks=_BACKGROUND_LOCKS + _WAITER_AT_MOUNT_DEVICE), lock.FREE)

    def test_hexadecimal_proc_locks_device_matches_decimal_mountinfo_device(self) -> None:
        fdinfo = f"pos:\t0\nflags:\t02100000\nmnt_id:\t130\nino:\t{_INO}\n"
        # 259:2 decimal in mountinfo is 103:02 hexadecimal in /proc/locks.
        self.assertEqual(self._probe(fdinfo=fdinfo, locks=f"9: FLOCK  ADVISORY  WRITE 77 103:02:{_INO} 0 EOF\n"),
                         lock.HELD)
        self.assertEqual(self._probe(fdinfo=fdinfo, locks=f"9: FLOCK  ADVISORY  WRITE 77 259:02:{_INO} 0 EOF\n"),
                         lock.FREE)

    # -- unknown, never free ----------------------------------------------

    def test_unreadable_proc_locks_is_unknown(self) -> None:
        self.assertEqual(self._probe(locks=PermissionError(errno.EACCES, "Permission denied")), lock.UNKNOWN)
        self.assertEqual(self._probe(locks=FileNotFoundError(errno.ENOENT, "No such file")), lock.UNKNOWN)

    def test_unreadable_fdinfo_or_mountinfo_is_unknown(self) -> None:
        self.assertEqual(self._probe(fdinfo=OSError(errno.EIO, "I/O error")), lock.UNKNOWN)
        self.assertEqual(self._probe(mountinfo=OSError(errno.EIO, "I/O error")), lock.UNKNOWN)

    def test_fdinfo_without_mnt_id_is_unknown(self) -> None:
        self.assertEqual(self._probe(fdinfo=f"pos:\t0\nflags:\t02100000\nino:\t{_INO}\n"), lock.UNKNOWN)

    def test_mount_id_absent_from_mountinfo_is_unknown(self) -> None:
        fdinfo = f"pos:\t0\nflags:\t02100000\nmnt_id:\t4242\nino:\t{_INO}\n"
        self.assertEqual(self._probe(fdinfo=fdinfo), lock.UNKNOWN)

    def test_unparseable_line_in_fdinfo_is_unknown(self) -> None:
        self.assertEqual(self._probe(fdinfo=f"mnt_id:\tninety-seven\nino:\t{_INO}\n"), lock.UNKNOWN)
        self.assertEqual(self._probe(fdinfo=f"mnt_id:\t{_MNT_ID}\nino:\t0x74a\n"), lock.UNKNOWN)

    def test_unparseable_line_in_mountinfo_is_unknown(self) -> None:
        self.assertEqual(self._probe(mountinfo=_MOUNTINFO + "garbage\n"), lock.UNKNOWN)
        self.assertEqual(self._probe(mountinfo=_MOUNTINFO + "131 22 0-40 / /mnt rw - ext4 /dev/sda rw\n"),
                         lock.UNKNOWN)
        self.assertEqual(self._probe(mountinfo=_MOUNTINFO + "x131 22 0:40 / /mnt rw - ext4 /dev/sda rw\n"),
                         lock.UNKNOWN)

    def test_unparseable_line_in_proc_locks_is_unknown(self) -> None:
        for bad in ("garbage without a colon\n",
                    "24: FLOCK  ADVISORY  WRITE\n",
                    "24: FLOCK  ADVISORY  WRITE 99 00:1d 0 EOF\n",
                    "24: FLOCK  ADVISORY  WRITE 99 zz:1d:5 0 EOF\n",
                    "24: FLOCK  ADVISORY  WRITE pid 00:1d:5 0 EOF\n"):
            with self.subTest(line=bad):
                self.assertEqual(self._probe(locks=_BACKGROUND_LOCKS + bad), lock.UNKNOWN)

    def test_fdinfo_without_ino_is_unknown_even_where_an_entry_at_st_ino_would_match(self) -> None:
        st_ino = os.stat(self.git_dir).st_ino
        fdinfo = f"pos:\t0\nflags:\t02100000\nmnt_id:\t{_MNT_ID}\n"
        locks = _BACKGROUND_LOCKS + f"23: FLOCK  ADVISORY  WRITE 1618310 00:1d:{st_ino} 0 EOF\n"
        self.assertEqual(self._probe(fdinfo=fdinfo, locks=locks), lock.UNKNOWN)
        self.assertEqual(self._probe(fdinfo=fdinfo, locks=_BACKGROUND_LOCKS), lock.UNKNOWN)

    # -- the pid namespace -------------------------------------------------

    def test_non_init_namespace_with_no_match_is_unknown(self) -> None:
        self.assertEqual(self._probe(namespace=_OTHER_NS), lock.UNKNOWN)

    def test_non_init_namespace_with_a_matching_holder_is_held(self) -> None:
        self.assertEqual(self._probe(namespace=_OTHER_NS, locks=_BACKGROUND_LOCKS + _HOLDER_AT_MOUNT_DEVICE),
                         lock.HELD)

    def test_unreadable_namespace_with_no_match_is_unknown_and_with_a_match_is_held(self) -> None:
        self.assertEqual(self._probe(namespace=None), lock.UNKNOWN)
        self.assertEqual(self._probe(namespace=None, locks=_BACKGROUND_LOCKS + _HOLDER_AT_MOUNT_DEVICE), lock.HELD)

    def test_init_namespace_constant(self) -> None:
        self.assertEqual(lock.INIT_PID_NAMESPACE, "pid:[4026531836]")


# ---------------------------------------------------------------------------
# The probe inside a real child pid namespace.
# ---------------------------------------------------------------------------


class ProbeChildPidNamespaceTest(unittest.TestCase):
    def test_probe_in_child_pid_namespace_is_unknown_after_the_taker_dies(self) -> None:
        available, reason = process_fixtures.unshare_available()
        if not available:
            self.skipTest(f"unprivileged user and pid namespaces are unavailable here: {reason}")
        root = process_fixtures.scratch_git_repo(self)
        results = process_fixtures.run_in_child_pid_namespace(root)
        self.assertNotEqual(results["namespace"], lock.INIT_PID_NAMESPACE)
        self.assertEqual(results["before"], lock.HELD)
        self.assertEqual(results["after"], lock.UNKNOWN)
        self.assertNotEqual(results["after"], lock.FREE)
        self.assertEqual(results["acquire_after"], "LifecycleWorkerActiveError")
        # Everything in the namespace has exited: the lock is free here.
        lock.acquire_lifecycle_lock(root).release()


if __name__ == "__main__":
    unittest.main()
