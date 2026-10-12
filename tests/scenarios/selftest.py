"""Scenarios that exercise the kit itself (`selftest=True`): never part of
the catalogue the runner writes evidence for. ``tests/test_scenario_kit.py``
runs them through the real launcher and asserts what they observed."""

from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
from pathlib import Path

from tests.scenarios import ScenarioFailure, scenario

#: Run in every attack path (a shell child, a git hook, a ``python -E`` child, an
#: ``execve`` that replaces ``PYTHONPATH`` as the Controller's re-exec does).
ATTACK_CODE = r"""
import errno, json, os, shutil, socket, sys
standin, port, exe = sys.argv[1], int(sys.argv[2]), sys.argv[3]
out = {}
def code(call):
    try:
        call(); return "ok"
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno))
def connect(addr):
    s = socket.socket(); s.settimeout(2)
    try: s.connect(addr)
    finally: s.close()
def write():
    with open(os.path.join(standin, "attack-" + str(os.getpid())), "w") as fh: fh.write("x")
out["listener"] = code(lambda: connect(("127.0.0.1", port)))
out["external"] = code(lambda: connect(("192.0.2.1", 9)))
out["write"] = code(write)
out["exe_by_name"] = shutil.which(exe)
sys.stdout.write(json.dumps(out))
"""


def _attack(kit, how: str) -> dict:
    standin, port, exe = kit.params["standin"], str(kit.params["port"]), kit.params["exe"]
    py = sys.executable
    if how == "python-E":
        argv = [py, "-E", "-c", ATTACK_CODE, standin, port, exe]
        return json.loads(subprocess.run(argv, capture_output=True, text=True, check=True).stdout)
    if how == "shell":
        quoted = " ".join(__import__("shlex").quote(a) for a in (py, "-c", ATTACK_CODE, standin, port, exe))
        return json.loads(subprocess.run(["sh", "-c", quoted], capture_output=True, text=True, check=True).stdout)
    if how == "reexec":
        # The Controller's re-exec replaces PYTHONPATH with its snapshot directory and starts
        # a fresh interpreter: the attack runs before and after that execve.
        code = ("import os, sys, subprocess\n"
                "sys.stdout.write(subprocess.run([sys.executable, '-P', '-B', '-c', sys.argv[1]] + sys.argv[2:],"
                " capture_output=True, text=True, check=True, env={**os.environ, 'PYTHONPATH': '/nonexistent'}).stdout)")
        return json.loads(subprocess.run([py, "-B", "-c", code, ATTACK_CODE, standin, port, exe],
                                         capture_output=True, text=True, check=True).stdout)
    if how == "git-hook":
        from tests import fixtures
        repo = fixtures.git_init(kit.workdir / "hookrepo")
        hooks = repo / "hooks"
        hooks.mkdir()
        result = repo / "hook-result.json"
        script = hooks / "pre-commit"
        script.write_text("#!/bin/sh\n" + " ".join(__import__("shlex").quote(a) for a in (
            py, "-c", ATTACK_CODE, standin, port, exe)) + f" > {result}\n")
        script.chmod(0o755)
        kit.git("config", "core.hooksPath", str(hooks), cwd=repo)
        (repo / "f").write_text("f\n")
        kit.git("add", "f", cwd=repo)
        kit.git("commit", "-q", "-m", "hook", cwd=repo)
        return json.loads(result.read_text())
    raise ScenarioFailure(how)


@scenario("selftest-attacks", selftest=True)
def attacks(kit) -> None:
    for how in ("shell", "git-hook", "python-E", "reexec"):
        kit.observe(f"attack-{how}", _attack(kit, how))


@scenario("selftest-basic", selftest=True)
def basic(kit) -> None:
    """A scratch origin and clone, a commit, a normalised record digest."""
    origin, clone = kit.build_origin_and_clone()
    kit.assert_remote_in_scratch(clone)
    (clone / "a.txt").write_text("a\n")
    kit.git("add", "a.txt", cwd=clone)
    kit.git("commit", "-q", "-m", "scenario: add a", cwd=clone)
    kit.observe("subject", kit.git("log", "-1", "--format=%s", cwd=clone).strip())
    kit.observe("tip", kit.git("rev-parse", "HEAD", cwd=clone).strip())
    kit.record_digest("job-record", {
        "job_id": "job-ABCDEF123456", "status": "FINISHED", "started_at": "2026-01-01T00:00:00Z",
        "worker_process": {"pid": os.getpid()}, "target": str(kit.scratch / "target"),
        "target_head": kit.git("rev-parse", "HEAD", cwd=clone).strip()})
    kit.observe("isolation-visible-to-scenario-only", "n/a")


@scenario("selftest-capabilities", selftest=True)
def capabilities(kit) -> None:
    status = Path("/proc/self/status").read_text()
    cap_eff = int([line for line in status.splitlines() if line.startswith("CapEff")][0].split()[1], 16)
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    target = kit.params["protected"].encode()
    umount = libc.umount2(target, 0)
    umount_errno = ctypes.get_errno() if umount else 0
    remount = libc.mount(None, target, None, ctypes.c_ulong(32 | 4096), None)
    remount_errno = ctypes.get_errno() if remount else 0
    try:
        (Path(kit.params["protected"]) / "x").write_text("x")
        write = "ok"
    except OSError as exc:
        write = errno.errorcode[exc.errno]
    kit.observe("caps", {"cap_eff": cap_eff, "uid": os.getuid(),
                         "umount": errno.errorcode.get(umount_errno), "remount": errno.errorcode.get(remount_errno),
                         "write": write})


@scenario("selftest-loopback", selftest=True)
def loopback(kit) -> None:
    import socket
    import urllib.request
    from tests.fake_signalhub import FakeSignalHub
    hub = FakeSignalHub().start()
    url = hub.url
    try:
        with urllib.request.urlopen(urllib.request.Request(url + "/events", data=b"{}", method="POST"), timeout=3):
            pass
    except Exception as exc:  # noqa: BLE001 -- the answer code does not matter, reachability does
        kit.observe("loopback", type(exc).__name__ if not hasattr(exc, "code") else "answered")
    else:
        kit.observe("loopback", "answered")
    sock = socket.socket()
    sock.settimeout(2)
    try:
        sock.connect(("192.0.2.1", 9))
        other = "connected"
    except OSError as exc:
        other = errno.errorcode.get(exc.errno, "error")
    kit.observe("other-address", other)
    kit.observe("hub-requests", len(hub.requests))
    hub.stop()


@scenario("selftest-probes", selftest=True)
def probes(kit) -> None:
    from controller import lock, worker
    origin, clone = kit.build_origin_and_clone()
    kit.observe("own-namespace", bool(worker._proc_numbers_own_namespace(worker._proc_root())))
    kit.observe("lifecycle-lock-free", str(lock.probe_lifecycle_lock(clone)))
    held = lock.acquire_lifecycle_lock(clone)
    try:
        kit.observe("lifecycle-lock-held", str(lock.probe_lifecycle_lock(clone)))
    finally:
        release = getattr(held, "release", None) or getattr(held, "close", None)
        if release:
            release()


@scenario("selftest-violation", selftest=True)
def violation(kit) -> None:
    """Start an executable that is not in the scratch bin/: the detector refuses it."""
    try:
        subprocess.run([kit.params["real_exe"]], capture_output=True, check=False)
    except RuntimeError as exc:
        kit.observe("refused", str(exc))
        raise ScenarioFailure("the audit hook refused an executable outside the scratch bin/")


@scenario("selftest-leftover", selftest=True)
def leftover(kit) -> None:
    subprocess.Popen(["sleep", "37"], start_new_session=True)
    kit.observe("started", "sleep 37 in a new session")


@scenario("selftest-fails", selftest=True)
def fails(kit) -> None:
    kit.observe("before", 1)
    raise ScenarioFailure("deliberate")


@scenario("selftest-fails-loudly", selftest=True)
def fails_loudly(kit) -> None:
    """A failure whose message (an assertion that prints whole records) and observations exceed a pipe buffer."""
    kit.observe("bulk", "o" * (1 << 17))
    raise ScenarioFailure("loud " + "x" * (1 << 18) + " end-of-message")


def _step_child(kit, kill_at=None, trace=False, **orphan):
    from tests import test_lifecycle_orchestration as lo
    lc = kit.seed_lifecycle()
    lc.add(lo.MILESTONE_IMPLEMENT, lc.implement("CP1"))
    env = kit.worker_env(lc, **orphan)
    child = kit.child_controller(kit.controller_argv("step", root=lc.root), kill_at=kill_at, trace=trace, extra_env=env)
    return lc, child


@scenario("selftest-trace-lifecycle", selftest=True)
def trace_lifecycle(kit) -> None:
    """The durable-write inventory: one uninterrupted step, every runtime write primitive in order."""
    lc, child = _step_child(kit, trace=True, FAKE_CLAUDE_ORPHAN_SECONDS=2)
    out, err = child.communicate(timeout=120)
    kit.observe("exit", child.returncode)
    kit.observe("stderr", err.decode()[-300:])
    kit.observe("writes", [f"{w['write']} {w['rel']} {w['status']} worker={w['has_worker']}" for w in kit.trace_writes()])


@scenario("selftest-kill-point", selftest=True)
def kill_point(kit) -> None:
    """Scenario 4's kill/resume oracle for ``kit.params["point"]``, proven inside the sandbox: the child is killed at
    the named durable write, then `resume` reconciles what it left. Measured (not assumed): the worker's stdin anchor
    outlives the killed Controller and holds the lifecycle lock until a `resume` reconciles the job."""
    import signal
    from controller import lock
    from tests import test_lifecycle_orchestration as lo
    from tests.scenarios.kit import _ancestors, tagged_pids
    point = kit.params["point"]
    lc = kit.seed_lifecycle()
    lc.add(lo.MILESTONE_IMPLEMENT, lc.implement("CP1"))
    env = kit.worker_env(lc, FAKE_CLAUDE_ORPHAN_SECONDS=2)
    child = kit.child_controller(kit.controller_argv("step", root=lc.root), kill_at=point, extra_env=env,
                                 ready_file=lc.case_dir / "fake-ready")
    kit.wait_for_marker(point)
    kit.kill(child)

    def statuses() -> list:
        directory = kit.runtime_root / "jobs"
        return [json.loads(p.read_text())["status"] for p in sorted(directory.glob("*.json"))] if directory.is_dir() else []

    def cmdline(pid: int) -> str:
        try:
            return open(f"/proc/{pid}/cmdline", "rb").read().decode(errors="replace")
        except OSError:
            return ""

    def split() -> tuple[list, list]:
        alive = tagged_pids(str(kit.scratch), exclude=_ancestors())
        anchors = [pid for pid in alive if "stdin anchor" in cmdline(pid)]
        return anchors, [pid for pid in alive if pid not in anchors]

    end = __import__("time").monotonic() + 10
    while split()[1] and __import__("time").monotonic() < end:
        __import__("time").sleep(0.05)
    anchors, others = split()
    kit.observe("jobs-after-kill", statuses())
    kit.observe("workers-left-after-deadline", len(others))
    kit.observe("anchors-after-kill", len(anchors))
    kit.observe("lock-after-kill", str(lock.probe_lifecycle_lock(lc.root)))
    result = kit.cli(["resume", str(lc.root)])
    kit.observe("resume-exit", result.code)
    kit.observe("jobs-after-resume", statuses())
    anchors, others = split()
    kit.observe("anchors-after-resume", len(anchors))
    kit.observe("worker-invocations", len(lc.processes_file.read_text().splitlines()) if lc.processes_file.exists() else 0)
    for pid in anchors + others:  # the cleanup the scenario owns: an unreconciled anchor is not left behind
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


@scenario("selftest-env", selftest=True)
def environment(kit) -> None:
    """The built environment and the sanitised Git environment, as a descendant sees them."""
    origin, clone = kit.build_origin_and_clone()
    kit.observe("env-keys", sorted(os.environ))
    kit.observe("path", os.environ["PATH"])
    kit.observe("bin", sorted(os.listdir(kit.bin_dir)))
    kit.observe("hooks-path", kit.git("config", "--get", "core.hooksPath", cwd=clone).strip())
    kit.observe("head-lookup", bool(kit.git("rev-parse", "HEAD", cwd=clone).strip()))
    kit.observe("which-claude", subprocess.run(["sh", "-c", "command -v claude gh workflow-manager curl"],
                                               capture_output=True, text=True).stdout.split())


@scenario("selftest-unnormalised", selftest=True)
def unnormalised(kit) -> None:
    """A deliberately raw timestamp the normalisation does not see: two runs differ."""
    import time
    kit.observations.append({"name": "raw", "value": time.time_ns()})
