"""The Codex tester sandbox (C10g-1).

A Codex process launched on a disposable clone can run the checklist's commands
and write test output into a scratch area, and nothing else. The mechanism is a
restricted-read permissions profile in the tester's own ``CODEX_HOME/config.toml``
(``default_permissions = "tester"``), selected in place of the reviewer's
``-s workspace-write``. The reviewer's pinned constants and controls in
:mod:`controller.review` are imported and reused, never edited. Nothing calls this
module yet: no role is declared and no launch path uses it (see
``docs/ai-workflow/CONTROLLER_CODEX_TESTER_SANDBOX_PLAN.md``).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from . import functional_review, review, runtime
from .errors import ReviewLaunchRefusedError

#: The tester's permissions profile name (``default_permissions``).
PROFILE = "tester"

#: System directories the profile makes readable. Only those that exist are listed.
SYSTEM_READ_DIRS = ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc")

#: Seconds a ``mcp list`` or ``features list`` control may take.
LISTING_TIMEOUT = 60.0

#: Flags no tester command line may carry: the reviewer's banned set, plus every way to select a sandbox other
#: than the profile.
BANNED_FLAGS = frozenset({"-p", "--profile", "--approve-for-me", "--dangerously-bypass-approvals-and-sandbox",
                          "--dangerously-bypass-hook-trust", "--add-dir", "--oss", "--ephemeral", "--enable",
                          "-s", "--sandbox", "--full-auto"})


@dataclasses.dataclass(frozen=True)
class Limit:
    """One boundary the sandbox does not (or cannot) enforce, with the sentence the guide carries."""

    name: str
    reason: str
    closed_by: str
    guide: str


#: What the sandbox does not guarantee. The first entry is the unbuilt composed-configuration check (D1b).
KNOWN_LIMITS: tuple[Limit, ...] = (
    Limit("composed_configuration",
          "the composed configuration values and the managed layers that are not files are not verified, other than "
          "their MCP servers and features; a managed layer could still change a setting other than an MCP server or "
          "a feature",
          "C10g-3 builds the tester's composed-configuration check before the sandbox is wired",
          "composed configuration values and non-file managed layers other than their MCP servers and features are "
          "not verified; a managed layer could still change a setting other than an MCP server or a feature"),
)


def _refuse(message: str, **evidence) -> ReviewLaunchRefusedError:
    return review._refuse(message, **evidence)


# ---------------------------------------------------------------------------
# The layout
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class TesterLayout:
    """Where one tester job's files live under ``<runtime>/functional/<job id>/``."""

    runtime_root: Path
    job_id: str

    @property
    def job(self) -> review.JobLayout:
        return review.JobLayout(self.runtime_root, self.job_id, area="functional")

    @property
    def base(self) -> Path:
        return self.job.base

    @property
    def clone(self) -> Path:
        return self.job.tree

    @property
    def home(self) -> Path:
        return self.job.home

    @property
    def codex_home(self) -> Path:
        return self.job.codex_home

    @property
    def verdict(self) -> Path:
        return self.job.verdict

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


# ---------------------------------------------------------------------------
# Paths and the readable roots (D2)
# ---------------------------------------------------------------------------


def _string(value: Path | str) -> str:
    """``value`` as a TOML basic string. A value that carries a quote, a backslash or a control character refuses
    (those are the only inputs ``json.dumps`` would escape differently from TOML)."""
    text = str(value)
    if any(ch in '"\\' or ord(ch) < 32 or ord(ch) == 127 for ch in text):
        raise _refuse(f"the value {text!r} cannot be written into the sandbox profile", check="path", entry=text)
    return json.dumps(text)


def _quote(path: Path | str) -> str:
    """``path`` as a TOML basic string; it must also be absolute."""
    if not str(path).startswith("/"):
        raise _refuse(f"the path {str(path)!r} is not absolute", check="path", entry=str(path))
    return _string(path)


def _forms(path: Path) -> set[Path]:
    forms = {Path(os.path.abspath(path))}
    try:
        forms.add(path.resolve())
    except OSError:
        pass
    return forms


def _contains(outer: Path, inner: Path) -> bool:
    return inner == outer or outer in inner.parents


def package_root(target: Path) -> Path:
    """The package root of a resolved Codex entry point: its nearest ancestor holding a ``package.json``, searched
    upward only as far as the nearest enclosing ``node_modules`` directory; the target's own directory when there
    is no such directory or no such file, so a stray ``package.json`` above a non-npm binary is never reached."""
    modules = next((parent for parent in target.parents if parent.name == "node_modules"), None)
    if modules is not None:
        for parent in target.parents:
            if parent == modules:
                break
            if (parent / "package.json").is_file():
                return parent
    return target.parent


def codex_install_roots(binary: Path) -> list[Path]:
    """The directory of ``binary`` (the symlink kept), of its resolved target, the target's package root and the
    interpreter directory of its ``#!`` line: what ``bwrap`` needs to exec ``codex`` inside the sandbox."""
    target = binary.resolve()
    roots = [binary.parent, target.parent, package_root(target)]
    interpreter = review.interpreter_dir(binary)
    if interpreter is not None:
        roots.append(interpreter)
    return roots


def interpreter_roots(python: Path | None = None, prefix: Path | None = None, base_prefix: Path | None = None
                      ) -> list[Path]:
    """The directories the stand-in launchers' interpreter needs: its resolved directory, ``sys.prefix`` (a venv's
    ``pyvenv.cfg`` and site directory) and ``sys.base_prefix`` (the standard library)."""
    python = Path(python or sys.executable).resolve()
    return [python.parent, Path(prefix or sys.prefix).resolve(), Path(base_prefix or sys.base_prefix).resolve()]


def controller_package_dir() -> Path:
    """The ``controller`` directory itself, never its parent: from a checkout the parent holds ``.controller``."""
    return Path(functional_review.__file__).resolve().parent


def check_roots(layout: TesterLayout, roots: Sequence[Path], operator_home: Path | None = None) -> None:
    """The overlap refusal: no readable root may equal or contain the job's base, the runtime root or the Codex
    home, lie inside the scratch area, or be ``/``, the operator's real home or one of its ancestors."""
    home = operator_home if operator_home is not None else Path.home()
    protected = {"the job folder": layout.base, "the runtime root": layout.runtime_root,
                 "the Codex home": layout.codex_home, "the operator's home": home}
    for root in roots:
        for form in _forms(root):
            if form == Path("/"):
                raise _refuse(f"the readable root {root} is the filesystem root", check="roots", entry=str(root))
            for what, path in protected.items():
                if any(_contains(form, p) for p in _forms(path)):
                    raise _refuse(f"the readable root {root} contains {what} ({path})", check="roots", entry=str(root))
            if any(_contains(s, form) for s in _forms(layout.scratch)):
                raise _refuse(f"the readable root {root} lies inside the scratch area", check="roots", entry=str(root))


def readable_roots(layout: TesterLayout, binary: Path, *, extra: Sequence[Path] = (), python: Path | None = None,
                   prefix: Path | None = None, base_prefix: Path | None = None, package_dir: Path | None = None,
                   system_dirs: Sequence[str] = SYSTEM_READ_DIRS, operator_home: Path | None = None) -> list[Path]:
    """Every directory the sandbox makes readable besides the clone: system directories, the Codex install roots
    and interpreter, the stand-in interpreter roots, the Controller package directory, ``<base>/bin`` and ``extra``
    (test-only roots). De-duplicated, sorted, and refused by :func:`check_roots` when one overlaps the job."""
    roots = [Path(d) for d in system_dirs if Path(d).exists()]
    roots += codex_install_roots(binary)
    roots += interpreter_roots(python, prefix, base_prefix)
    roots += [package_dir if package_dir is not None else controller_package_dir(), layout.bin, *extra]
    unique = sorted({Path(os.path.abspath(root)) for root in roots})
    check_roots(layout, unique, operator_home)
    return unique


# ---------------------------------------------------------------------------
# The configuration (D1)
# ---------------------------------------------------------------------------


def profile_toml(layout: TesterLayout, roots: Sequence[Path]) -> str:
    """The ``tester`` permissions profile: ``:minimal``, the readable roots and the clone read, the scratch area
    write, the network off. Nothing of the operator's home is listed."""
    entries = {str(root): "read" for root in roots}
    entries[str(layout.clone)] = "read"
    entries[str(layout.scratch)] = "write"
    lines = [f'default_permissions = "{PROFILE}"', f"[permissions.{PROFILE}.filesystem]", '":minimal" = "read"']
    lines += [f"{_quote(path)} = {json.dumps(access)}" for path, access in sorted(entries.items())]
    lines += [f"[permissions.{PROFILE}.network]", "enabled = false"]
    return "\n".join(lines) + "\n"


def config_toml(layout: TesterLayout, roots: Sequence[Path]) -> str:
    """The tester's ``config.toml``: the reviewer's pinned lines plus the profile. Deterministic."""
    limit = KNOWN_LIMITS[0]
    header = ("# Written by the Controller for one tester job; never edited.\n"
              f"# Known limit: {limit.guide}.\n")
    pinned = review.HOME_CONFIG_TOML.split("\n", 1)[1]
    return header + pinned + profile_toml(layout, roots)


def check_home(layout: TesterLayout, config_text: str) -> None:
    """The startup-load check for the tester's home: ``config.toml`` is exactly ``config_text`` and every other
    entry is a pinned Codex-written state entry (the reviewer's rules, over its public constants)."""
    home = layout.codex_home
    config = home / "config.toml"
    try:
        digest = hashlib.sha256(config.read_bytes()).hexdigest()
    except OSError:
        digest = None
    if digest != hashlib.sha256(config_text.encode()).hexdigest():
        raise _refuse("the tester's Codex home config.toml differs from the one written", check="home",
                      entry="config.toml", source=str(config))
    for entry in sorted(home.iterdir(), key=lambda p: p.name):
        name = entry.name
        if name == "config.toml":
            continue
        if name in ("skills", "plugins"):
            for child in sorted(entry.iterdir(), key=lambda p: p.name):
                if f"{name}/{child.name}" not in review.CODEX_STARTUP_SUBENTRIES:
                    raise _refuse(f"the tester's Codex home holds the startup-loaded entry {name}/{child.name!r}",
                                  check="home", entry=f"{name}/{child.name}")
            continue
        if name in review.STARTUP_LOADED or name.startswith("hooks") or name.endswith(".toml"):
            raise _refuse(f"the tester's Codex home holds the startup-loaded entry {name!r}", check="home", entry=name)
        if name in review.CODEX_STATE_ENTRIES or name.endswith(review._STATE_DB_SUFFIXES):
            continue
        raise _refuse(f"the tester's Codex home holds the unexpected entry {name!r}", check="home", entry=name)


def check_launch(layout: TesterLayout, config_text: str, binary: Path, env: Mapping[str, str], *,
                 system_dir: Path = review.SYSTEM_LAYER_DIR, run: Callable[..., review.RunResult] | None = None,
                 timeout: float = LISTING_TIMEOUT) -> None:
    """The pre-launch controls (D1b), in order, before every ``codex exec`` and ``codex sandbox`` run: the layers
    outside the home, the home, ``mcp list``, ``features list`` and the home again. The first refusal stops the
    rest. Not covered: the composed-configuration values (:data:`KNOWN_LIMITS`)."""
    run = run or review.run_bounded
    review.check_outside_layers(system_dir)
    for kind in ("mcp", "features"):
        check_home(layout, config_text)
        command = review.argv(kind, binary=binary)
        review.check_argv(kind, command)
        result = run(command, cwd=layout.clone, env=env, timeout=timeout)
        if result.exit_code != 0 or result.timed_out:
            raise _refuse(f"`{kind} list` did not run", check=kind, value=result.stderr.decode("utf-8", "replace")[:400])
        (review.check_mcp_list if kind == "mcp" else review.check_features)(result.stdout.decode("utf-8", "replace"))
    check_home(layout, config_text)


# ---------------------------------------------------------------------------
# The environment and the command line (D3, D4)
# ---------------------------------------------------------------------------


def child_environment(layout: TesterLayout, binary: Path, ownership_tag: str, inherited: Mapping[str, str] | None = None
                      ) -> dict[str, str]:
    """The Codex process's environment: the reviewer's allowlist, with the scratch ``TMPDIR``."""
    env = review.child_environment(layout.job, binary, ownership_tag, inherited)
    env["TMPDIR"] = str(layout.scratch_tmp)
    return env


def command_environment(layout: TesterLayout) -> dict[str, str]:
    """What the commands inside the sandbox see (``shell_environment_policy``): nothing inherited."""
    return {"PATH": f"{layout.bin}:{review.SYSTEM_PATH}", "HOME": str(layout.home), "TMPDIR": str(layout.scratch_tmp),
            "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def shell_policy_overrides(layout: TesterLayout) -> list[str]:
    """The two ``-c`` overrides that leave the sandboxed shell with only :func:`command_environment`."""
    items = ", ".join(f"{key}={_string(value)}" for key, value in command_environment(layout).items())
    return ['shell_environment_policy.inherit="none"', f"shell_environment_policy.set={{{items}}}"]


def required_overrides(layout: TesterLayout, *, model: str = review.DEFAULT_MODEL, effort: str = review.DEFAULT_EFFORT
                       ) -> tuple[str, ...]:
    """The complete ``-c`` set of a tester ``codex exec`` (D4)."""
    return (
        'model_provider="openai"', "notify=[]", 'web_search="disabled"', 'project_root_markers=[".git"]',
        f'model="{model}"', f'model_reasoning_effort="{effort}"', f'service_tier="{review.SERVICE_TIER}"',
        'approval_policy="never"', f'projects={{{_quote(layout.clone)}={{trust_level="untrusted"}}}}',
        "skills.bundled.enabled=false", f'default_permissions="{PROFILE}"', *shell_policy_overrides(layout),
    )


def argv(layout: TesterLayout, *, binary: Path, model: str = review.DEFAULT_MODEL, effort: str = review.DEFAULT_EFFORT,
         prompt: str | None = None) -> list[str]:
    """The only constructor of a tester ``codex exec`` command line: the reviewer's flags with no ``-s`` and the
    profile selected by ``default_permissions``."""
    out = [str(binary), "exec", "--strict-config"]
    for feature in review.DISABLED_FEATURES:
        out += ["--disable", feature]
    for item in required_overrides(layout, model=model, effort=effort):
        out += ["-c", item]
    out += ["-C", str(layout.clone), "--ignore-rules", "--color", "never", "-o", str(layout.verdict)]
    out.append(prompt if prompt is not None else "-")
    return out


def check_argv(command: Sequence[str], layout: TesterLayout, *, model: str = review.DEFAULT_MODEL,
               effort: str = review.DEFAULT_EFFORT) -> None:
    """Refuse a tester command line that omits a pinned item, adds a ``-c`` outside :func:`required_overrides`,
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

    def value_of(flag: str) -> str | None:
        return command[command.index(flag) + 1] if command.count(flag) == 1 and command.index(flag) + 1 < len(command) \
            else None

    for flag in ("--strict-config", "--ignore-rules"):
        if flag not in command:
            raise _refuse(f"the command line omits {flag}", check="argv", entry=flag)
    for flag, expected in (("-C", str(layout.clone)), ("-o", str(layout.verdict)), ("--color", "never")):
        if value_of(flag) != expected:
            raise _refuse(f"the command line does not pin {flag} {expected}", check="argv", entry=flag)


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Built:
    """What :func:`build` prepared: the layout, the files written and the command to launch."""

    layout: TesterLayout
    config_text: str
    roots: tuple[Path, ...]
    argv: tuple[str, ...]
    environment: Mapping[str, str]


def build(*, runtime_root: Path, job_id: str, operator_home: Path, binary: Path, ownership_tag: str = "tester",
          model: str = review.DEFAULT_MODEL, effort: str = review.DEFAULT_EFFORT, prompt: str | None = None,
          system_dir: Path = review.SYSTEM_LAYER_DIR, package_parent: Path | None = None,
          extra_readable: Sequence[Path] = (), inherited: Mapping[str, str] | None = None,
          real_home: Path | None = None) -> Built:
    """Write the tester's home (credential copy, ``config.toml``), scratch area and stand-in launchers, run the two
    controls that start no process (outside layers, home) and return the command and environment. The clone is
    the caller's. Nothing is written under the clone and nothing is started. ``real_home`` is the operator's real
    home for the overlap refusal (default: the current user's)."""
    layout = TesterLayout(runtime_root, job_id)
    review.check_outside_layers(system_dir)
    python = Path(sys.executable).resolve()
    package_dir = package_parent / "controller" if package_parent is not None else None
    roots = readable_roots(layout, binary, extra=extra_readable, python=python, package_dir=package_dir,
                           operator_home=real_home)
    config_text = config_toml(layout, roots)
    review.build_home(layout.job, operator_home)
    runtime.write_private_bytes(runtime_root, layout.rel("codex-home", "config.toml"), config_text.encode())
    for directory in (layout.scratch_tmp, layout.fake_logs):
        directory.mkdir(parents=True, exist_ok=True)
    functional_review.write_launchers(runtime_root, layout.rel(), layout.fake_logs, python=str(python),
                                      package_parent=package_parent)
    check_home(layout, config_text)
    command = argv(layout, binary=binary, model=model, effort=effort, prompt=prompt)
    check_argv(command, layout, model=model, effort=effort)
    return Built(layout, config_text, tuple(roots), tuple(command),
                 child_environment(layout, binary, ownership_tag, inherited))
