"""The scenario kit (`workflow-controller-automated-lifecycle-scenarios` CP1).

Two halves. The **host half** (``probe_isolation``, ``run_scenario``,
``snapshot_checkout``) launches a scenario worker through the two-phase
sandbox of D3 and proves afterwards that the real checkout did not change.
The **worker half** (:class:`Kit`, ``worker_main``) runs inside the sandbox:
it builds the scratch origin, clone, ``bin/`` of fakes and the built
environment, and gives the scenario ``observe``/``cli``/``child_controller``.

Nothing here touches ``controller/``: the kill-point wrapper and the audit
hook live in ``bootstrap_hooks.py`` and are loaded by the child's
``sitecustomize`` (D1, D7).
"""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest.mock
from pathlib import Path

from tests.scenarios import REGISTRY, REPO_ROOT, Result, ScenarioFailure

SCENARIO_DIR = Path(__file__).resolve().parent
SETUP_SCRIPT = SCENARIO_DIR / "sandbox_setup.py"
HOOKS_FILE = SCENARIO_DIR / "bootstrap_hooks.py"

#: Paths whose contents decide a scenario's outcome (D4, D3b).
CODE_PATHS = ("controller", "tests", "tools", "pyproject.toml", "setup.py")
#: Executables the fixtures use; the first three are required.
ALLOWED_TOOLS = ("git", "sh", "python3", "sleep", "timeout", "setsid", "env", "cat", "true", "false", "mkdir", "rm",
                 "cp", "mv", "ls", "touch", "ln", "head", "tail", "wc", "sort", "tee", "dirname", "basename",
                 "date", "id", "kill", "bash", "printf", "echo", "test", "chmod", "tr", "grep", "sed", "uname")
REQUIRED_TOOLS = ("git", "sh", "python3")
STEPS = ("user-namespace", "setup", "drop")
KILL_DEADLINE_SECONDS = 10


#: The durable writes scenario 4 kills around, as the inventory measured them (`selftest-trace-lifecycle`):
#: the Controller publishes the milestone binding with ``runtime.create_json`` (its last step is ``os.link``), the
#: initial run record with ``write_json`` (``runs/``), and the job record that records the spawned worker with the
#: first ``write_json`` of a ``jobs/`` record that is ``LAUNCHED`` and carries ``worker_process``. ``spawn`` is the
#: class: ``pre`` (no worker exists) or ``post`` (a worker exists; the kill waits for its readiness file).
KILL_POINTS = {
    f"{kind}-{when}": {"primitive": primitive, "pattern": pattern, "where": where, "occurrence": 1, "when": when,
                       "spawn": spawn}
    for kind, primitive, pattern, where, spawn in (
        ("binding", "create_json", r"^repositories/[^/]+/milestones/[^/]+\.json$", {}, "pre"),
        ("run", "write_json", r"^runs/[^/]+\.json$", {}, "pre"),
        ("job", "write_json", r"^jobs/[^/]+\.json$", {"status": "LAUNCHED", "has_worker": True}, "post"))
    for when in ("before", "after")
}


def isolation_gate(probe: "Probe", env: "dict") -> tuple[str, str]:
    """What the suite wrapper does with ``probe`` (D3): ``("run", "")`` when
    the sandbox works; otherwise ``("skip", reason)`` locally and
    ``("fail", reason)`` when ``CI`` is set, so a green CI run always means the
    namespace enforcement was exercised. ``reason`` names the failed step."""
    if probe.ok:
        return "run", ""
    reason = f"isolation unavailable at step {probe.step!r}: {probe.detail}"
    return ("fail" if env.get("CI") else "skip"), reason


class IsolationUnavailable(RuntimeError):
    """The sandbox cannot be established; ``step`` names the failed step."""

    def __init__(self, step: str, detail: str) -> None:
        super().__init__(f"isolation unavailable at step {step!r}: {detail}")
        self.step, self.detail = step, detail


class MetadataRefused(RuntimeError):
    """The checkout's Git metadata cannot be resolved or bound (D3)."""


# ---------------------------------------------------------------------------
# Sanitised Git environment and the real checkout's content-sensitive snapshot
# ---------------------------------------------------------------------------

def sanitised_git_env(base: "dict | None" = None, *, scratch: "Path | None" = None) -> dict:
    """``base`` without any ``GIT_*`` variable, plus the pinned set (D3, D9)."""
    env = {k: v for k, v in (os.environ if base is None else base).items() if not k.startswith("GIT_")}
    env.update({
        "GIT_CONFIG_GLOBAL": str(scratch / "gitconfig") if scratch else os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_AUTHOR_NAME": "Scenario", "GIT_AUTHOR_EMAIL": "scenario@example.invalid",
        "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
        "GIT_COMMITTER_NAME": "Scenario", "GIT_COMMITTER_EMAIL": "scenario@example.invalid",
        "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
    })
    return env


def _git(checkout: Path, *args: str, env: "dict | None" = None, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, "-C", str(checkout), *args],
        env=sanitised_git_env() if env is None else env, capture_output=True, check=False)
    if check and result.returncode:
        raise MetadataRefused(f"git {' '.join(args)} failed: {result.stderr.decode(errors='replace').strip()}")
    return result.stdout.decode(errors="replace")


def head_of(checkout: Path = REPO_ROOT) -> str:
    return _git(checkout, "rev-parse", "HEAD").strip()


def snapshot_checkout(checkout: Path = REPO_ROOT) -> dict:
    """HEAD, a digest of every ref, of the index entry by entry, and the
    sha256 of the *content* of every tracked and untracked non-ignored file
    under :data:`CODE_PATHS` (a deleted tracked file digests as absent)."""
    names = _git(checkout, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", *CODE_PATHS)
    content = hashlib.sha256()
    for name in sorted(set(filter(None, names.split("\0")))):
        path = checkout / name
        content.update(name.encode() + b"\0")
        try:
            if path.is_symlink():
                content.update(b"L" + os.readlink(path).encode())
            else:
                content.update(f"{path.stat().st_mode & 0o777:o}".encode() + hashlib.sha256(path.read_bytes()).digest())
        except OSError:
            content.update(b"absent")
    return {
        "head": head_of(checkout),
        "refs": hashlib.sha256(_git(checkout, "for-each-ref", "--format=%(refname) %(objectname)").encode()).hexdigest(),
        "index": hashlib.sha256(_git(checkout, "ls-files", "-s").encode()).hexdigest(),
        "content": content.hexdigest(),
    }


def resolve_git_metadata(checkout: Path) -> list[Path]:
    """The checkout's absolute Git directory and common directory that lie
    outside the checkout tree, each an existing directory (else
    :class:`MetadataRefused`, before anything runs)."""
    root = checkout.resolve()
    found: list[Path] = []
    for option in ("--absolute-git-dir", "--git-common-dir"):
        text = _git(checkout, "rev-parse", option).strip()
        path = Path(text) if os.path.isabs(text) else (checkout / text)
        try:
            path = path.resolve(strict=True)
        except OSError as exc:
            raise MetadataRefused(f"the Git metadata path {text!r} does not resolve: {exc}") from None
        if not path.is_dir():
            raise MetadataRefused(f"the Git metadata path {path} is not a directory")
        if root in path.parents or path == root:
            continue
        if path not in found:
            found.append(path)
    return found


def real_locations(checkout: Path, env: "dict | None" = None) -> list[Path]:
    """The real locations a scenario must not write (D3): the checkout, the
    Controller's config/state directories and the XDG overrides, the SignalHub
    directory, the invoker's runtime root and ``/tmp/workflow-controller-tests``."""
    env = dict(os.environ if env is None else env)
    home = Path(env.get("HOME") or Path.home())
    paths = [checkout.resolve(), home / ".config" / "workflow-controller", home / ".local" / "state" / "workflow-controller",
             home / ".config" / "signalhub", Path(tempfile.gettempdir()) / "workflow-controller-tests",
             Path("/tmp/workflow-controller-tests")]
    for var, leaf in (("XDG_CONFIG_HOME", "workflow-controller"), ("XDG_STATE_HOME", "workflow-controller"),
                      ("XDG_DATA_HOME", "workflow-controller")):
        if env.get(var):
            paths.append(Path(env[var]) / leaf)
    if env.get("SIGNALHUB_CONFIG_DIR"):
        paths.append(Path(env["SIGNALHUB_CONFIG_DIR"]))
    for var in ("WORKFLOW_CONTROLLER_HOME", "WORKFLOW_CONTROLLER_RUNTIME_DIR"):
        if env.get(var):
            paths.append(Path(env[var]))
    paths.append(checkout.resolve() / ".controller")
    return paths


#: Ancestors that are never made read-only to cover a missing real location.
_NEVER_PROTECT = {Path("/"), Path("/tmp"), Path("/home"), Path("/var"), Path("/run"), Path("/usr"), Path("/etc")}


def protection_plan(checkout: Path, env: "dict | None" = None, extra: "list[Path]" = ()) -> list[dict]:
    """``[{"path", "mode"}]``: each existing real location read-only; a
    missing one is covered through its nearest existing ancestor that is not
    a system directory, else (a missing top-level path) skipped."""
    plan: dict[str, str] = {}
    # The scratch tree is created in the temporary directory, so neither it nor an ancestor of it may be made read-only
    # to cover a missing real location (a per-shard TMPDIR that lacks `workflow-controller-tests`).
    temp = Path(os.path.realpath(tempfile.gettempdir()))
    candidates = [*real_locations(checkout, env), *resolve_git_metadata(checkout), *map(Path, extra)]
    for candidate in candidates:
        path = Path(os.path.abspath(candidate))
        while not path.exists() and path != path.parent:
            path = path.parent
        if path in _NEVER_PROTECT or not path.is_dir():
            continue
        resolved = path.resolve()
        if resolved == temp or resolved in temp.parents:
            continue
        plan[str(resolved)] = "ro"
    # An enclosing protected directory already covers a nested one.
    paths = sorted(plan, key=len)
    kept: list[str] = []
    for path in paths:
        if not any(path == k or path.startswith(k.rstrip("/") + "/") for k in kept):
            kept.append(path)
    return [{"path": p, "mode": "ro"} for p in kept]


# ---------------------------------------------------------------------------
# The two-phase launch and its whole-chain probe
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Chain:
    """The tools the launch chain uses, resolved from the invoker's ``PATH``
    before the scenario environment (whose ``PATH`` is only the scratch
    ``bin/``) is built. Tests replace pieces to stub a failing step."""
    unshare: str
    python: str = sys.executable
    setup_script: Path = SETUP_SCRIPT
    drop: "list[str] | None" = None
    version: str = ""

    @classmethod
    def resolve(cls) -> "Chain":
        unshare = shutil.which("unshare")
        if unshare is None:
            raise IsolationUnavailable("user-namespace", "unshare is not on PATH")
        version = subprocess.run([unshare, "--version"], capture_output=True, text=True, check=False).stdout.strip()
        return cls(unshare=unshare, version=version)


PROBE_CODE = r"""
import ctypes, errno, json, os, socket, sys
os.write(4, b"drop-ok\n")
out = {}
caps = [l.split()[1] for l in open("/proc/self/status") if l.startswith("CapEff")][0]
out["cap_eff"] = int(caps, 16)
out["uid"] = os.getuid()
def fails(call):
    try:
        call(); return None
    except OSError as exc:
        return errno.errorcode.get(exc.errno, str(exc.errno))
protected, scratch = sys.argv[1], sys.argv[2]
def write(path):
    with open(os.path.join(path, "x"), "w") as fh: fh.write("x")
out["protected_write"] = fails(lambda: write(protected))
out["scratch_write"] = fails(lambda: write(scratch))
libc = ctypes.CDLL(None, use_errno=True)
def umount():
    if libc.umount2(protected.encode(), 0) != 0:
        raise OSError(ctypes.get_errno(), "umount")
def remount():
    if libc.mount(None, protected.encode(), None, ctypes.c_ulong(32 | 4096), None) != 0:
        raise OSError(ctypes.get_errno(), "remount")
out["umount"] = fails(umount)
out["remount_rw"] = fails(remount)
def connect(addr):
    s = socket.socket(); s.settimeout(2)
    try: s.connect(addr)
    finally: s.close()
out["external"] = fails(lambda: connect(("192.0.2.1", 9)))
server = socket.socket(); server.bind(("127.0.0.1", 0)); server.listen(1)
out["loopback"] = fails(lambda: connect(("127.0.0.1", server.getsockname()[1])))
print(json.dumps(out))
"""


def _chain_argv(spec: dict, chain: Chain, worker: list[str]) -> list[str]:
    return [chain.unshare, "--user", "--map-root-user", "--net", "--mount", "--",
            chain.python, "-I", "-S", "-B", str(chain.setup_script), json.dumps(spec), "--", *worker]


def _launch(chain: Chain, *, scratch: Path, protect: list[dict], worker: list[str], env: dict, cwd: "Path | None",
            result_fd: "int | None" = None, timeout: float = 600.0, log_path: "Path | None" = None) -> dict:
    """Run ``worker`` through the chain. Returns ``{"returncode", "stages",
    "stdout", "stderr"}``. ``stages`` is the progress the status pipe saw."""
    status_r, status_w = os.pipe()
    uid, gid = os.getuid(), os.getgid()
    inherit = [status_w] + ([result_fd] if result_fd is not None else [])
    spec = {"scratch": str(scratch), "protect": protect, "status_fd": 4, "inherit_fds": [4, *([3] if result_fd is not None else [])],
            "cwd": str(cwd) if cwd else "/", "uid": uid, "gid": gid, "unshare": chain.unshare, "drop": chain.drop}
    # Pin the descriptor numbers the worker expects: 4 = status, 3 = result.
    mapping = {status_w: 4}
    if result_fd is not None:
        mapping[result_fd] = 3

    def preexec() -> None:
        # Copy every source out of the way first: a source may already be another's target.
        moved = {source: fcntl.fcntl(source, fcntl.F_DUPFD, 100) for source in mapping}
        for source, target in mapping.items():
            os.dup2(moved[source], target)
            os.set_inheritable(target, True)
        for copy in moved.values():
            os.close(copy)

    # The chain runs behind an independent `timeout -s KILL` (leftover-lifetime D14), resolved from the
    # invoker's PATH: a killed test run leaves no sandbox past its deadline.
    argv = [shutil.which("timeout") or "timeout", "-s", "KILL", f"{timeout + 15:g}", *_chain_argv(spec, chain, worker)]
    # Output goes to host-side files, not pipes: a leftover process inherits a pipe and would hold
    # `communicate()` open until it exits, hiding itself from the leftover check.
    out, err = tempfile.TemporaryFile(), tempfile.TemporaryFile()
    try:
        proc = subprocess.Popen(argv, env=env, cwd="/", stdout=out, stderr=err,
                                pass_fds=tuple({*mapping, *mapping.values()}), preexec_fn=preexec,
                                start_new_session=True)
    finally:
        os.close(status_w)
        if result_fd is not None:
            os.close(result_fd)
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    out.seek(0), err.seek(0)
    stdout, stderr = out.read(), err.read()
    out.close(), err.close()
    with os.fdopen(status_r, "rb") as fh:
        stages = fh.read().decode().split()
    return {"returncode": proc.returncode, "stages": stages, "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace")}


@dataclasses.dataclass(frozen=True)
class Probe:
    ok: bool
    step: "str | None"
    detail: str
    unshare: str = ""
    version: str = ""


_PROBE_CACHE: dict = {}


def probe_isolation(chain: "Chain | None" = None, *, cache: bool = True) -> Probe:
    """Run the **whole chain** in a throwaway directory and check, from inside
    the dropped process, that ``CapEff`` is 0, the uid is the invoker's, a
    protected write fails ``EROFS``, ``umount`` and ``remount,rw`` fail
    ``EPERM``, a non-loopback connection fails and loopback works. On failure
    the result names the step (``user-namespace``, ``setup`` or ``drop``)."""
    try:
        chain = chain or Chain.resolve()
    except IsolationUnavailable as exc:
        return Probe(False, exc.step, exc.detail)
    key = (chain.unshare, chain.python, str(chain.setup_script), tuple(chain.drop or ()))
    if cache and key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    base = Path(tempfile.mkdtemp(prefix="scn-probe-"))
    try:
        protected, scratch = base / "protected", base / "scratch"
        protected.mkdir()
        scratch.mkdir()
        env = {"PATH": os.devnull, "LC_ALL": "C"}
        outcome = _launch(chain, scratch=scratch, protect=[{"path": str(protected), "mode": "ro"}],
                          worker=[chain.python, "-I", "-S", "-B", "-c", PROBE_CODE, str(protected), str(scratch)],
                          env=env, cwd=None, timeout=60)
        probe = _judge(chain, outcome)
    finally:
        shutil.rmtree(base, ignore_errors=True)
    if cache:
        _PROBE_CACHE[key] = probe
    return probe


def _judge(chain: Chain, outcome: dict) -> Probe:
    stages = outcome["stages"]
    tail = (outcome["stderr"].strip().splitlines() or [""])[-1]
    kw = {"unshare": chain.unshare, "version": chain.version}
    if "userns" not in stages:
        return Probe(False, "user-namespace", f"the user-namespace launch failed: {tail}", **kw)
    if "setup-ok" not in stages:
        return Probe(False, "setup", f"the setup phase failed: {tail}", **kw)
    if "drop-ok" not in stages:
        return Probe(False, "drop", f"the capability drop failed: {tail}", **kw)
    try:
        checks = json.loads(outcome["stdout"].strip().splitlines()[-1])
    except (ValueError, IndexError):
        return Probe(False, "drop", f"the probe produced no result: {tail}", **kw)
    expected = {"cap_eff": 0, "uid": os.getuid(), "protected_write": "EROFS", "scratch_write": None, "umount": "EPERM",
                "remount_rw": "EPERM", "loopback": None}
    bad = {k: checks.get(k) for k, v in expected.items() if checks.get(k) != v}
    if checks.get("external") not in ("ENETUNREACH", "EPERM", "ENETDOWN"):
        bad["external"] = checks.get("external")
    if bad:
        step = "drop" if {"cap_eff", "uid", "umount", "remount_rw"} & set(bad) else "setup"
        return Probe(False, step, f"the isolation checks failed: {bad}", **kw)
    return Probe(True, None, "ok", **kw)


# ---------------------------------------------------------------------------
# Normalisation (D9) and the leftover check (D8)
# ---------------------------------------------------------------------------

_TIME_KEYS = re.compile(r"(_at|_ticks|_pid|pid|pgid|ppid)$|^(ts|started|ended|reset_at|duration|elapsed|owner_token|token|nonce)$")
_SHA = re.compile(r"\b[0-9a-f]{40}\b")
_RANDOM_ID = re.compile(r"\b(?:job|run)-[0-9a-zA-Z_-]{6,}\b")


class Normaliser:
    """Replace what differs between two runs: scratch paths, every
    timestamp-ish field, pids, owner tokens, random identifiers; commit SHAs
    become ``<shaN>`` by order of first appearance."""

    def __init__(self, scratch: "str | Path" = "") -> None:
        self.scratch = str(scratch)
        self.shas: dict[str, str] = {}

    def text(self, value: str) -> str:
        if self.scratch:
            value = value.replace(self.scratch, "<scratch>")
        value = re.sub(r"scn-[a-z0-9_-]+-[A-Za-z0-9_]{6,}", "<scratch>", value)

        def sha(match):
            return self.shas.setdefault(match.group(0), f"<sha{len(self.shas) + 1}>")
        value = _SHA.sub(sha, value)
        return _RANDOM_ID.sub("<id>", value)

    def __call__(self, value):
        if isinstance(value, dict):
            return {k: ("<volatile>" if _TIME_KEYS.search(k) else self(v)) for k, v in sorted(value.items())}
        if isinstance(value, (list, tuple)):
            return [self(v) for v in value]
        if isinstance(value, str):
            return self.text(value)
        return value


def record_digest(record: dict, normaliser: "Normaliser | None" = None) -> str:
    """The digest of a job/run/binding record is computed over the
    **normalised** record, never the raw one (D9)."""
    normal = (normaliser or Normaliser())(record)
    return hashlib.sha256(json.dumps(normal, sort_keys=True).encode()).hexdigest()


def tagged_pids(tag: str, *, exclude: "set[int] | None" = None) -> list[int]:
    """Processes (any namespace-visible pid) whose command line or environment
    carries ``tag`` (the scenario's scratch path), except ``exclude`` and this process."""
    skip = {os.getpid(), *(exclude or ())}
    needle = tag.encode()
    found: list[int] = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) in skip:
            continue
        for name in ("cmdline", "environ"):
            try:
                if needle in Path(f"/proc/{entry}/{name}").read_bytes():
                    state = Path(f"/proc/{entry}/stat").read_text().rsplit(")", 1)[1].split()[0]
                    if state != "Z":
                        found.append(int(entry))
                    break
            except OSError:
                continue
    return found


def wait_no_tagged(tag: str, *, deadline: float = 10.0, exclude: "set[int] | None" = None) -> list[int]:
    """Poll until no tagged process remains or ``deadline`` seconds pass
    (monotonic); returns the survivors."""
    end = time.monotonic() + deadline
    while True:
        alive = tagged_pids(tag, exclude=exclude)
        if not alive or time.monotonic() >= end:
            return alive
        time.sleep(0.05)


# ---------------------------------------------------------------------------
# The diagnostic mode's leak scan (D3b)
# ---------------------------------------------------------------------------

def leak_scan(directories: "list[Path]", token: str, scratch: str, since: float) -> list[str]:
    """Entries under ``directories`` created or modified since ``since``
    whose name (or content, up to 1 MiB) carries the scenario's token or
    scratch path. Cannot see a token-free overwrite or deletion: a diagnostic
    aid, never the proof."""
    leaks: list[str] = []
    needles = (token.encode(), scratch.encode())
    for root in directories:
        for path in ([root] if root.is_file() else sorted(root.rglob("*")) if root.is_dir() else []):
            try:
                if path.stat().st_mtime < since:
                    continue
                if any(n.decode() in str(path) for n in needles):
                    leaks.append(str(path))
                elif path.is_file() and path.stat().st_size <= 1 << 20 and any(n in path.read_bytes() for n in needles):
                    leaks.append(str(path))
            except OSError:
                continue
    return leaks


# ---------------------------------------------------------------------------
# Running one scenario from the host
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Outcome:
    flow_id: str
    isolation: str  # "namespace" | "degraded"
    result: dict
    log: str
    head: str
    unchanged: bool
    problems: list[str]
    seconds: float

    @property
    def passed(self) -> bool:
        return self.result.get("status") == "passed" and self.unchanged and not self.problems


WORKER_CODE = ("import sys; sys.path.insert(0, sys.argv[1]); "
               "from tests.scenarios.kit import worker_main; worker_main(sys.argv[2:])")


def run_scenario(flow_id: str, *, mode: str = "namespace", checkout: Path = REPO_ROOT, chain: "Chain | None" = None,
                 timeout: float = 600.0, extra_protect: "list[Path]" = (), module: str = "",
                 params: "dict | None" = None) -> Outcome:
    """Run the registered scenario ``flow_id`` in its own sandbox process.

    ``mode="namespace"`` requires a passing probe (else
    :class:`IsolationUnavailable`: nothing runs); ``mode="degraded"`` is the
    diagnostic override and its outcome never becomes evidence."""
    if mode not in ("namespace", "degraded"):
        raise ValueError(mode)
    chain = chain or Chain.resolve() if mode == "namespace" else chain
    if mode == "namespace":
        probe = probe_isolation(chain)
        if not probe.ok:
            raise IsolationUnavailable(probe.step or "user-namespace", probe.detail)
    plan = protection_plan(checkout, extra=extra_protect) if mode == "namespace" else []
    resolve_git_metadata(checkout)  # refuse unresolvable metadata in either mode, before executing
    tools = {}
    for name in ALLOWED_TOOLS:
        found = shutil.which(name)
        if found:
            tools[name] = found
    missing = [t for t in REQUIRED_TOOLS if t not in tools]
    if missing:
        raise IsolationUnavailable("setup", f"required tools not on PATH: {missing}")
    before = snapshot_checkout(checkout)
    scratch = Path(tempfile.mkdtemp(prefix=f"scn-{flow_id}-"))
    token = scratch.name
    start_wall, start = time.time(), time.monotonic()
    env = build_env(scratch)
    config = {"flow_id": flow_id, "scratch": str(scratch), "tools": tools, "checkout": str(checkout.resolve()),
              "head": before["head"], "module": module, "isolation": mode, "params": params or {}}
    result_r, result_w = os.pipe()
    worker = [sys.executable, "-B", "-c", WORKER_CODE, str(checkout.resolve()), json.dumps(config)]
    problems: list[str] = []
    try:
        if mode == "namespace":
            outcome = _launch(chain, scratch=scratch, protect=plan, worker=worker, env=env, cwd=scratch,
                              result_fd=result_w, timeout=timeout)
        else:
            os.set_inheritable(result_w, True)
            status_r, status_w = os.pipe()
            with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
                proc = subprocess.Popen(worker, env=env, cwd=str(scratch), stdout=out, stderr=err,
                                        pass_fds=(result_w, status_w, 3, 4),
                                        preexec_fn=_degraded_preexec(result_w, status_w))
                os.close(status_w), os.close(status_r), os.close(result_w)
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
                out.seek(0), err.seek(0)
                outcome = {"returncode": proc.returncode, "stages": [], "stdout": out.read().decode(errors="replace"),
                           "stderr": err.read().decode(errors="replace")}
        with os.fdopen(result_r, "rb") as fh:
            raw = fh.read().decode().strip()
    except BaseException:
        shutil.rmtree(scratch, ignore_errors=True)
        raise
    try:
        result = json.loads(raw.splitlines()[-1]) if raw else {
            "flow_id": flow_id, "status": "failed", "observations": [],
            "message": f"the worker returned no result (exit {outcome['returncode']}): "
                       f"{outcome['stderr'].strip().splitlines()[-1:] or ''}"}
    except ValueError:
        result = {"flow_id": flow_id, "status": "failed", "observations": [], "message": "unreadable worker result"}
    survivors = wait_no_tagged(token)
    for pid in survivors:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)
    if survivors:
        problems.append(f"leftover processes tagged with {token}: {survivors}")
    if mode == "degraded":
        real = [p for p in real_locations(checkout) if p != checkout.resolve()]
        leaks = leak_scan(real, token, str(scratch), start_wall)
        problems += [f"leaked into a real location: {leak}" for leak in leaks]
    after = snapshot_checkout(checkout)
    unchanged = before == after
    if not unchanged:
        problems.append("the real checkout changed: " + ", ".join(k for k in before if before[k] != after[k]))
    shutil.rmtree(scratch, ignore_errors=True)
    log = (outcome["stdout"] + outcome["stderr"])
    return Outcome(flow_id, mode if mode == "degraded" else "namespace", result, log, before["head"], unchanged, problems,
                   time.monotonic() - start)


def _degraded_preexec(result_w: int, status_w: int):
    def preexec() -> None:
        high = [fcntl.fcntl(fd, fcntl.F_DUPFD, 100) for fd in (result_w, status_w)]
        for copy, target in zip(high, (3, 4)):
            os.dup2(copy, target)
            os.set_inheritable(target, True)
            os.close(copy)
    return preexec


def build_env(scratch: Path) -> dict:
    """The sandbox environment is *built*, not filtered: an allow-list and
    nothing else (no proxy variable, ``SIGNALHUB_URL`` or credential)."""
    env = sanitised_git_env({}, scratch=scratch)
    env.update({
        "PATH": str(scratch / "bin"), "HOME": str(scratch / "home"), "XDG_CONFIG_HOME": str(scratch / "home" / ".config"),
        "XDG_STATE_HOME": str(scratch / "home" / ".local" / "state"),
        "XDG_DATA_HOME": str(scratch / "home" / ".local" / "share"), "XDG_CACHE_HOME": str(scratch / "home" / ".cache"),
        "TMPDIR": str(scratch / "tmp"), "LC_ALL": "C", "PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1",
        "SCENARIO_SCRATCH": str(scratch),
    })
    return env


# ---------------------------------------------------------------------------
# The worker half (inside the sandbox)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class CliRun:
    code: int
    stdout: str
    stderr: str


class Kit:
    """What a scenario receives: scratch origin/clone, the fakes on ``PATH``,
    the built environment, observations and the Controller drivers."""

    def __init__(self, config: dict) -> None:
        self.config = config
        self.flow_id = config["flow_id"]
        self.scratch = Path(config["scratch"])
        self.checkout = Path(config["checkout"])
        self.head = config["head"]
        self.isolation = config["isolation"]
        self.params = config.get("params", {})
        self.observations: list = []
        self.home = self.scratch / "home"
        self.runtime_root = self.scratch / "runtime"
        self.workdir = self.scratch / "work"
        self.kill_dir = self.scratch / "kill"
        self.boot_dir = self.scratch / "boot"
        self.log_path = self.scratch / "audit.jsonl"
        self.bin_dir = self.scratch / "bin"
        self.norm = Normaliser(self.scratch)
        self._children: list[subprocess.Popen] = []
        for directory in (self.bin_dir, self.home, self.home / ".config", self.home / ".local" / "state",
                          self.runtime_root, self.workdir, self.scratch / "tmp", self.kill_dir, self.boot_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.tool_targets = self._build_bin(config["tools"])
        self.env = self._apply_env()
        (self.scratch / "gitconfig").write_text(
            "[core]\n\thooksPath = %s\n\tautocrlf = false\n[init]\n\tdefaultBranch = main\n"
            "[maintenance]\n\tauto = false\n[gc]\n\tauto = 0\n" % (self.scratch / "empty-hooks"))
        (self.scratch / "empty-hooks").mkdir(exist_ok=True)
        shutil.copyfile(HOOKS_FILE, self.boot_dir / "sitecustomize.py")
        self._audit_installed = False

    # --- environment ---------------------------------------------------------

    def _build_bin(self, tools: dict) -> set[str]:
        targets: set[str] = {os.path.realpath(sys.executable), sys.executable}
        for name, target in tools.items():
            link = self.bin_dir / name
            if not link.exists():
                link.symlink_to(target)
            targets.add(os.path.realpath(target))
        from tests import fixtures
        for name, source in (("claude", fixtures.REPO_ROOT / "tests" / "fake_claude.py"),
                             ("gh", fixtures.REPO_ROOT / "tests" / "fake_gh.py")):
            link = self.bin_dir / name
            if not link.exists():
                link.symlink_to(source)
        manager = self.bin_dir / "workflow-manager"
        if not manager.exists():
            fixtures.write_stub_workflow_manager(manager)
        return targets

    def _apply_env(self) -> dict:
        env = build_env(self.scratch)
        os.environ.clear()
        os.environ.update(env)
        tempfile.tempdir = None  # `gettempdir()` caches: forget a /tmp resolved before TMPDIR pointed at the scratch tree
        return env

    def boot_config(self, **extra) -> str:
        config = {"allowed": sorted(self.tool_targets), "bin": str(self.bin_dir), "log": str(self.log_path)}
        config.update(extra)
        return json.dumps(config)

    def install_audit_hook(self) -> None:
        """The detector for this interpreter (D7); a violation is also logged
        so a swallowed exception still fails the scenario."""
        if self._audit_installed:
            return
        spec = importlib.util.spec_from_file_location("_scenario_boot_hooks", HOOKS_FILE)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.install(json.loads(self.boot_config()))
        self._audit_installed = True

    def violations(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        return [e for e in map(json.loads, self.log_path.read_text().splitlines()) if "violation" in e]

    # --- repositories --------------------------------------------------------

    def git(self, *args: str, cwd: "Path | None" = None, check: bool = True) -> str:
        result = subprocess.run(["git", *args], cwd=cwd or self.workdir, capture_output=True, text=True, check=False)
        if check and result.returncode:
            raise ScenarioFailure(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def build_origin_and_clone(self, name: str = "target") -> tuple[Path, Path]:
        """A scratch bare origin and a clone of it, both under the scratch tree."""
        from tests import fixtures
        origin, clone = fixtures.build_origin_pair(self.workdir / name)
        for repo in (origin, clone):
            self.assert_inside_scratch(repo)
        return origin, clone

    def assert_inside_scratch(self, path: "Path | str") -> None:
        resolved = Path(os.path.realpath(path))
        if self.scratch.resolve() not in (resolved, *resolved.parents):
            raise ScenarioFailure(f"{path} resolves outside the scratch tree")

    def assert_remote_in_scratch(self, repo: Path) -> None:
        for line in self.git("remote", "-v", cwd=repo).splitlines():
            parts = line.split()
            if len(parts) > 1:
                self.assert_inside_scratch(parts[1])

    # --- observations --------------------------------------------------------

    def observe(self, name: str, value) -> None:
        self.observations.append({"name": name, "value": self.norm(value)})

    def record_digest(self, name: str, record: dict) -> str:
        digest = record_digest(record, self.norm)
        self.observe(name, {"digest": digest})
        return digest

    def check(self, condition: bool, message: str) -> None:
        if not condition:
            raise ScenarioFailure(message)

    def equal(self, actual, expected, what: str) -> None:
        if actual != expected:
            raise ScenarioFailure(f"{what}: expected {expected!r}, got {actual!r}")

    # --- a seeded lifecycle target ---------------------------------------------

    def seed_lifecycle(self, phase: str = "IMPLEMENTING", done: tuple = ()):
        """A committed ``"2.2"`` target under the scratch tree at ``phase``,
        modelled by ``tests.test_lifecycle_orchestration.Lifecycle`` (imported
        by its exact name, not the unrelated one of ``test_protocol_lifecycle``)."""
        from tests import fixtures
        from tests import test_lifecycle_orchestration as lo
        case_dir = self.workdir / "lifecycle"
        root = case_dir / "target"
        fixtures.build_target_git_repo(root)
        (root / "README.md").write_text("lifecycle fixture\n")
        (root / ".gitignore").write_text(".ai-review/\n")
        fixtures.write_installation_manifest(root)
        base = fixtures.commit_all(root, "initial")
        lc = lo.Lifecycle(case_dir, root.resolve(), base, phase=phase,
                          checkpoints={cid: {"status": "COMPLETE", "start_commit": base} for cid in done})
        fixtures.write_registry(root, lo.WI, lo.CHECKPOINT_IDS)
        (root / lo.STATE_REL).write_text(lc.state_text())
        fixtures.commit_all(root, "Seed the work item")
        return lc

    def worker_env(self, lc, **extra) -> dict:
        """The fake worker's scripted environment for ``lc``."""
        from tests import fixtures
        fixtures.write_worker_script(lc.script_path, lc.script)
        env = {"FAKE_CLAUDE_SCRIPT": str(lc.script_path), "FAKE_CLAUDE_INVOCATIONS_FILE": str(lc.processes_file),
               "FAKE_CLAUDE_ORPHAN_READY_FILE": str(lc.case_dir / "fake-ready")}
        env.update({k: str(v) for k, v in extra.items()})
        return env

    def controller_argv(self, command: str, *args: str, root: "Path | None" = None) -> list[str]:
        return ["--workflow-manager", str(self.bin_dir / "workflow-manager"), "--claude-binary", str(self.bin_dir / "claude"),
                "--timeout", "60", command, *args, *([str(root)] if root else [])]

    # --- driving the Controller ----------------------------------------------

    def _identity(self):
        from controller.identity import ControllerIdentity, SOURCE_KIND_COMMIT
        return ControllerIdentity(
            generation=1, source_root=self.checkout, origin_source_root=self.checkout, source_kind=SOURCE_KIND_COMMIT,
            source_commit=self.head, tree_digest="d" * 64, generation_source="head",
            pinned_at="2026-01-01T00:00:00Z", version="0.0.0-scenario")

    def cli(self, argv: list[str]) -> CliRun:
        """``cli.main`` in process with a pinned identity (so there is no
        re-exec), stdout/stderr captured."""
        from controller import cli, identity
        self.install_audit_hook()
        ident = self._identity()
        out, err = io.StringIO(), io.StringIO()
        with unittest.mock.patch.object(identity, "pin", return_value=ident), \
                unittest.mock.patch.object(identity, "current", return_value=ident), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--runtime-dir", str(self.runtime_root), *argv])
        return CliRun(code, out.getvalue(), err.getvalue())

    def child_controller(self, argv: list[str], *, kill_at: "dict | str | None" = None, trace: bool = False,
                         extra_env: "dict | None" = None, seconds: float = 120,
                         ready_file: "Path | None" = None) -> subprocess.Popen:
        """The Controller as a bounded child through the kit's bootstrap.

        ``kill_at`` is a name of :data:`KILL_POINTS` (or such a dict plus ``point``): the child blocks at that
        durable write, once the worker's ``ready_file`` exists for a post-spawn point, writing
        ``<scratch>/kill/<point>.reached`` until the scenario kills it."""
        from tests import process_fixtures
        kill = None
        if kill_at:
            if isinstance(kill_at, str):
                kill_at = dict(KILL_POINTS[kill_at], point=kill_at)
            kill = dict(kill_at, marker=str(self.kill_dir / f"{kill_at['point']}.reached"))
            if kill.get("spawn") == "post":
                if ready_file is None:
                    raise ScenarioFailure("a post-spawn kill point needs the worker's readiness file")
                kill["ready"] = str(ready_file)
        env = dict(self.env)
        env.update(extra_env or {})
        env["SCENARIO_BOOT_CONFIG"] = self.boot_config(kill=kill, trace=trace)
        env["PYTHONPATH"] = str(self.boot_dir)
        code = process_fixtures.PARENT_GUARD + _CHILD_MAIN
        argv_json = json.dumps({"argv": ["--runtime-dir", str(self.runtime_root), *argv],
                                "identity": dataclasses.asdict(self._identity()), "checkout": str(self.checkout)},
                               default=str)
        proc = process_fixtures.popen_bounded([sys.executable, "-B", "-c", code, argv_json], seconds=seconds, env=env,
                                              cwd=str(self.checkout), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                              start_new_session=True)
        self._children.append(proc)
        return proc

    def wait_for_marker(self, point: str, *, deadline: float = KILL_DEADLINE_SECONDS) -> None:
        """Block until ``<scratch>/kill/<point>.reached`` exists (the persistence-boundary handshake)."""
        marker = self.kill_dir / f"{point}.reached"
        end = time.monotonic() + deadline
        while not marker.exists():
            if time.monotonic() >= end:
                raise ScenarioFailure(f"kill point {point!r} was not reached within {deadline:g} s")
            time.sleep(0.02)

    def kill(self, proc: subprocess.Popen) -> None:
        from tests import process_fixtures
        process_fixtures.kill_bounded(proc, signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)

    def trace_writes(self) -> list[dict]:
        if not self.log_path.exists():
            return []
        return [e for e in map(json.loads, self.log_path.read_text().splitlines()) if "write" in e]

    def close(self) -> None:
        from tests import process_fixtures
        for proc in self._children:
            if proc.poll() is None:
                process_fixtures.kill_bounded(proc, signal.SIGKILL)
            with contextlib.suppress(Exception):
                proc.communicate(timeout=10)


_CHILD_MAIN = (
    "import json, sys\n"
    "_cfg = json.loads(sys.argv[1]); sys.path.insert(0, _cfg['checkout'])\n"
    "import unittest.mock\n"
    "from pathlib import Path\n"
    "from controller import cli, identity\n"
    "_ident = identity.ControllerIdentity(**{**_cfg['identity'], 'source_root': Path(_cfg['identity']['source_root']),\n"
    "    'origin_source_root': Path(_cfg['identity']['origin_source_root'])})\n"
    "with unittest.mock.patch.object(identity, 'pin', return_value=_ident), \\\n"
    "     unittest.mock.patch.object(identity, 'current', return_value=_ident):\n"
    "    sys.exit(cli.main(_cfg['argv']))\n"
)


def worker_main(argv: list[str]) -> None:
    """Entry of the sandboxed worker: progress to fd 4, result to fd 3."""
    config = json.loads(argv[0])
    with contextlib.suppress(OSError):
        os.write(4, b"drop-ok\n")
        for fd in (3, 4):  # no descendant may hold the result or status pipe open
            os.set_inheritable(fd, False)
    result = Result(config["flow_id"], "failed", [], "")
    kit = None
    try:
        kit = Kit(config)
        if config.get("module"):
            importlib.import_module(config["module"])
        else:
            importlib.import_module("tests.scenarios.catalogue")
        scenario = REGISTRY.get(config["flow_id"])
        if scenario is None:
            raise ScenarioFailure(f"no scenario is registered as {config['flow_id']!r}")
        kit.install_audit_hook()
        scenario.run(kit)
        if kit.violations():
            raise ScenarioFailure(f"the audit hook recorded a launch outside the scratch bin/: {kit.violations()}")
        survivors = wait_no_tagged(str(kit.scratch), exclude=_ancestors())
        if survivors:
            raise ScenarioFailure(f"leftover processes after the scenario: {survivors}")
        result.status = "passed"
    except BaseException as exc:  # noqa: BLE001 -- a failure is evidence, reported not raised
        result.message = f"{type(exc).__name__}: {exc}"
    finally:
        if kit is not None:
            kit.close()
            result.observations = kit.observations
    payload = json.dumps(dataclasses.asdict(result), sort_keys=True)
    with contextlib.suppress(OSError):
        os.write(3, payload.encode() + b"\n")


def _ancestors() -> set[int]:
    pids, pid = set(), os.getpid()
    while pid > 1:
        pids.add(pid)
        try:
            pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            break
    return pids
