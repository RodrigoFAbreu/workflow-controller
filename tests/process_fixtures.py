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
- :func:`reap_recorded_workers` / :class:`ReapRecordedWorkersMixin`: the
  streaming-worker teardown guarantee (worker-lifecycle-ownership CP1).
- :func:`scratch_git_repo`: a git repository under the checkout's
  gitignored ``build/``, not ``/tmp`` -- on the development machine ``/tmp``
  is tmpfs, where ``st_dev`` equals the mount device, while the checkout is
  on btrfs, where it does not (round 2, I1).
"""

from __future__ import annotations

import ast
import collections
import json
import math
import os
import re
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


def build_dir() -> Path:
    """``BUILD_DIR``, created if a fresh checkout does not have it yet."""
    BUILD_DIR.mkdir(exist_ok=True)
    return BUILD_DIR

#: The most a process a test starts may live when a killed test could leave
#: it behind in a new session or group (leftover-lifetime plan D12). A new
#: long-lived helper takes this instead of re-introducing a 3,600 s sleep.
LEFTOVER_LIFETIME_SECONDS = 600


def shell_release_wait(release: "str | os.PathLike", *, seconds: float = LEFTOVER_LIFETIME_SECONDS,
                       poll: float = 0.05) -> str:
    """A bash fragment that waits for ``release`` to exist for at most
    ``seconds`` (``$SECONDS``, monotonic, counted from the loop's start) and
    otherwise exits non-zero, so the commands after it never run and a
    timeout cannot look like a release. The loop ends with no help from the
    test that was meant to open it (plan D13)."""
    import shlex
    end = math.ceil(seconds)
    return (f"end=$((SECONDS+{end})); while [ ! -e {shlex.quote(str(release))} ]; do "
            f"[ $SECONDS -lt $end ] || exit 1; sleep {poll:g}; done")


def python_release_wait(release_expr: str, *, seconds: float = LEFTOVER_LIFETIME_SECONDS,
                        poll: float = 0.01) -> str:
    """Python source (for a ``-c`` script) that waits for the path
    ``release_expr`` names, for at most ``seconds`` from the loop's start
    (``time.monotonic()``), then carries on (plan D13)."""
    return (f"_end = time.monotonic() + {seconds!r}\n"
            f"while not os.path.exists({release_expr}) and time.monotonic() < _end: time.sleep({poll!r})\n")


#: A prefix for the ``-c`` script of a child Controller that cannot re-exec
#: (it pins its identity before ``cli.main``, or never imports ``cli``): once
#: its parent differs from the one it started under, the test that started it
#: is dead and it exits (leftover-lifetime plan D11). ``SIGALRM`` is used by
#: no Controller code, a handler that returns lets blocking calls retry
#: (PEP 475), and while the parent lives it does nothing. The constant ends
#: with a newline and is joined by plain concatenation, never by ``;``.
PARENT_GUARD = (
    "import os as _guard_os, signal as _guard_signal\n"
    "_GUARD_PARENT = _guard_os.getppid()\n"
    "def _guard_check(*_ignored):\n"
    "    if _guard_os.getppid() != _GUARD_PARENT:\n"
    "        _guard_os._exit(0)\n"
    "_guard_signal.signal(_guard_signal.SIGALRM, _guard_check)\n"
    "_guard_signal.setitimer(_guard_signal.ITIMER_REAL, 1, 1)\n"
)


def bounded_controller_argv(argv, seconds: float = LEFTOVER_LIFETIME_SECONDS) -> list[str]:
    """``argv`` behind GNU ``timeout -s KILL <seconds>``: a separate process
    that keeps counting down after the test that started the child has been
    killed, and that survives the child's own ``execve`` (the snapshot
    re-exec keeps the pid). It signals only its child and the group it makes
    for it, never the test's (plan D14)."""
    return ["timeout", "-s", "KILL", f"{seconds:g}", *[str(part) for part in argv]]


def kill_bounded(proc: subprocess.Popen, sig: int = signal.SIGKILL) -> None:
    """Signal a ``popen_bounded`` child's group (the ``timeout`` wrapper and
    the child), tolerating one that is already gone. ``SIGKILL`` cannot be
    forwarded by the wrapper, so ``proc.kill()`` alone would orphan the child
    and remove its one deadline."""
    try:
        os.killpg(proc.pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


class BoundedController(subprocess.Popen):
    """A ``Popen`` whose handle is the ``timeout`` wrapper of
    :func:`bounded_controller_argv`: ``kill()`` and ``terminate()`` go through
    the group so the child ends with the wrapper; ``send_signal(SIGINT)``
    stays a plain signal to the wrapper, which forwards it."""

    def kill(self) -> None:
        if self.returncode is None:
            kill_bounded(self, signal.SIGKILL)

    def terminate(self) -> None:
        if self.returncode is None:
            kill_bounded(self, signal.SIGTERM)


def popen_bounded(argv, seconds: float = LEFTOVER_LIFETIME_SECONDS, **popen_kwargs) -> BoundedController:
    """Start ``argv`` under an independent deadline (plan D14)."""
    return BoundedController(bounded_controller_argv(argv, seconds), **popen_kwargs)


# ---------------------------------------------------------------------------
# The enumerated inventory of survivor shapes (leftover-lifetime plan D13).
# ---------------------------------------------------------------------------

_WAITED_STARTS = frozenset({"run", "call", "check_call", "check_output"})
_HELPER_STARTS = frozenset({"spawn_sleeper", "popen_bounded", "run_controller_module"})
_CONTROLLER_MARKERS = ("controller", "_child_", "console_script", "str(script)")
_LOOP_TEXT = re.compile(r"\[ ! -e|while not os\.path\.exists|while True|\bread\s+(?:-\w+\s+)*\w+\s*<")
_BOUND_TEXT = re.compile(r"SECONDS|monotonic|deadline")
_SLEEP_TEXT = re.compile(r"sleep\W{1,3}(\d+)")


def _call_parts(node: ast.Call) -> tuple[str | None, str | None]:
    func = node.func
    if isinstance(func, ast.Attribute):
        owner = func.value
        return (owner.id if isinstance(owner, ast.Name) else owner.attr if isinstance(owner, ast.Attribute)
                else None), func.attr
    if isinstance(func, ast.Name):
        return None, func.id
    return None, None


def _imported_subprocess_names(tree: ast.AST) -> frozenset[str]:
    """The ``_WAITED_STARTS`` names a module imports from ``subprocess`` by name
    (``from subprocess import check_call``), so a bare call of one is a start."""
    return frozenset(alias.asname or alias.name for node in ast.walk(tree)
                     if isinstance(node, ast.ImportFrom) and node.module == "subprocess"
                     for alias in node.names if alias.name in _WAITED_STARTS)


def _literal_argv(node: ast.Call) -> bool:
    """Whether the call's argv is written out as a list or tuple (not built by a helper or passed as a name)."""
    first = node.args[0] if node.args else None
    return isinstance(first, (ast.List, ast.Tuple)) and bool(first.elts)


def _start_form(node: ast.Call, imported: frozenset[str] = frozenset()) -> str | None:
    """The inventory form of a call that starts a process, whatever its
    argv, or ``None`` for a call that starts none or cannot outlive a test
    (a ``subprocess.run`` of ``git``, or with a ``timeout=`` that is not a
    Controller: a ``timeout=`` stops being enforced when the test is killed,
    so it never bounds a Controller child, D12/D13). A ``timeout=`` call
    is skipped only when its argv is a literal that names no Controller; one
    whose argv is built elsewhere is classified, because the scan cannot see
    what it starts. ``imported`` holds the ``subprocess`` names the module
    imports by name, whose bare calls are starts too."""
    owner, name = _call_parts(node)
    if name is None:
        return None
    if name in _HELPER_STARTS:
        form = "helper:" + name
    elif (owner == "subprocess" and name in _WAITED_STARTS) or (owner is None and name in imported):
        first = node.args[0] if node.args else None
        if any(kw.arg == "timeout" for kw in node.keywords) and _literal_argv(node) \
                and not any(marker in ast.unparse(node).lower() for marker in _CONTROLLER_MARKERS):
            return None
        if isinstance(first, (ast.List, ast.Tuple)) and first.elts and isinstance(first.elts[0], ast.Constant) \
                and first.elts[0].value == "git":
            return None
        form = "run"
    elif name == "Popen" and owner in (None, "subprocess"):
        form = "Popen"
    elif owner == "os" and (name in ("fork", "forkpty", "system", "popen") or name.startswith(("exec", "spawn", "posix_spawn"))):
        form = "other"
    elif owner == "multiprocessing" and name == "Process":
        form = "other"
    else:
        return None
    if form in ("Popen", "run") and any(marker in ast.unparse(node).lower() for marker in _CONTROLLER_MARKERS):
        form += ":controller"
    return form


def scan_leftover_constructs(sources: "dict[str, str]") -> "dict[tuple[str, str], int]":
    """Count, per ``(file, kind)``, every construct that starts or holds a
    process beyond a test: ``start:<form>`` for each process start (the argv
    is never a filter), ``wait`` for a wait loop with no deadline, and
    ``sleep`` for a ``sleep`` literal above ``LEFTOVER_LIFETIME_SECONDS``."""
    counts: collections.Counter = collections.Counter()
    for name, source in sources.items():
        tree = ast.parse(source)
        imported = _imported_subprocess_names(tree)
        inside_f = {id(part) for node in ast.walk(tree) if isinstance(node, ast.JoinedStr)
                    for part in ast.walk(node) if part is not node}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                form = _start_form(node, imported)
                if form is not None:
                    counts[(name, "start:" + form)] += 1
                if _call_parts(node)[1] == "sleep" and node.args and isinstance(node.args[0], ast.Constant) \
                        and isinstance(node.args[0].value, (int, float)) \
                        and node.args[0].value > LEFTOVER_LIFETIME_SECONDS:
                    counts[(name, "sleep")] += 1
            elif isinstance(node, ast.While):
                text = ast.unparse(node.test)
                if ("exists" in text or "is_file" in text) and not _BOUND_TEXT.search(text):
                    counts[(name, "wait")] += 1
            elif (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in inside_f) \
                    or isinstance(node, ast.JoinedStr):
                text = node.value if isinstance(node, ast.Constant) else ast.unparse(node)
                if _LOOP_TEXT.search(text) and not _BOUND_TEXT.search(text):
                    counts[(name, "wait")] += 1
                for match in _SLEEP_TEXT.finditer(text):
                    if int(match.group(1)) > LEFTOVER_LIFETIME_SECONDS:
                        counts[(name, "sleep")] += 1
    return dict(counts)


DISPOSITIONS = frozenset({"bounded", "short", "parent-guard", "deadline", "watchdog", "exception"})


def check_inventory(sources: "dict[str, str]", inventory) -> list[str]:
    """Problems between the scan of ``sources`` and ``inventory`` (rows of
    ``(file, kind, count, disposition, reason)``): a match with no row, a row
    whose count differs, a stale row, an unknown disposition, a Controller
    child with a disposition that does not survive its test, and a long
    ``sleep`` or deadline-less wait loop that is not a named exception."""
    problems: list[str] = []
    found = scan_leftover_constructs(sources)
    rows: dict[tuple[str, str], tuple] = {}
    for file, kind, count, disposition, reason in inventory:
        if (file, kind) in rows:
            problems.append(f"{file} {kind}: two rows")
        rows[(file, kind)] = (count, disposition, reason)
        if disposition not in DISPOSITIONS:
            problems.append(f"{file} {kind}: unknown disposition {disposition!r}")
        if not reason:
            problems.append(f"{file} {kind}: no reason")
        if kind.endswith(":controller") and disposition not in ("parent-guard", "deadline", "bounded", "short") \
                and not (disposition == "exception" and "opt-in" in reason):
            problems.append(f"{file} {kind}: a Controller child cannot be {disposition!r}")
        if kind.endswith(":controller") and disposition in ("bounded", "short") \
                and not re.search(r"--timeout|read-only|exits", reason):
            problems.append(f"{file} {kind}: a bounded Controller child names its --timeout or its read-only command")
        if kind in ("sleep", "wait") and disposition not in ("exception", "watchdog", "deadline", "parent-guard"):
            problems.append(f"{file} {kind}: only an exception, a watchdog, a deadline or a guard may hold one")
    for key, count in sorted(found.items()):
        if key not in rows:
            problems.append(f"{key[0]} {key[1]}: {count} match(es) and no inventory row")
        elif rows[key][0] != count:
            problems.append(f"{key[0]} {key[1]}: inventory says {rows[key][0]}, the scan finds {count}")
    for key in sorted(set(rows) - set(found)):
        problems.append(f"{key[0]} {key[1]}: stale inventory row (no match)")
    return problems


#: One row per survivor shape: ``(file, kind, count, disposition, reason)``
#: with the kinds of :func:`scan_leftover_constructs`. Dispositions: ``bounded``
#: (a numeric life, at most ``LEFTOVER_LIFETIME_SECONDS``, or a deadline the
#: process enforces itself), ``short`` (exits by itself at once, or is waited
#: by the test), ``parent-guard`` (a ``-c`` Controller child that exits when
#: its test process dies, D11), ``deadline`` (an independent ``timeout``
#: wrapper, D14), ``watchdog`` (the fake's own D2), ``exception`` (named,
#: with the reason).
LEFTOVER_INVENTORY = (
    ("fake_claude.py", "sleep", 1, "watchdog",
     "the fake's in-process hour-long sleep of FAKE_CLAUDE_SELF_TERM: the sleeper is the fake itself, which D2 ends; "
     "the FAKE_CLAUDE_HANG sleep has no literal (FAKE_CLAUDE_HANG_SECONDS, default an hour): D2 ends it too, and a "
     "test that disables the watchdog sets FAKE_CLAUDE_HANG_SECONDS"),
    ("fake_claude.py", "start:Popen", 4, "bounded",
     "the Bash task shell (the fake's group, D2); the monitor and hang-child sleeps (SUBPROCESS_SLEEP_SECONDS, D12); "
     "the detached orphan-burst helper (its gate wait gives up after FAKE_CLAUDE_ORPHAN_SECONDS, D6)"),
    ("fake_claude.py", "start:other", 3, "bounded",
     "the descendant fork of FAKE_CLAUDE_DESCENDANT and the burst's two forks: each lives its finite seconds/lifetime parameter"),
    ("fake_claude.py", "start:run", 1, "bounded",
     "the non-detached orphan burst, waited by the fake; its gate wait is bounded as D6"),
    ("fake_claude.py", "wait", 5, "watchdog",
     "the fake's own pause, hang-until-file and record-path loops end with the fake (D2); the burst gate loop has its own deadline (D6); "
     "the burst's shell FIFO read of its gate runs under `timeout --foreground` for the orphan lifetime (D6)"),
    ("fake_claude_review.py", "start:run", 1, "bounded",
     "the scripted shell command of a fake review: its timeout=30 is enforced by the fake, which ends with its test"),
    ("fake_codex.py", "start:run", 1, "bounded",
     "the scripted shell command of a fake exec: its timeout=20 is enforced by the fake, which ends with its test"),
    ("fake_codex.py", "start:Popen", 1, "bounded",
     "the scripted descendant of the exec behaviour: a 30 s sleep (the fake's own orphan watchdog ends its group too)"),
    ("fixtures.py", "start:run", 5, "short",
     "venv, pip and install runs the test waits for; each exits by itself"),
    ("fixtures.py", "start:run:controller", 1, "short",
     "run_controller_module: a read-only command (status, follow, --version) or a step/run with --timeout N <= 600 (D11)"),
    ("test_review_mode.py", "start:run", 1, "short",
     "the captures generator's --check run: a read-only comparison that exits by itself"),
    ("process_fixtures.py", "start:Popen", 2, "bounded",
     "spawn_sleeper (LEFTOVER_LIFETIME_SECONDS) and the ZombieGroup helper (its release wait has a total deadline)"),
    ("process_fixtures.py", "wait", 1, "exception",
     "the scan's own pattern text, not a loop"),
    ("scenarios/kit.py", "start:Popen", 2, "deadline",
     "the sandbox chain (unshare, setup, drop, worker) behind an independent timeout -s KILL (D14) and killed by group on its own "
     "timeout; the diagnostic-mode worker, waited with the same timeout and killed on it"),
    ("scenarios/kit.py", "start:helper:popen_bounded", 1, "deadline",
     "Kit.child_controller: the child Controller behind timeout -s KILL (D14), with the parent guard; the scenario kills it by group and Kit.close ends it"),
    ("scenarios/kit.py", "start:run", 1, "short", "unshare --version: exits at once"),
    ("scenarios/kit.py", "wait", 2, "deadline",
     "the monotonic-deadline polls for tagged processes to end and for a kill-point marker (10 s, a named failure otherwise)"),
    ("scenarios/sandbox_setup.py", "start:other", 1, "deadline",
     "the setup phase execs the dropped worker in place (same pid): the chain's timeout wrapper bounds it"),
    ("scenarios/selftest.py", "start:Popen", 1, "bounded",
     "the leftover selftest: a 30 s sleep in a new session that the kit's leftover check must find and kill"),
    ("scenarios/selftest.py", "start:run", 5, "short", "attack paths and git calls the scenario waits for; each exits"),
    ("test_run_scenarios.py", "start:run", 1, "deadline",
     "the runner CLI in a clone of the checkout: the scenarios it runs sit behind the kit's per-scenario timeout (D14) and the call waits for it"),
    ("test_scenarios.py", "start:run", 1, "short",
     "a python -c that imports the two suite wrappers and prints which scenario modules they loaded: no scenario runs, it exits at once"),
    ("test_scenario_kit.py", "start:helper:popen_bounded", 2, "deadline",
     "the kill-point wrapper unit tests (reached and not-ready): a child blocked at a durable write behind timeout -s KILL (D14), "
     "killed by group by the test"),
    ("test_buildinfo.py", "start:run:controller", 1, "short",
     "a python -P -c import check in a venv; read-only and exits at once"),
    ("test_cli.py", "start:Popen", 1, "short", "python -c pass: exits at once"),
    ("test_cli.py", "start:run:controller", 2, "bounded",
     "a read-only `follow` that exits (its timeout= is no bound, D12), and the guarded _CHILD_RUN step with --timeout 30"),
    ("test_cli.py", "start:Popen:controller", 2, "parent-guard",
     "_CHILD_RUN is a pinned -c script (no re-exec) and takes the parent guard (D11); the second start is RunParentGuardTest's intermediate"),
    ("test_cli.py", "start:helper:run_controller_module", 3, "short",
     "read-only follow/--workflow-manager commands that exit, waited by the test"),
    ("test_cli.py", "start:helper:spawn_sleeper", 7, "bounded", "spawn_sleeper: LEFTOVER_LIFETIME_SECONDS"),
    ("test_review_job.py", "start:Popen", 1, "bounded",
     "ReplayTest's Codex stand-in: a 120 s sleep in its own session carrying the ownership tag; `resume` ends its group, "
     "and the test's cleanup kills it by exact pid"),
    ("test_review_same_provider.py", "start:Popen", 1, "bounded",
     "ClaudeRecoveryTest's Claude stand-in: a 120 s sleep in its own session carrying the ownership tag; `resume` ends "
     "its group, and the test's cleanup kills it by exact pid"),
    ("test_fake_codex_contract.py", "start:Popen", 1, "bounded",
     "the intermediate launcher of the orphan test: it waits on a fake with hang_seconds 120 and orphan_seconds 1; the test kills it and ends the fake's group by exact pid"),
    ("test_fake_claude_contract.py", "start:Popen", 9, "bounded",
     "fakes (the watchdog, D2), the intermediate and launcher (a 600 s sleep), sleepers of 60 s, the bounded-starter intermediate; each test ends them by exact pid"),
    ("test_fake_claude_contract.py", "start:run", 1, "bounded",
     "the plain fake run to completion: the fake's own watchdog (D2) ends it, and the call carries timeout=60"),
    ("test_fake_claude_contract.py", "wait", 1, "exception",
     "a process-name pattern (the gate wait wrapped in `timeout --foreground 3`) the test searches for, not a loop"),
    ("test_fake_claude_contract.py", "start:Popen:controller", 1, "deadline",
     "the negative control: a bare Popen of a bounded_controller_argv wrapper, ended by exact pid (D14)"),
    ("test_fake_claude_contract.py", "start:helper:popen_bounded", 4, "deadline",
     "stand-in Controllers behind timeout -s KILL (D14); each test ends them by exact pid"),
    ("test_fake_claude_contract.py", "start:helper:spawn_sleeper", 1, "bounded",
     "spawn_sleeper: LEFTOVER_LIFETIME_SECONDS (the end_pid test)"),
    ("test_forge.py", "start:run", 2, "short", "a forge/gh stub run the test waits for; exits"),
    ("test_handoff.py", "start:helper:popen_bounded", 1, "deadline",
     "the installed console-script Controller paused on a sentinel runs behind timeout -s KILL (D14)"),
    ("test_integration_disposable_repo.py", "start:run:controller", 2, "exception",
     "opt-in real-Claude Controller steps, skipped unless the operator opts in; their timeout= is no bound (D12) and they run longer than 600 s"),
    ("test_integration_disposable_repo.py", "start:run", 1, "exception",
     "the opt-in live CLI helper with a built argv, skipped unless the operator opts in; its timeout= is no bound (D12)"),
    ("test_integration_disposable_repo.py", "start:Popen", 2, "exception",
     "opt-in real-Claude tests, skipped unless the operator opts in; they legitimately run longer than 600 s"),
    ("test_job.py", "start:helper:spawn_sleeper", 3, "bounded", "spawn_sleeper: LEFTOVER_LIFETIME_SECONDS"),
    ("test_lifecycle_orchestration.py", "start:Popen:controller", 3, "parent-guard",
     "_CHILD_STEP, _CHILD_CLI and _CHILD_WAITING_STEP are pinned -c scripts and take the parent guard (D11)"),
    ("test_lock.py", "start:Popen", 1, "bounded",
     "the _spawn wrapper: its callers start sleepers of LEFTOVER_LIFETIME_SECONDS or short helpers"),
    ("test_notify.py", "start:run", 1, "short", "a python -c snippet (the key never reaches a log); exits at once"),
    ("test_observation_equivalence.py", "start:helper:popen_bounded", 2, "deadline",
     "python -m controller children supervising a worker (controller and follower); popen_bounded (D14)"),
    ("test_observe.py", "sleep", 1, "exception",
     "a cmdline fixture string with an hour-long sleep, not a process"),
    ("test_observe.py", "start:Popen", 4, "short", "python -c pass: exits at once"),
    ("test_observe.py", "start:helper:spawn_sleeper", 13, "bounded", "spawn_sleeper: LEFTOVER_LIFETIME_SECONDS"),
    ("test_packaged_runtime.py", "start:run", 1, "short",
     "the installed console script with --timeout 60 against a stub manager and the fake Claude; it exits and the call waits for it"),
    ("test_packaged_runtime.py", "start:helper:popen_bounded", 1, "deadline",
     "the packaged console-script Controller paused with no --timeout; popen_bounded (D14)"),
    ("test_resume.py", "start:Popen", 5, "bounded",
     "a 60 s sleep, a lock holder with a finite seconds argument, python -c pass, the child of a lost-Controller helper, and the parent-guard test's intermediate (a 600 s sleep, ended by exact pid)"),
    ("test_resume.py", "start:Popen:controller", 1, "parent-guard",
     "_LOST_CONTROLLER is a pinned -c script and takes the parent guard (D9, D11)"),
    ("test_resume.py", "start:run:controller", 1, "short",
     "_CHILD_ABANDON is a pinned -c script that abandons a job record and exits"),
    ("test_resume.py", "start:helper:spawn_sleeper", 7, "bounded", "spawn_sleeper: LEFTOVER_LIFETIME_SECONDS"),
    ("test_resume.py", "wait", 1, "parent-guard",
     "_LOST_CONTROLLER's hang() loop ends with the guarded child (D9, D11)"),
    ("test_run_tests.py", "start:run", 3, "short",
     "tools/run_tests.py on a synthetic tree (plan, run, selection): it exits by itself and each call is waited with a timeout"),
    ("test_run_tests.py", "start:Popen", 2, "bounded",
     "a 300 s sleeper and a shard runner whose hanging test sleeps 600 s (the runner kills its shards)"),
    ("test_settings.py", "start:Popen", 1, "short", "a python -c snippet that exits"),
    ("test_settings.py", "start:run:controller", 1, "short",
     "a unittest child (matched by its _child_env name, not a Controller) that runs one test and exits"),
    ("test_test_shards.py", "start:Popen", 4, "bounded",
     "300 s sleepers, ended by exact pid, and a shard executor on a synthetic tree whose 600 s "
     "suites its lane guard ends with the executor (the test kills the executor otherwise), and the "
     "same executor under a subreaper wrapper that never reaps (60 s wait, then killed)"),
    ("test_test_shards.py", "wait", 2, "deadline", "the executor started by run_directly (30 s) and the 10 s wait on a killed child"),
    ("test_test_shards.py", "start:run", 1, "short", "a python -c probe the test waits for"),
    ("test_trunk_orchestration_e2e.py", "start:run", 1, "short", "a bash command the test waits for"),
    ("test_trunk_preflight.py", "start:Popen:controller", 1, "parent-guard",
     "_CHILD_CLI is a pinned -c script (D11); its cleanup already kills the Controller itself"),
    ("test_worker.py", "start:Popen", 15, "bounded",
     "sleepers of LEFTOVER_LIFETIME_SECONDS or 60 s or less, helpers that exit, and the leader/member scripts (LEFTOVER_LIFETIME_SECONDS)"),
    ("test_worker.py", "start:helper:spawn_sleeper", 13, "bounded", "spawn_sleeper: LEFTOVER_LIFETIME_SECONDS"),
)


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


def end_pid(pid: int | None) -> None:
    """End one process by its exact pid: ``SIGCONT`` (a stopped process would not act on the kill until
    continued) then ``SIGKILL``, ignoring one that is already gone. Unlike :func:`kill_group` it never
    reaches a group the pid happens to lead, so a recycled pid cannot take other processes with it."""
    if not isinstance(pid, int) or pid <= 1:
        return
    for sig in (signal.SIGCONT, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            return


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
    proc = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({LEFTOVER_LIFETIME_SECONDS})"],
                            start_new_session=True)

    def _cleanup() -> None:
        kill_group(proc.pid)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass

    test_case.addCleanup(_cleanup)
    assert wait_until(lambda: read_stat(proc.pid) is not None)
    return proc


def wait_fake_ready(ready_file: "str | os.PathLike", pid: int, on_waiting=None, *, timeout: float = 30.0) -> None:
    """Wait until the fake's watchdog readiness file (``FAKE_CLAUDE_ORPHAN_READY_FILE``)
    names ``pid``, so a test that orphans a fake on purpose never does it in
    the fake's startup window. ``on_waiting`` is called once, after the
    inputs are resolved and immediately before the first poll (a handshake
    for tests that order something against the fake's readiness). Raises
    ``AssertionError`` naming the file and pid when it never does."""
    ready = Path(ready_file)
    if on_waiting is not None:
        on_waiting()

    def named() -> bool:
        try:
            return ready.read_text().strip() == str(pid)
        except (OSError, ValueError):
            return False

    if not wait_until(named, timeout=timeout):
        raise AssertionError(f"the fake worker {pid} never announced readiness in {ready}")


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
# Test teardown for streaming workers (worker-lifecycle-ownership CP1,
# round 1's I7).
# ---------------------------------------------------------------------------

#: The environment variable carrying a worker's ownership tags, a
#: ``:``-separated list (plan C, step 1).
OWNERSHIP_VAR = "WORKFLOW_CONTROLLER_OWNERSHIP"


def _identity_matches(identity: dict) -> bool:
    """Whether ``identity`` (a ``WorkerProcess``-shaped dict) is still the
    same live process: its pid alive, not a zombie, with the recorded start
    ticks and, where recorded, this boot."""
    pid = identity.get("pid")
    if not isinstance(pid, int) or pid <= 1 or pid == os.getpid():
        return False
    stat = read_stat(pid)
    if stat is None or stat[0] in ("Z", "X", "x"):
        return False
    if identity.get("start_ticks") is not None and stat[2] != identity["start_ticks"]:
        return False
    boot_id = identity.get("boot_id")
    if boot_id is not None:
        try:
            if Path("/proc/sys/kernel/random/boot_id").read_text().strip() != boot_id:
                return False
        except OSError:
            return False
    return True


def tagged_pids(tag: str) -> list[int]:
    """Every live same-uid process whose readable ``environ`` carries
    ``tag`` in ``WORKFLOW_CONTROLLER_OWNERSHIP`` (never this process)."""
    found = []
    prefix = f"{OWNERSHIP_VAR}=".encode()
    for name in os.listdir("/proc"):
        if not name.isdigit() or int(name) == os.getpid():
            continue
        try:
            environ = Path(f"/proc/{name}/environ").read_bytes().split(b"\0")
        except OSError:
            continue
        for item in environ:
            if item.startswith(prefix) and tag.encode() in item[len(prefix):].split(b":"):
                stat = read_stat(int(name))
                if stat is not None and stat[0] not in ("Z", "X", "x"):
                    found.append(int(name))
                break
    return found


def reap_recorded_workers(runtime_root: "str | os.PathLike") -> list[int]:
    """Identity-checked ``SIGKILL`` of every worker, anchor and tagged
    process that a job record under ``runtime_root`` still names, and that is
    still alive; returns the pids signalled.

    Reads ``jobs/*.json``: ``worker_process``, ``worker_anchor``,
    ``worker_state.owned_processes`` (each a ``(pid, start_ticks, ...)``
    identity, killed only while it still matches) and ``ownership_tag`` (a
    tag scan). So a test that SIGKILLs its Controller never leaks an anchor
    onto the machine or into an outer lifecycle job."""
    killed: list[int] = []
    jobs = Path(runtime_root) / "jobs"
    if not jobs.is_dir():
        return killed
    for path in sorted(jobs.glob("*.json")):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict):
            continue
        identities = [record.get("worker_process"), record.get("worker_anchor")]
        worker_state = record.get("worker_state")
        if isinstance(worker_state, dict):
            identities += list(worker_state.get("owned_processes") or [])
        pids = [i["pid"] for i in identities if isinstance(i, dict) and _identity_matches(i)]
        tag = record.get("ownership_tag")
        if isinstance(tag, str) and tag:
            pids += tagged_pids(tag)
        for pid in dict.fromkeys(pids):
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append(pid)
            except (ProcessLookupError, PermissionError):
                pass
    return killed


def end_recorded_anchor(record: dict) -> bool:
    """Identity-checked ``SIGKILL`` of the stdin anchor ``record`` names
    (``worker_anchor``); whether it was signalled.

    Worker-lifecycle-ownership CP3: once its Controller is lost, a
    streaming worker that ends its turn keeps waiting on stdin, because its
    anchor keeps the pipe open (plan I4), until ``resume`` re-attaches
    (CP5). A test that releases an orphaned worker and waits for it to end
    ends its session the way an operator would, by ending the anchor: the
    worker then sees EOF and exits, and the anchor's copy of the lifecycle
    lock goes with it."""
    anchor = record.get("worker_anchor")
    if not isinstance(anchor, dict) or not _identity_matches(anchor):
        return False
    try:
        os.kill(anchor["pid"], signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return False
    return True


class ReapRecordedWorkersMixin:
    """Registers :func:`reap_recorded_workers` as a cleanup of the test for
    each runtime root it launches streaming workers under. Every test class
    that launches a streaming worker uses it."""

    def reap_workers_under(self, runtime_root: "str | os.PathLike") -> None:
        self.addCleanup(reap_recorded_workers, runtime_root)


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
            time.sleep(__LIFETIME__)
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
deadline = time.monotonic() + __LIFETIME__
while not os.path.exists(release_path) and time.monotonic() < deadline:
    time.sleep(0.02)
'''.replace("__LIFETIME__", str(LEFTOVER_LIFETIME_SECONDS))


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
    root = Path(tempfile.mkdtemp(prefix="cp5-lock-", dir=build_dir()))
    test_case.addCleanup(shutil.rmtree, root, True)
    from tests import fixtures

    fixtures.git_init(root)
    return root
