"""The Codex worker sandbox (H4b1).

A Codex **worker** changes the job's worktree and commits there, and nothing else. The mechanism is the tester's
(:mod:`controller.codex_sandbox`): a restricted-read permissions profile in the worker's own ``CODEX_HOME/config.toml``
(``default_permissions = "worker"``), selected in place of ``-s``, with the worktree and a scratch area writable and the
network off. The tester's module is imported for the helpers that depend only on strings and paths and is never edited;
the helpers typed to its layout are written again here over :class:`WorkerLayout`. Nothing calls this module yet: the
launcher is H4b2 (see ``docs/ai-workflow/CONTROLLER_CODEX_WORKER_SANDBOX_PLAN.md``).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shlex
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from . import codex_sandbox, functional_review, review, runtime
from .errors import ReviewLaunchRefusedError

#: The worker's permissions profile name (``default_permissions``).
PROFILE = "worker"

#: Seconds a ``mcp list`` or ``features list`` control may take.
LISTING_TIMEOUT = codex_sandbox.LISTING_TIMEOUT

#: Flags no worker command line may carry: the tester's set (every way to select a sandbox other than the profile).
BANNED_FLAGS = codex_sandbox.BANNED_FLAGS


def _refuse(message: str, **evidence) -> ReviewLaunchRefusedError:
    return review._refuse(message, **evidence)


# ---------------------------------------------------------------------------
# The layout (D1)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class WorkerLayout:
    """Where one worker job's files live under ``<runtime>/worker/<job id>/``."""

    runtime_root: Path
    job_id: str

    @property
    def job(self) -> review.JobLayout:
        return review.JobLayout(self.runtime_root, self.job_id, area="worker")

    @property
    def base(self) -> Path:
        return self.job.base

    @property
    def worktree(self) -> Path:
        return self.job.tree

    @property
    def home(self) -> Path:
        return self.job.home

    @property
    def codex_home(self) -> Path:
        return self.job.codex_home

    @property
    def last_message(self) -> Path:
        return self.job.verdict.with_name("last-message.md")

    @property
    def bin(self) -> Path:
        return self.base / "bin"

    @property
    def scratch(self) -> Path:
        return self.base / "scratch"

    @property
    def scratch_tmp(self) -> Path:
        return self.scratch / "tmp"

    @property
    def fake_logs(self) -> Path:
        return self.scratch / "fake-logs"

    def rel(self, *parts: str) -> str:
        return self.job.rel(*parts)


def check_worktree(path: Path) -> None:
    """D2: ``path`` is a directory with a ``.git`` that is a real directory. A missing ``.git``, a symlink or a file
    (a linked worktree or a submodule keeps its objects and refs outside the tree, so a commit would need write
    access outside it) refuses, naming the cause."""
    if not path.is_dir() or path.is_symlink():
        raise _refuse(f"the worktree {path} is not a directory", check="worktree", entry=str(path))
    git = path / ".git"
    if git.is_symlink():
        raise _refuse(f"the worktree's .git is a symlink ({git})", check="worktree", entry=".git")
    if git.is_file():
        raise _refuse(f"the worktree's .git is a file, not a directory ({git}): a linked worktree or submodule "
                      "keeps its git directory outside the tree", check="worktree", entry=".git")
    if not git.is_dir():
        raise _refuse(f"the worktree has no .git directory ({git})", check="worktree", entry=".git")


# ---------------------------------------------------------------------------
# The readable roots (D3)
# ---------------------------------------------------------------------------


def check_roots(layout: WorkerLayout, roots: Sequence[Path], operator_home: Path | None = None) -> None:
    """The overlap refusal: no readable root may equal or contain the job's base, the worktree, the runtime root or the
    Codex home, lie inside the worktree, the scratch area or the Codex home, or be ``/``, the operator's real home or
    one of its ancestors."""
    home = operator_home if operator_home is not None else Path.home()
    protected = {"the job folder": layout.base, "the worktree": layout.worktree, "the runtime root": layout.runtime_root,
                 "the Codex home": layout.codex_home, "the operator's home": home}
    contains = codex_sandbox._contains
    forms = codex_sandbox._forms
    for root in roots:
        for form in forms(root):
            if form == Path("/"):
                raise _refuse(f"the readable root {root} is the filesystem root", check="roots", entry=str(root))
            for what, path in protected.items():
                if any(contains(form, p) for p in forms(path)):
                    raise _refuse(f"the readable root {root} contains {what} ({path})", check="roots", entry=str(root))
            for what, path in (("the worktree", layout.worktree), ("the scratch area", layout.scratch),
                               ("the Codex home", layout.codex_home)):
                if any(contains(p, form) for p in forms(path)):
                    raise _refuse(f"the readable root {root} lies inside {what}", check="roots", entry=str(root))


def readable_roots(layout: WorkerLayout, binary: Path, *, extra: Sequence[Path] = (), python: Path | None = None,
                   prefix: Path | None = None, base_prefix: Path | None = None, package_dir: Path | None = None,
                   system_dirs: Sequence[str] = codex_sandbox.SYSTEM_READ_DIRS, operator_home: Path | None = None
                   ) -> list[Path]:
    """Every directory the sandbox makes readable besides the worktree: system directories, the Codex install roots
    and interpreter, the stand-in interpreter roots, the Controller package directory, ``<base>/bin`` and ``extra``
    (test-only roots). De-duplicated, sorted, and refused by :func:`check_roots` when one overlaps the job."""
    roots = [Path(d) for d in system_dirs if Path(d).exists()]
    roots += codex_sandbox.codex_install_roots(binary)
    roots += codex_sandbox.interpreter_roots(python, prefix, base_prefix)
    roots += [package_dir if package_dir is not None else codex_sandbox.controller_package_dir(), layout.bin, *extra]
    unique = sorted({Path(os.path.abspath(root)) for root in roots})
    check_roots(layout, unique, operator_home)
    return unique


# ---------------------------------------------------------------------------
# The configuration (D3, D4)
# ---------------------------------------------------------------------------


def profile_toml(layout: WorkerLayout, roots: Sequence[Path]) -> str:
    """The ``worker`` permissions profile: ``:minimal``, the readable roots read, the worktree and the scratch area
    write, ``.git/hooks`` and ``.git/config`` read under the writable worktree (D4, a narrower entry the live test
    measures), the network off. Nothing of the operator's home is listed."""
    entries = {str(root): "read" for root in roots}
    entries[str(layout.worktree)] = "write"
    entries[str(layout.scratch)] = "write"
    entries[str(layout.worktree / ".git" / "hooks")] = "read"
    entries[str(layout.worktree / ".git" / "config")] = "read"
    quote = codex_sandbox._quote
    lines = [f'default_permissions = "{PROFILE}"', f"[permissions.{PROFILE}.filesystem]", '":minimal" = "read"']
    lines += [f"{quote(path)} = {json.dumps(access)}" for path, access in sorted(entries.items())]
    lines += [f"[permissions.{PROFILE}.network]", "enabled = false"]
    return "\n".join(lines) + "\n"


def config_toml(layout: WorkerLayout, roots: Sequence[Path]) -> str:
    """The worker's ``config.toml``: the reviewer's pinned lines plus the profile. Deterministic."""
    header = "# Written by the Controller for one worker job; never edited.\n"
    pinned = review.HOME_CONFIG_TOML.split("\n", 1)[1]
    return header + pinned + profile_toml(layout, roots)


def check_home(layout: WorkerLayout, config_text: str) -> None:
    """The startup-load check for the worker's home: ``config.toml`` is exactly ``config_text`` and every other
    entry is a pinned Codex-written state entry (the reviewer's rules, over its public constants)."""
    home = layout.codex_home
    config = home / "config.toml"
    try:
        digest = hashlib.sha256(config.read_bytes()).hexdigest()
    except OSError:
        digest = None
    if digest != hashlib.sha256(config_text.encode()).hexdigest():
        raise _refuse("the worker's Codex home config.toml differs from the one written", check="home",
                      entry="config.toml", source=str(config))
    for entry in sorted(home.iterdir(), key=lambda p: p.name):
        name = entry.name
        if name == "config.toml":
            continue
        if name in ("skills", "plugins"):
            for child in sorted(entry.iterdir(), key=lambda p: p.name):
                if f"{name}/{child.name}" not in review.CODEX_STARTUP_SUBENTRIES:
                    raise _refuse(f"the worker's Codex home holds the startup-loaded entry {name}/{child.name!r}",
                                  check="home", entry=f"{name}/{child.name}")
            continue
        if name in review.STARTUP_LOADED or name.startswith("hooks") or name.endswith(".toml"):
            raise _refuse(f"the worker's Codex home holds the startup-loaded entry {name!r}", check="home", entry=name)
        if name in review.CODEX_STATE_ENTRIES or name.endswith(review._STATE_DB_SUFFIXES):
            continue
        raise _refuse(f"the worker's Codex home holds the unexpected entry {name!r}", check="home", entry=name)


def check_launch(layout: WorkerLayout, config_text: str, binary: Path, env: Mapping[str, str], *,
                 system_dir: Path = review.SYSTEM_LAYER_DIR, run: Callable[..., review.RunResult] | None = None,
                 timeout: float = LISTING_TIMEOUT) -> None:
    """The pre-launch controls, in order, before every ``codex exec`` and ``codex sandbox`` run: the layers outside
    the home, then the home before each of ``mcp list`` and ``features list`` and once more after both (a listing
    writes Codex state into the home). Both listings run in the worktree. The first refusal stops the rest. The
    composed configuration is not checked here: it is H4b2's (D6)."""
    run = run or review.run_bounded
    review.check_outside_layers(system_dir)
    for kind in ("mcp", "features"):
        check_home(layout, config_text)
        command = review.argv(kind, binary=binary)
        review.check_argv(kind, command)
        result = run(command, cwd=layout.worktree, env=env, timeout=timeout)
        if result.exit_code != 0 or result.timed_out:
            raise _refuse(f"`{kind} list` did not run", check=kind, value=result.stderr.decode("utf-8", "replace")[:400])
        (review.check_mcp_list if kind == "mcp" else review.check_features)(result.stdout.decode("utf-8", "replace"))
    check_home(layout, config_text)


# ---------------------------------------------------------------------------
# The environment and the command line (D5, D6)
# ---------------------------------------------------------------------------


def child_environment(layout: WorkerLayout, binary: Path, ownership_tag: str, inherited: Mapping[str, str] | None = None
                      ) -> dict[str, str]:
    """The Codex process's environment: the reviewer's allowlist, with the scratch ``TMPDIR``."""
    env = review.child_environment(layout.job, binary, ownership_tag, inherited)
    env["TMPDIR"] = str(layout.scratch_tmp)
    return env


def command_environment(layout: WorkerLayout) -> dict[str, str]:
    """What the commands inside the sandbox see (``shell_environment_policy``): nothing inherited, no identity and
    no git configuration beyond the repository's own."""
    return {"PATH": f"{layout.bin}:{review.SYSTEM_PATH}", "HOME": str(layout.home), "TMPDIR": str(layout.scratch_tmp),
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


def shell_policy_overrides(layout: WorkerLayout) -> list[str]:
    """The two ``-c`` overrides that leave the sandboxed shell with only :func:`command_environment`."""
    items = ", ".join(f"{key}={codex_sandbox._string(value)}" for key, value in command_environment(layout).items())
    return ['shell_environment_policy.inherit="none"', f"shell_environment_policy.set={{{items}}}"]


def required_overrides(layout: WorkerLayout, *, model: str = review.DEFAULT_MODEL, effort: str = review.DEFAULT_EFFORT
                       ) -> tuple[str, ...]:
    """The complete ``-c`` set of a worker ``codex exec`` (D6)."""
    return (
        'model_provider="openai"', "notify=[]", 'web_search="disabled"', 'project_root_markers=[".git"]',
        f'model="{model}"', f'model_reasoning_effort="{effort}"', f'service_tier="{review.SERVICE_TIER}"',
        'approval_policy="never"', f'projects={{{codex_sandbox._quote(layout.worktree)}={{trust_level="untrusted"}}}}',
        "skills.bundled.enabled=false", f'default_permissions="{PROFILE}"', *shell_policy_overrides(layout),
    )


def argv(layout: WorkerLayout, *, binary: Path, model: str = review.DEFAULT_MODEL, effort: str = review.DEFAULT_EFFORT,
         prompt: str | None = None) -> list[str]:
    """The only constructor of a worker ``codex exec`` command line: the pinned flags with no ``-s`` and the profile
    selected by ``default_permissions``."""
    out = [str(binary), "exec", "--strict-config"]
    for feature in review.DISABLED_FEATURES:
        out += ["--disable", feature]
    for item in required_overrides(layout, model=model, effort=effort):
        out += ["-c", item]
    out += ["-C", str(layout.worktree), "--ignore-rules", "--color", "never", "-o", str(layout.last_message)]
    out.append(prompt if prompt is not None else "-")
    return out


def check_argv(command: Sequence[str], layout: WorkerLayout, *, model: str = review.DEFAULT_MODEL,
               effort: str = review.DEFAULT_EFFORT) -> None:
    """Refuse a worker command line that omits a pinned item, adds a ``-c`` outside :func:`required_overrides`,
    selects a sandbox with ``-s`` or carries a banned flag."""
    present_disables = {command[i + 1] for i, a in enumerate(command[:-1]) if a == "--disable"}
    for feature in review.DISABLED_FEATURES:
        if feature not in present_disables:
            raise _refuse(f"the command line does not disable the {feature!r} feature", check="argv", entry=feature)
    if "code_mode_host" in present_disables:
        raise _refuse("the command line disables the 'code_mode_host' tool", check="argv", entry="code_mode_host")
    present = [command[i + 1] for i, a in enumerate(command[:-1]) if a == "-c"]
    required = required_overrides(layout, model=model, effort=effort)
    for item in required:
        if item not in present:
            raise _refuse(f"the command line omits the pinned override {item!r}", check="argv", entry=item)
    for item in present:
        if item not in required:
            raise _refuse(f"the command line carries the unpinned override {item!r}", check="argv", entry=item)
    for arg in command[1:]:
        if arg in BANNED_FLAGS:
            raise _refuse(f"the command line carries {arg!r}", check="argv", entry=arg)
    for arg in command[1:-1]:
        if arg == "--config" or arg.startswith(("--config=", "--sandbox", "--add-dir=", "-c=")):
            raise _refuse(f"the command line carries the equivalent spelling {arg!r}", check="argv", entry=arg)

    def value_of(flag: str) -> str | None:
        return command[command.index(flag) + 1] if command.count(flag) == 1 and command.index(flag) + 1 < len(command) \
            else None

    for flag in ("--strict-config", "--ignore-rules"):
        if flag not in command:
            raise _refuse(f"the command line omits {flag}", check="argv", entry=flag)
    for flag, expected in (("-C", str(layout.worktree)), ("-o", str(layout.last_message)), ("--color", "never")):
        if value_of(flag) != expected:
            raise _refuse(f"the command line does not pin {flag} {expected}", check="argv", entry=flag)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Built:
    """What :func:`build` prepared: the layout, the files written and the command to launch."""

    layout: WorkerLayout
    config_text: str
    roots: tuple[Path, ...]
    argv: tuple[str, ...]
    environment: Mapping[str, str]
    #: The digest of the credential copy :func:`build` wrote (what a copy-back compares against).
    credential_digest: str = ""
    model: str = review.DEFAULT_MODEL
    effort: str = review.DEFAULT_EFFORT
    #: The binary ``argv`` was built with (as given, symlink kept).
    binary: str = ""


def build(*, runtime_root: Path, job_id: str, operator_home: Path, binary: Path, ownership_tag: str = "worker",
          model: str = review.DEFAULT_MODEL, effort: str = review.DEFAULT_EFFORT, prompt: str | None = None,
          system_dir: Path = review.SYSTEM_LAYER_DIR, package_parent: Path | None = None,
          extra_readable: Sequence[Path] = (), inherited: Mapping[str, str] | None = None,
          real_home: Path | None = None) -> Built:
    """Write the worker's home (credential copy, ``config.toml``), scratch area and stand-in launchers, run the two
    controls that start no process (outside layers, home) and return the command and environment. The worktree is
    the caller's and must satisfy :func:`check_worktree`. Nothing is written under the worktree and nothing is
    started. ``real_home`` is the operator's real home for the overlap refusal (default: the current user's)."""
    layout = WorkerLayout(runtime_root, job_id)
    review.check_outside_layers(system_dir)
    python = Path(sys.executable).resolve()
    package_dir = package_parent / "controller" if package_parent is not None else None
    roots = readable_roots(layout, binary, extra=extra_readable, python=python, package_dir=package_dir,
                           operator_home=real_home)
    config_text = config_toml(layout, roots)
    digest = review.build_home(layout.job, operator_home)
    runtime.write_private_bytes(runtime_root, layout.rel("codex-home", "config.toml"), config_text.encode())
    for directory in (layout.scratch_tmp, layout.fake_logs):
        directory.mkdir(parents=True, exist_ok=True)
    functional_review.write_launchers(runtime_root, layout.rel(), layout.fake_logs, python=str(python),
                                      package_parent=package_parent)
    check_home(layout, config_text)
    command = argv(layout, binary=binary, model=model, effort=effort, prompt=prompt)
    check_argv(command, layout, model=model, effort=effort)
    return Built(layout, config_text, tuple(roots), tuple(command),
                 child_environment(layout, binary, ownership_tag, inherited), digest, model, effort, str(binary))


# ---------------------------------------------------------------------------
# The probes (D7)
# ---------------------------------------------------------------------------

#: Each boundary the probe command attempts, and the result that means it held. ``PROBE <name> <RESULT>``.
PROBE_EXPECTED: Mapping[str, str] = {
    "tracked": "WROTE", "newfile": "WROTE", "rename": "WROTE", "delete": "WROTE", "commit": "COMMITTED",
    "scratch": "WROTE", "home": "REFUSED",
    "canary_file": "BLOCKED", "canary_env": "EMPTY", "credential": "BLOCKED",
    "push": "REFUSED", "gh_name": "STANDIN", "gh_path": "FAILED", "tcp": "BLOCKED", "unix": "BLOCKED",
}

#: Probes whose line must be present but whose result is informational: the ancestor-folder writes of the tester
#: (they report success on a private layer; the live test asserts the host path stays absent) and the two writes into
#: ``.git`` whose outcome is what the live test measures (D4); when one wrote, the guard digest must differ.
PROBE_INFORMATIONAL: Mapping[str, frozenset[str]] = {
    name: frozenset({"WROTE", "REFUSED"}) for name in ("tmp", "vartmp", "sibling", "hooks", "gitconfig")
}

_PROBE_LINE_RE = re.compile(r"^PROBE (?P<name>[a-z_]+) (?P<result>[A-Z0-9]+)$", re.MULTILINE)
_MARKER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

#: The files the probe command creates in the worktree for ``rename`` and ``delete`` (their own, never the tracked one).
PROBE_RENAME_SOURCE = "probe-rename-src"
PROBE_RENAME_TARGET = "probe-rename-dst"
PROBE_DELETE_FILE = "probe-delete-me"
PROBE_NEW_FILE = "probe-new-file"


@dataclasses.dataclass(frozen=True)
class ProbeTargets:
    """Where the probe command aims. Every path is absolute; ``push_origin`` is a bare repository the live test makes
    readable through an extra root. ``tracked`` is a file of the worktree that git tracks."""

    worktree: Path
    scratch: Path
    sibling: Path
    stand_in_home: Path
    canary_file: Path
    canary_env: str
    credential: Path
    push_origin: Path
    tcp_port: int
    socket_path: Path
    tracked: str = "src.txt"


def probe_command(targets: ProbeTargets, *, marker: str) -> str:
    """The one shell command both probe runs execute: a real attempt per boundary, every path absolute. Each line is
    ``PROBE <name> <RESULT>``; the command exits 0 whatever the attempts did. ``commit`` runs a real ``git add`` and
    ``git commit`` with an identity given by ``-c``; ``hooks`` and ``gitconfig`` run after it, so a refused or
    permitted write there cannot change the ``commit`` result, ``hooks`` writes a name no git command runs, and
    ``gitconfig`` appends a comment line to ``.git/config`` and never truncates it."""
    if not _MARKER_RE.match(marker):
        raise _refuse(f"the probe marker {marker!r} is not a plain name", check="probe", entry=marker)
    q = shlex.quote
    tree = targets.worktree
    git_dir = tree / ".git"
    tracked = tree / targets.tracked
    rename_src, rename_dst = tree / PROBE_RENAME_SOURCE, tree / PROBE_RENAME_TARGET
    delete_file = tree / PROBE_DELETE_FILE
    py_tcp = f"import socket;socket.create_connection(('127.0.0.1',{targets.tcp_port}),3)"
    py_unix = f"import socket;s=socket.socket(socket.AF_UNIX);s.settimeout(3);s.connect({str(targets.socket_path)!r})"
    identity = "-c user.name=probe -c user.email=probe@example.invalid -c commit.gpgsign=false"
    return "\n".join([
        # w truncates a path of its own; ap appends to a path that exists; neither is used on .git/config.
        'w() { if ( : > "$2" ) 2>/dev/null; then echo "PROBE $1 WROTE"; else echo "PROBE $1 REFUSED"; fi; }',
        'ap() { if ( echo probe >> "$2" ) 2>/dev/null; then echo "PROBE $1 WROTE"; else echo "PROBE $1 REFUSED"; fi; }',
        f"ap tracked {q(str(tracked))}",
        f"w newfile {q(str(tree / PROBE_NEW_FILE))}",
        f"w scratch {q(str(targets.scratch / 'probe'))}",
        f"w sibling {q(str(targets.sibling))}",
        f"w home {q(str(targets.stand_in_home / 'probe'))}",
        f"w tmp {q('/tmp/' + marker)}",
        f"w vartmp {q('/var/tmp/' + marker)}",
        f"echo x > {q(str(rename_src))} 2>/dev/null",
        f"if mv {q(str(rename_src))} {q(str(rename_dst))} 2>/dev/null; "
        f'then echo "PROBE rename WROTE"; else echo "PROBE rename REFUSED"; fi',
        f"echo x > {q(str(delete_file))} 2>/dev/null",
        f"if rm {q(str(delete_file))} 2>/dev/null && [ ! -e {q(str(delete_file))} ]; "
        f'then echo "PROBE delete WROTE"; else echo "PROBE delete REFUSED"; fi',
        f"if git -C {q(str(tree))} {identity} add -A >/dev/null 2>&1 && "
        f"git -C {q(str(tree))} {identity} commit -q --allow-empty --no-verify -m probe >/dev/null 2>&1; "
        f'then echo "PROBE commit COMMITTED"; else echo "PROBE commit FAILED"; fi',
        'ah() { if ( : >> "$2" ) 2>/dev/null; then echo "PROBE $1 WROTE"; else echo "PROBE $1 REFUSED"; fi; }',
        f"ah hooks {q(str(git_dir / 'hooks' / ('probe-' + marker)))}",
        f"if ( printf '%s\\n' {q('# probe-' + marker)} >> {q(str(git_dir / 'config'))} ) 2>/dev/null; "
        f'then echo "PROBE gitconfig WROTE"; else echo "PROBE gitconfig REFUSED"; fi',
        f"if cat {q(str(targets.canary_file))} >/dev/null 2>&1; then echo \"PROBE canary_file READ\"; "
        f'else echo "PROBE canary_file BLOCKED"; fi',
        f'if [ -n "${{{targets.canary_env}:-}}" ]; then echo "PROBE canary_env SET"; else echo "PROBE canary_env EMPTY"; fi',
        f"if cat {q(str(targets.credential))} >/dev/null 2>&1; then echo \"PROBE credential READ\"; "
        f'else echo "PROBE credential BLOCKED"; fi',
        f"if git -C {q(str(tree))} push {q(str(targets.push_origin))} HEAD:refs/heads/probe >/dev/null 2>&1; "
        f'then echo "PROBE push PUSHED"; else echo "PROBE push REFUSED"; fi',
        'gh --version >/dev/null 2>&1; if [ "$?" = 127 ]; then echo "PROBE gh_name STANDIN"; '
        'else echo "PROBE gh_name OTHER"; fi',
        'if /usr/bin/gh auth status >/dev/null 2>&1; then echo "PROBE gh_path AUTHED"; '
        'else echo "PROBE gh_path FAILED"; fi',
        f'if python3 -c {q(py_tcp)} 2>/dev/null; then echo "PROBE tcp CONNECTED"; else echo "PROBE tcp BLOCKED"; fi',
        f'if python3 -c {q(py_unix)} 2>/dev/null; then echo "PROBE unix CONNECTED"; else echo "PROBE unix BLOCKED"; fi',
    ])


def probe_prompt(command: str) -> str:
    return ("Run exactly the following shell command once, then reply with its complete output and nothing else. "
            "Do not change the command, do not retry it, and do not run anything else.\n\n"
            f"```sh\n{command}\n```\n")


def judge(output: str) -> list[str]:
    """The failures of a probe run's output; empty means every boundary held. A line missing, repeated with a
    different result, not expected, or output nobody could read, is a failure (fail closed)."""
    seen: dict[str, str] = {}
    failures: list[str] = []
    for match in _PROBE_LINE_RE.finditer(output):
        name, result = match["name"], match["result"]
        if name in seen and seen[name] != result:
            failures.append(f"{name}: reported both {seen[name]} and {result}")
        seen[name] = result
    for name, want in PROBE_EXPECTED.items():
        got = seen.get(name)
        if got is None:
            failures.append(f"{name}: no result line")
        elif got != want:
            failures.append(f"{name}: {got}, expected {want}")
    for name, allowed in PROBE_INFORMATIONAL.items():
        got = seen.get(name)
        if got is None:
            failures.append(f"{name}: no result line")
        elif got not in allowed:
            failures.append(f"{name}: {got}, expected one of {sorted(allowed)}")
    for name in seen.keys() - PROBE_EXPECTED.keys() - PROBE_INFORMATIONAL.keys():
        failures.append(f"{name}: not a known probe")
    return failures


def sandbox_probe_argv(layout: WorkerLayout, *, binary: Path, command: str) -> list[str]:
    """The modelless form: ``codex sandbox -P worker -C <worktree> -c <shell policy> -- sh -c <command>``, run with the
    worker's ``CODEX_HOME`` and environment. No ``-s`` and no other profile selector."""
    out = [str(binary), "sandbox", "-P", PROFILE, "-C", str(layout.worktree)]
    for item in shell_policy_overrides(layout):
        out += ["-c", item]
    return out + ["--", "sh", "-c", command]


# ---------------------------------------------------------------------------
# The git guard (D4)
# ---------------------------------------------------------------------------

#: The roots under ``.git`` whose contents a commit writes: only their type is recorded.
GUARD_EXCLUDED_ROOTS = ("objects", "refs", "logs")
#: The regular files a commit or an automatic gc writes (and ``*.lock``): their existence and content are not recorded.
GUARD_EXCLUDED_FILES = frozenset({"index", "HEAD", "ORIG_HEAD", "COMMIT_EDITMSG", "packed-refs", "info/refs"})
#: Files whose existence means an object store outside the repository (recorded as a boolean, refused when true).
GUARD_ALTERNATES = ("objects/info/alternates", "objects/info/http-alternates")


def _kind(mode: int) -> str:
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "dir"
    if stat.S_ISREG(mode):
        return "file"
    return "special"


def _guard_excluded(rel: str, kind: str) -> bool:
    """True when ``rel`` is a regular file or a directory that a commit may add, change or remove unrecorded."""
    if kind not in ("file", "dir"):
        return False
    if rel.split("/", 1)[0] in GUARD_EXCLUDED_ROOTS:
        return True
    return kind == "file" and (rel in GUARD_EXCLUDED_FILES or rel.endswith(".lock"))


def guard_digest(worktree: Path) -> dict:
    """The digest of ``<worktree>/.git`` (D4): the type of ``.git`` itself; the type and mode (and the content of a
    regular file) of every entry except what a commit writes; the type of each excluded root; every symlink and every
    entry that is neither a regular file nor a directory, anywhere, by relative path; and whether an alternates file
    exists. Nothing is followed: every entry is read with ``lstat``."""
    git = worktree / ".git"
    try:
        top = _kind(os.lstat(git).st_mode)
    except FileNotFoundError:
        top = "missing"
    except OSError as exc:
        raise _refuse(f"cannot read {git}: {exc}", check="guard", entry=".git") from exc
    digest: dict = {"git": top, "entries": {}, "roots": {}, "special": [], "alternates": False}
    if top != "dir":
        return digest
    for root in GUARD_EXCLUDED_ROOTS:
        try:
            digest["roots"][root] = _kind(os.lstat(git / root).st_mode)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise _refuse(f"cannot read {git / root}: {exc}", check="guard", entry=root) from exc
    for alternates in GUARD_ALTERNATES:
        if os.path.lexists(git / alternates):
            digest["alternates"] = True
    pending = [""]
    while pending:
        rel_dir = pending.pop()
        directory = git / rel_dir if rel_dir else git
        try:
            with os.scandir(directory) as scan:
                children = sorted(scan, key=lambda e: e.name)
            for child in children:
                rel = f"{rel_dir}/{child.name}" if rel_dir else child.name
                mode = child.stat(follow_symlinks=False)
                kind = _kind(mode.st_mode)
                if kind in ("symlink", "special"):
                    digest["special"].append(rel)
                    continue
                if kind == "dir":
                    pending.append(rel)
                if _guard_excluded(rel, kind):
                    continue
                record = f"{kind} {stat.S_IMODE(mode.st_mode):o}"
                if kind == "file":
                    record += " " + hashlib.sha256((git / rel).read_bytes()).hexdigest()
                digest["entries"][rel] = record
        except OSError as exc:
            raise _refuse(f"cannot read {directory}: {exc}", check="guard", entry=rel_dir or ".git") from exc
    digest["special"].sort()
    return digest


def check_guard(before: Mapping, after: Mapping) -> None:
    """Refuse, naming the entry, when ``.git`` is not a real directory in either digest, an excluded root is not a
    directory or changed type, a symlink or special entry exists in either digest, an alternates file exists, or any
    other entry was added, removed or changed. A baseline that already holds a symlink is refused, not accepted."""
    for label, digest in (("before", before), ("after", after)):
        if digest["git"] != "dir":
            raise _refuse(f"the worktree's .git is {digest['git']} ({label} the run), not a directory",
                          check="guard", entry=".git")
    for label, digest in (("before", before), ("after", after)):
        for root, kind in digest["roots"].items():
            if kind != "dir":
                raise _refuse(f".git/{root} is {kind} ({label} the run), not a directory", check="guard", entry=root)
    for root in sorted(before["roots"]):
        if before["roots"][root] != after["roots"].get(root):
            raise _refuse(f".git/{root} changed during the run", check="guard", entry=root)
    for label, digest in (("before", before), ("after", after)):
        if digest["special"]:
            entry = digest["special"][0]
            raise _refuse(f".git/{entry} is a symlink or a special file ({label} the run)", check="guard", entry=entry)
        if digest["alternates"]:
            raise _refuse(f".git/{GUARD_ALTERNATES[0]} (or http-alternates) exists ({label} the run)",
                          check="guard", entry=GUARD_ALTERNATES[0])
    entries_before, entries_after = before["entries"], after["entries"]
    for entry in sorted(set(entries_before) | set(entries_after)):
        if entries_before.get(entry) != entries_after.get(entry):
            what = "added" if entry not in entries_before else "removed" if entry not in entries_after else "changed"
            raise _refuse(f".git/{entry} was {what} during the run", check="guard", entry=entry)
