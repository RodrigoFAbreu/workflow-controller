"""Verifying a Codex build (``workflow-controller-verify-codex-command``, CP1).

:func:`verify_build` runs the C10g-1 probes (:mod:`controller.codex_sandbox`) against the launcher a launch would
run: the modelless ``codex sandbox`` form first, then one short ``codex exec`` turn. Each probe runs in its own
fresh job against host fixtures the sandbox could otherwise reach (a TCP and a Unix listener, a bare push origin, a
canary file), and what the run did to the host is read back from bytes and listings, never from git. Nothing is
recorded here: the caller records the returned digest. Every case the probes cannot decide is a
:class:`BuildNotVerified` naming the probe.
"""
from __future__ import annotations

import dataclasses
import hashlib
import os
import shutil
import socket
import stat
import subprocess
import tempfile
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from . import codex_build, codex_sandbox as cs, functional_review as fr, review, runtime
from .errors import ControllerError, ReviewLaunchRefusedError

PROBES = ("sandbox", "exec")
SANDBOX_TIMEOUT = 180
EXEC_TIMEOUT = 600
VERSION_TIMEOUT = 30
CANARY_ENV = "CONTROLLER_VERIFY_CANARY"
CANARY_VALUE = "canary-value-must-not-reach-the-sandbox"
SOCKET_NAME = "s.sock"
#: Words in a failed ``exec`` turn's stderr that say the model could not be reached (login, credit, limit).
_UNREACHABLE_WORDS = ("login", "log in", "credit", "limit", "authenticat", "unauthorized", "api key", "overloaded",
                      "429", "quota", "billing", *(name.lower() for name in review.LIMIT_ERRORS))


class BuildNotVerified(ControllerError):
    """A build was not shown to hold the boundary. ``probe`` names the failing check; ``inconclusive`` is a probe
    that could not decide (run it again), as opposed to one that found the boundary broken."""

    code = "codex_build_not_verified"

    def __init__(self, probe: str, message: str, *, inconclusive: bool = False, evidence: dict | None = None) -> None:
        super().__init__(f"{probe}: {message}", evidence=evidence)
        self.probe = probe
        self.inconclusive = inconclusive


class ModelUnreachable(BuildNotVerified):
    """The ``exec`` turn could not reach a model (login, credit, limit, timeout, no output): the build is neither
    verified nor shown broken."""

    def __init__(self, message: str) -> None:
        super().__init__("model_unreachable", message, inconclusive=True)


@dataclasses.dataclass(frozen=True)
class ProbeResult:
    name: str
    outcome: str
    detail: str

    def line(self) -> str:
        return f"probe {self.name}: {self.outcome} -- {self.detail}"


@dataclasses.dataclass(frozen=True)
class Verification:
    """A build whose every probe held: the launcher path, the digest taken before the first run, the version it
    reports and each probe."""

    path: str
    digest: str
    version: str
    probes: tuple[ProbeResult, ...]

    def probe_results(self) -> dict[str, str]:
        return {probe.name: probe.outcome for probe in self.probes}


# ---------------------------------------------------------------------------
# The host: a short folder, listeners, git before the run
# ---------------------------------------------------------------------------


def _short_folder() -> Path:
    """A private folder (0700) short enough for the host Unix socket, under the first of
    ``functional_review.short_tmp_roots()`` that fits."""
    problems = []
    for root in fr.short_tmp_roots():
        try:
            tmp = Path(tempfile.mkdtemp(prefix=fr.TMP_PREFIX, dir=root))
        except OSError as exc:
            problems.append(f"{root}: {exc.strerror or exc}")
            continue
        if len(os.fsencode(tmp / SOCKET_NAME)) > fr.SOCKET_PATH_LIMIT:
            runtime.remove_tree(root, tmp.name)
            problems.append(f"{root}: the socket path would be {len(os.fsencode(tmp / SOCKET_NAME))} bytes")
            continue
        return tmp
    raise BuildNotVerified("host", f"no short folder for the host listener (a socket path of at most "
                                   f"{fr.SOCKET_PATH_LIMIT} bytes): {'; '.join(problems)}")


class _Listeners:
    """A TCP listener on 127.0.0.1 and a Unix listener. A connection completes in the kernel's backlog, so
    :meth:`drain` finds every one without a thread."""

    def __init__(self, folder: Path) -> None:
        self.socket_path = folder / SOCKET_NAME
        self.unix = socket.socket(socket.AF_UNIX)
        self.unix.bind(str(self.socket_path))
        self.unix.listen(8)
        self.tcp = socket.socket()
        self.tcp.bind(("127.0.0.1", 0))
        self.tcp.listen(8)
        self.port = self.tcp.getsockname()[1]

    def drain(self) -> list[str]:
        hits = []
        for name, sock in (("unix", self.unix), ("tcp", self.tcp)):
            sock.setblocking(False)
            while True:
                try:
                    conn, _ = sock.accept()
                except OSError:
                    break
                conn.close()
                hits.append(name)
        return hits

    def close(self) -> None:
        self.unix.close()
        self.tcp.close()


def _git(args: Sequence[str], cwd: Path, stdin: bytes | None = None) -> None:
    """The only place git runs: before the sandboxed run, with no user or system configuration and no templates."""
    env = {"PATH": os.environ.get("PATH", review.SYSTEM_PATH), "HOME": str(cwd), "GIT_CONFIG_GLOBAL": "/dev/null",
           "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_TERMINAL_PROMPT": "0", "GIT_AUTHOR_NAME": "verify",
           "GIT_AUTHOR_EMAIL": "verify@example.invalid", "GIT_COMMITTER_NAME": "verify",
           "GIT_COMMITTER_EMAIL": "verify@example.invalid"}
    subprocess.run(["git", *args], cwd=cwd, env=env, check=True, capture_output=True, timeout=60,
                   input=stdin)


#: One commit holding ``src.txt``, in ``git fast-import``'s stream form: the clone gets its history without a commit.
_FAST_IMPORT = (b"commit refs/heads/main\ncommitter verify <verify@example.invalid> 0 +0000\ndata 4\ninit\n"
                b"M 100644 inline src.txt\ndata 9\noriginal\n\n")


def _make_clone(runtime_root: Path, clone: Path) -> None:
    clone.mkdir(parents=True, mode=0o700)
    _git(["init", "-q", "--template=", "-b", "main", str(clone)], clone)
    _git(["fast-import", "--quiet"], clone, stdin=_FAST_IMPORT)
    _git(["read-tree", "HEAD"], clone)
    runtime.write_bytes(runtime_root, clone.relative_to(runtime_root) / "src.txt", b"original\n")
    _git(["update-index", "--refresh"], clone)


def _make_origin(origin: Path) -> None:
    origin.mkdir(mode=0o700)
    _git(["init", "-q", "--bare", "--template=", str(origin)], origin)


# ---------------------------------------------------------------------------
# What the run did to the host (D6): listings and bytes, never git
# ---------------------------------------------------------------------------


def snapshot(root: Path) -> dict[str, tuple[str, str]]:
    """Every entry under ``root`` as relative path to ``(type, detail)``: a directory ``D``, a regular file ``F``
    with its SHA-256, a symlink ``L`` with its target text, anything else ``S``. Nothing is followed and no FIFO,
    socket or device is opened."""
    out: dict[str, tuple[str, str]] = {}
    stack = [("", root)]
    while stack:
        prefix, directory = stack.pop()
        with os.scandir(directory) as it:
            entries = sorted(it, key=lambda e: e.name)
        for entry in entries:
            rel = prefix + entry.name
            if entry.is_symlink():
                out[rel] = ("L", os.readlink(entry.path))
            elif entry.is_dir(follow_symlinks=False):
                out[rel] = ("D", "")
                stack.append((rel + "/", Path(entry.path)))
            elif entry.is_file(follow_symlinks=False):
                digest = hashlib.sha256()
                with open(entry.path, "rb") as fh:
                    while chunk := fh.read(1 << 20):
                        digest.update(chunk)
                out[rel] = ("F", digest.hexdigest())
            else:
                out[rel] = ("S", "")
    return out


def differences(before: dict[str, tuple[str, str]], after: dict[str, tuple[str, str]], label: str) -> list[str]:
    found = [f"{label}: {rel} was created" for rel in sorted(after.keys() - before.keys())]
    found += [f"{label}: {rel} was removed" for rel in sorted(before.keys() - after.keys())]
    found += [f"{label}: {rel} changed" for rel in sorted(before.keys() & after.keys()) if before[rel] != after[rel]]
    return found


def _nonempty_regular(path: Path) -> bool:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        info = os.fstat(fd)
        return stat.S_ISREG(info.st_mode) and info.st_size > 0
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# One probe
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _Job:
    """One probe's fresh job: built layout, the targets, and what the host looked like before the run."""

    name: str
    built: cs.Built
    command: str
    marker: str
    origin: Path
    standin: Path
    clone_before: dict[str, tuple[str, str]]
    origin_before: dict[str, tuple[str, str]]
    credential_digest: str


def _discard(runtime_root: Path, layout: cs.TesterLayout) -> None:
    """Remove a job folder (it may hold the credential copy), whatever state it is in."""
    try:
        runtime.remove_tree(runtime_root, layout.rel())
    except OSError:
        pass


def _prepare(name: str, *, binary: Path, runtime_root: Path, operator_home: Path, folder: Path,
             listeners: _Listeners) -> _Job:
    job_id = f"verify-{name}-{uuid.uuid4().hex[:8]}"
    layout = cs.TesterLayout(runtime_root, job_id)
    marker = "verify-" + uuid.uuid4().hex[:12]
    origin = folder / f"{name}.git"
    standin = layout.base / "standin"
    try:
        _make_clone(runtime_root, layout.clone)
        _make_origin(origin)
        standin.mkdir(mode=0o700)
        runtime.write_bytes(runtime_root, standin.relative_to(runtime_root) / "canary", b"canary\n")
        targets = cs.ProbeTargets(
            clone=layout.clone, scratch=layout.scratch, sibling=layout.base / "sibling", stand_in_home=standin,
            canary_file=standin / "canary", canary_env=CANARY_ENV, credential=layout.codex_home / "auth.json",
            push_origin=origin, tcp_port=listeners.port, socket_path=listeners.socket_path)
        command = cs.probe_command(targets, marker=marker)
        # The job's own ownership tag: a run's clean-up kills every process carrying it, so a shared one (the
        # default ``tester``) would also kill a concurrent verification or tester.
        built = cs.build(runtime_root=runtime_root, job_id=job_id, operator_home=operator_home, binary=binary,
                         ownership_tag=job_id, prompt=cs.probe_prompt(command) if name == "exec" else None,
                         extra_readable=[folder], inherited={**os.environ, CANARY_ENV: CANARY_VALUE})
        digest = hashlib.sha256((layout.codex_home / "auth.json").read_bytes()).hexdigest()
        return _Job(name, built, command, marker, origin, standin, snapshot(layout.clone), snapshot(origin), digest)
    except ReviewLaunchRefusedError as exc:
        _discard(runtime_root, layout)
        raise BuildNotVerified("credentials" if exc.evidence.get("check") == "credentials" else "launch_controls",
                               str(exc)) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        _discard(runtime_root, layout)
        raise BuildNotVerified("host", f"the {name} job could not be prepared: {exc}") from exc


def _host_failures(job: _Job, listeners: _Listeners) -> list[str]:
    layout = job.built.layout
    failures = differences(job.clone_before, snapshot(layout.clone), "the clone")
    failures += differences(job.origin_before, snapshot(job.origin), "the push origin")
    for path in (layout.clone / "probe-new-file", layout.base / "sibling", job.standin / "probe",
                 Path("/tmp") / job.marker, Path("/var/tmp") / job.marker):
        if os.path.lexists(path):
            failures.append(f"{path} exists")
    if not os.path.lexists(layout.scratch / "probe"):
        failures.append(f"the scratch write did not happen ({layout.scratch / 'probe'})")
    if not _nonempty_regular(layout.fake_logs / "gh.jsonl"):
        failures.append("the stand-in gh logged no call")
    hits = listeners.drain()
    if hits:
        failures.append(f"a host listener saw a connection ({', '.join(hits)})")
    return failures


def _tail(data: bytes, size: int = 400) -> str:
    return data.decode("utf-8", "replace").strip()[-size:]


def _exec_cause(result: review.RunResult) -> BuildNotVerified | None:
    """The refusal for an ``exec`` turn that did not complete, ``None`` for a clean exit. Read from the exit code and
    the stderr text only."""
    if result.timed_out:
        return ModelUnreachable(f"the exec turn timed out after {EXEC_TIMEOUT}s")
    if result.exit_code == 0:
        return None
    stderr = _tail(result.stderr, 2000)
    if any(word in stderr.lower() for word in _UNREACHABLE_WORDS):
        return ModelUnreachable(f"the exec turn failed with a login, credit or limit cause: {stderr[-300:]}")
    return BuildNotVerified("exec_failed", f"the exec turn exited {result.exit_code}: {stderr[-300:]}")


def _run_probe(name: str, job: _Job, listeners: _Listeners, run: Callable[..., Any], runtime_root: Path,
               operator_home: Path, report: Callable[[str], None]) -> ProbeResult:
    layout = job.built.layout
    try:
        try:
            cs.check_launch(layout, job.built.config_text, Path(job.built.argv[0]), job.built.environment, run=run)
        except ReviewLaunchRefusedError as exc:
            raise BuildNotVerified("launch_controls", f"{name}: {exc}") from exc
        listeners.drain()
        if name == "sandbox":
            result = run(cs.sandbox_probe_argv(layout, binary=Path(job.built.argv[0]), command=job.command),
                         cwd=layout.clone, env=dict(job.built.environment), timeout=SANDBOX_TIMEOUT)
            if result.timed_out:
                raise BuildNotVerified("sandbox", f"the sandbox run timed out after {SANDBOX_TIMEOUT}s")
            output = result.stdout.decode("utf-8", "replace")
            judged = cs.judge(output)
            if not judged and result.exit_code not in (0, None):
                judged = [f"the run exited {result.exit_code}: {_tail(result.stderr)}"]
        else:
            result = run(list(job.built.argv), cwd=layout.clone, env=dict(job.built.environment),
                         timeout=EXEC_TIMEOUT, stdin_text="")
            cause = _exec_cause(result)
            if cause is not None:
                raise cause
            try:
                output = layout.verdict.read_text(encoding="utf-8", errors="replace") if layout.verdict.is_file() else ""
            except OSError:
                output = ""
            if not output.strip():
                raise ModelUnreachable("the exec turn returned no output")
            judged = cs.judge(output)
        judged = judged + _host_failures(job, listeners)
        if judged:
            raise BuildNotVerified(name, "; ".join(judged))
        return ProbeResult(name, "pass", "every boundary held")
    finally:
        try:
            outcome = review.copy_back_credentials(layout.job, operator_home, job.credential_digest)
            report(f"credentials after the {name} probe: {outcome}")
        except OSError as exc:
            report(f"credentials after the {name} probe: copy-back failed ({exc})")
        try:
            runtime.remove_tree(runtime_root, layout.rel())
        except OSError as exc:
            report(f"the {name} job folder could not be removed: {exc}")


# ---------------------------------------------------------------------------
# The verification
# ---------------------------------------------------------------------------


def verify_build(binary: str | os.PathLike, *, runtime_root: Path, operator_home: Path,
                 run: Callable[..., Any] = review.run_bounded, probes: Sequence[str] | None = None,
                 which: Callable[[str], str | None] = shutil.which,
                 report: Callable[[str], None] = lambda message: None) -> Verification:
    """Run the probes of D4 against ``binary`` (the launcher path a launch would run, a symlink kept) and return the
    verification, or raise :class:`BuildNotVerified` naming the probe that failed. Records nothing. ``run`` launches
    ``codex`` (``review.run_bounded``'s signature); ``probes`` restricts the run to named probes."""
    binary = Path(binary)
    digest = codex_build.build_digest(binary)
    if digest is None:
        raise BuildNotVerified("identity", f"{binary} cannot be bound to a digest (an unsupported Codex layout)")
    if not (binary.is_file() and os.access(binary, os.X_OK)):
        raise BuildNotVerified("host", f"{binary} is not an executable codex")
    if which("bwrap") is None:
        raise BuildNotVerified("host", "bwrap is not installed")
    if not (Path(operator_home) / "auth.json").is_file():
        raise BuildNotVerified("credentials", f"no Codex credential file at {Path(operator_home) / 'auth.json'}")
    folder = _short_folder()
    listeners: _Listeners | None = None
    try:
        try:
            listeners = _Listeners(folder)
        except OSError as exc:
            raise BuildNotVerified("host", f"the host listeners could not be started: {exc}") from exc
        done: list[ProbeResult] = []
        version = "unknown"
        for name in PROBES:
            if probes is not None and name not in probes:
                continue
            job = _prepare(name, binary=binary, runtime_root=runtime_root, operator_home=operator_home,
                           folder=folder, listeners=listeners)
            if not done:
                version = _version(binary, job, run)
            done.append(_run_probe(name, job, listeners, run, runtime_root, operator_home, report))
        if codex_build.build_digest(binary) != digest:
            raise BuildNotVerified("build_changed", f"the build at {binary} changed while the probes ran")
        return Verification(path=str(binary), digest=digest, version=version, probes=tuple(done))
    finally:
        if listeners is not None:
            listeners.close()
        runtime.remove_tree(folder.parent, folder.name)


def _version(binary: Path, job: _Job, run: Callable[..., Any]) -> str:
    try:
        result = run([str(binary), "--version"], cwd=job.built.layout.base, env=dict(job.built.environment),
                     timeout=VERSION_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    text = result.stdout.decode("utf-8", "replace").strip() if result.exit_code == 0 else ""
    return text.splitlines()[0] if text else "unknown"
