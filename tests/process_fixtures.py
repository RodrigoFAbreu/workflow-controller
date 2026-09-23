"""Real-process fixtures for `workflow-controller-automatic-lifecycle-
orchestration` CP5's lifecycle-lock and worker-liveness tests.

- :func:`spawn_sleeper`: a test-owned process group that is live in this
  boot, ended by the test's own cleanup.
- :class:`ZombieGroup`: a helper process that makes itself a child
  subreaper (``prctl(PR_SET_CHILD_SUBREAPER)`` through ``ctypes``) and never
  reaps, so an exited group member stays a zombie. This host's ``systemd``
  reaps orphans, so a test cannot otherwise wait for an orphan to become a
  zombie (round 4, I1). Two shapes: an unreaped zombie leader, and a group
  whose only survivor is an unreaped zombie member (its leader reaped).
  Releasing the helper lets it exit, so its zombies are reparented and
  reaped.
- :func:`unshare_available` / :func:`run_in_child_pid_namespace`: the
  ``unshare -Urpf --mount-proc`` probe runner (round 4, I2), where
  unprivileged user namespaces exist.
- :func:`scratch_git_repo`: a git repository under the checkout's
  gitignored ``build/``, not ``/tmp`` -- on the development machine ``/tmp``
  is tmpfs, where ``st_dev`` equals the mount device, while the checkout is
  on btrfs, where it does not (round 2, I1).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = REPO_ROOT / "build"


def wait_until(predicate, *, timeout: float = 15.0, interval: float = 0.02) -> bool:
    """Poll ``predicate`` until it is true or ``timeout`` elapses (bounded)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def read_stat(pid: int) -> tuple[str, int, int] | None:
    """``(state, pgrp, start_ticks)`` from the real ``/proc/<pid>/stat``, or
    ``None`` when the process is gone."""
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return None
    rest = text[text.rfind(")") + 1:].split()
    return rest[0], int(rest[2]), int(rest[19])


def kill_group(pgid: int | None) -> None:
    """``SIGKILL`` a whole process group, ignoring one that is already gone."""
    if not isinstance(pgid, int) or pgid <= 1:
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def group_has_running_member(pgid: int) -> bool:
    """Whether any process in ``pgid`` is running (not a zombie), from the
    real ``/proc``."""
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        stat = read_stat(int(name))
        if stat is not None and stat[1] == pgid and stat[0] not in ("Z", "X", "x"):
            return True
    return False


def spawn_sleeper(test_case: unittest.TestCase) -> subprocess.Popen:
    """A test-owned sleeper in its own session (its pid is its pgid),
    killed and reaped by ``test_case``'s cleanup."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"], start_new_session=True)

    def _cleanup() -> None:
        kill_group(proc.pid)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    test_case.addCleanup(_cleanup)
    assert wait_until(lambda: read_stat(proc.pid) is not None)
    return proc


def worker_process_dict(pid: int, *, pgid: int | None = None, start_ticks: int | None | object = ...,
                        context: dict | None = None) -> dict:
    """A ``worker_process`` record for ``pid`` in ``context`` (the current
    ``worker.read_process_context()`` by default). ``start_ticks`` defaults
    to the process's real one."""
    from controller import worker

    if start_ticks is ...:
        stat = read_stat(pid)
        start_ticks = stat[2] if stat is not None else None
    return {
        "pid": pid, "pgid": pid if pgid is None else pgid, "start_ticks": start_ticks,
        **(worker.read_process_context() if context is None else context),
    }


# ---------------------------------------------------------------------------
# Zombies, through a child subreaper that never reaps.
# ---------------------------------------------------------------------------

_ZOMBIE_HELPER = r'''
import ctypes, json, os, signal, sys, time

PR_SET_CHILD_SUBREAPER = 36
libc = ctypes.CDLL(None, use_errno=True)
if libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
    sys.exit(3)
mode, out_path, release_path = sys.argv[1:4]


def stat(pid):
    try:
        with open(f"/proc/{pid}/stat") as fh:
            text = fh.read()
    except (FileNotFoundError, ProcessLookupError):
        return None
    rest = text[text.rfind(")") + 1:].split()
    return rest[0], int(rest[2]), int(rest[19])


def wait_zombie(pid):
    for _ in range(1000):
        s = stat(pid)
        if s is not None and s[0] == "Z":
            return
        time.sleep(0.01)
    sys.exit(4)


if mode == "leader":
    leader = os.fork()
    if leader == 0:
        os.setsid()
        os._exit(0)
    wait_zombie(leader)
    info = {"pid": leader, "pgid": leader, "start_ticks": stat(leader)[2]}
elif mode == "member":
    r, w = os.pipe()
    leader = os.fork()
    if leader == 0:
        os.close(r)
        os.setsid()
        member = os.fork()
        if member == 0:
            os.close(w)
            time.sleep(3600)
            os._exit(0)
        os.write(w, f"{member}\n".encode())
        os._exit(0)
    os.close(w)
    member = int(os.read(r, 64).decode().strip())
    wait_zombie(leader)
    leader_ticks = stat(leader)[2]
    os.waitpid(leader, 0)  # the leader is reaped; the member is reparented here
    for _ in range(1000):
        if stat(leader) is None:
            break
        time.sleep(0.01)
    os.kill(member, signal.SIGKILL)
    wait_zombie(member)  # the helper never reaps it
    info = {"pid": leader, "pgid": leader, "start_ticks": leader_ticks, "member_pid": member}
else:
    sys.exit(2)

with open(out_path + ".tmp", "w") as fh:
    json.dump(info, fh)
os.replace(out_path + ".tmp", out_path)
while not os.path.exists(release_path):
    time.sleep(0.02)
'''


class ZombieGroup:
    """A recorded process group made only of zombies, held by a subreaper
    helper that never reaps: ``mode="leader"`` is an unreaped zombie
    ``setsid`` leader; ``mode="member"`` is a group whose leader was
    reaped and whose only survivor is an unreaped zombie member.
    ``.info`` carries ``pid``/``pgid``/``start_ticks`` (and
    ``member_pid``). The test's cleanup releases the helper, which exits,
    so the zombies are reparented and reaped."""

    def __init__(self, test_case: unittest.TestCase, mode: str) -> None:
        self._dir = Path(tempfile.mkdtemp(prefix="zombie-group-"))
        out_path = self._dir / "info.json"
        self._release = self._dir / "release"
        self._helper = subprocess.Popen(
            [sys.executable, "-c", _ZOMBIE_HELPER, mode, str(out_path), str(self._release)],
        )
        test_case.addCleanup(self.release)
        if not wait_until(lambda: out_path.exists() or self._helper.poll() is not None):
            raise AssertionError("the zombie helper never reported its group")
        if not out_path.exists():
            raise AssertionError(f"the zombie helper exited {self._helper.returncode} before reporting")
        self.info = json.loads(out_path.read_text())

    def release(self) -> None:
        self._release.touch()
        try:
            self._helper.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._helper.kill()
            self._helper.wait(timeout=10)
        for pid in (self.info.get("pid"), self.info.get("member_pid")) if hasattr(self, "info") else ():
            kill_group(pid)
        shutil.rmtree(self._dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# A real child pid namespace (`unshare -Urpf --mount-proc`).
# ---------------------------------------------------------------------------


def unshare_available() -> tuple[bool, str]:
    """Whether ``unshare -Urpf --mount-proc true`` succeeds here, and why
    not when it does not (unprivileged user namespaces unavailable)."""
    try:
        result = subprocess.run(["unshare", "-Urpf", "--mount-proc", "true"],
                                capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"unshare could not run: {exc}"
    if result.returncode != 0:
        return False, f"unshare -Urpf --mount-proc true exited {result.returncode}: {result.stderr.strip()}"
    return True, ""


#: Runs as PID 1 of a fresh pid namespace: a forked taker acquires the
#: lifecycle lock, forks a holder that inherits the descriptor, and exits
#: when told; PID 1 reaps it. The probe is read before and after, and an
#: acquire is tried after.
_PID_NAMESPACE_PROBE = r'''
import json, os, sys
sys.path.insert(0, sys.argv[1])
from controller import lock
from controller.errors import LifecycleWorkerActiveError

target = sys.argv[2]
ready_r, ready_w = os.pipe()
taker_go_r, taker_go_w = os.pipe()
holder_go_r, holder_go_w = os.pipe()
taker = os.fork()
if taker == 0:
    held = lock.acquire_lifecycle_lock(target)
    holder = os.fork()
    if holder == 0:
        os.read(holder_go_r, 1)  # keeps the inherited descriptor until told
        os._exit(0)
    os.write(ready_w, f"{holder}\n".encode())
    os.read(taker_go_r, 1)
    os._exit(0)  # the taker dies; the holder still holds the lock
holder = int(os.read(ready_r, 64).decode().strip())
results = {"namespace": lock.read_pid_namespace(), "before": lock.probe_lifecycle_lock(target)}
os.write(taker_go_w, b"x")
os.waitpid(taker, 0)  # PID 1 reaps the taker
results["after"] = lock.probe_lifecycle_lock(target)
try:
    lock.acquire_lifecycle_lock(target).release()
    results["acquire_after"] = "acquired"
except LifecycleWorkerActiveError:
    results["acquire_after"] = "LifecycleWorkerActiveError"
os.write(holder_go_w, b"x")
os.waitpid(holder, 0)
print(json.dumps(results))
'''


def run_in_child_pid_namespace(target_root: Path) -> dict:
    """Run :data:`_PID_NAMESPACE_PROBE` against ``target_root`` inside
    ``unshare -Urpf --mount-proc`` and return its JSON result."""
    result = subprocess.run(
        ["unshare", "-Urpf", "--mount-proc", sys.executable, "-c", _PID_NAMESPACE_PROBE,
         str(REPO_ROOT), str(target_root)],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(f"the pid-namespace probe exited {result.returncode}: {result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


# ---------------------------------------------------------------------------
# A git repository on the checkout's own filesystem.
# ---------------------------------------------------------------------------


def scratch_git_repo(test_case: unittest.TestCase) -> Path:
    """A fresh git repository under the checkout's gitignored ``build/``,
    removed by ``test_case``'s cleanup."""
    BUILD_DIR.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="cp5-lock-", dir=BUILD_DIR))
    test_case.addCleanup(shutil.rmtree, root, True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root
