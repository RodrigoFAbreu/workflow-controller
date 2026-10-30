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
import re
import shlex
import sys
import tomllib
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


#: What the sandbox does not guarantee. The one entry is the window in which :func:`check_composed` can be out of date (D6).
KNOWN_LIMITS: tuple[Limit, ...] = (
    Limit("composition_window",
          "check_composed reads the configuration from a separate app-server process started just before the launch, "
          "with the same home, environment and -c items, so a layer that changes between that read and the launch, or "
          "a setting only the exec command composes differently, is not seen; the reviewer's control (b) makes the "
          "same assumption",
          "none: a different composing path would need a Codex command that reports the exec's own composition",
          "the composed configuration is read by the app-server just before the launch, so a layer that changes "
          "in that window, or a setting only exec composes differently, is not seen"),
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
    """``value`` as a TOML basic string. A value that carries a quote, a backslash, a control character or a lone
    surrogate refuses (those are the only inputs ``json.dumps`` would escape differently from TOML); every other
    character, supplementary ones included, is written as itself, since TOML has no surrogate-pair escapes."""
    text = str(value)
    if any(ch in '"\\' or ord(ch) < 32 or ord(ch) == 127 or 0xD800 <= ord(ch) <= 0xDFFF for ch in text):
        raise _refuse(f"the value {text!r} cannot be written into the sandbox profile", check="path", entry=text)
    return json.dumps(text, ensure_ascii=False)


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
    home, lie inside the scratch area or the Codex home (the credential copy), or be ``/``, the operator's real home
    or one of its ancestors."""
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
            if any(_contains(h, form) for h in _forms(layout.codex_home)):
                raise _refuse(f"the readable root {root} lies inside the Codex home", check="roots", entry=str(root))


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
    outside the home, then the home before each of ``mcp list`` and ``features list`` and once more after both, as
    the reviewer's ``_preflight`` does (a listing writes Codex state into the home). The first refusal stops the
    rest. The composed configuration is a separate control, :func:`check_composed`, which this does not call
    (:data:`KNOWN_LIMITS` names the window it leaves)."""
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
    for arg in command[1:-1]:
        if arg == "--config" or arg.startswith(("--config=", "--sandbox", "--add-dir=", "-c=")):
            raise _refuse(f"the command line carries the equivalent spelling {arg!r}", check="argv", entry=arg)

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
# The composed configuration (C10g-3a)
# ---------------------------------------------------------------------------


def compose_argv(layout: TesterLayout, *, binary: Path, model: str = review.DEFAULT_MODEL,
                 effort: str = review.DEFAULT_EFFORT) -> list[str]:
    """The only constructor of the tester's composing command: ``app-server`` with the exec's own ``--disable`` and
    ``-c`` items, so the composed values are the ones the tester runs under (``tester_compose.txt``)."""
    out = [str(binary), "app-server", "--strict-config"]
    for feature in review.DISABLED_FEATURES:
        out += ["--disable", feature]
    for item in required_overrides(layout, model=model, effort=effort):
        out += ["-c", item]
    return out + ["--listen", "stdio://"]


def check_compose_argv(command: Sequence[str], layout: TesterLayout, *, model: str = review.DEFAULT_MODEL,
                       effort: str = review.DEFAULT_EFFORT) -> None:
    """Refuse a composing command line that omits a pinned item, adds a ``-c`` outside :func:`required_overrides`,
    selects a sandbox or carries a banned flag, then require it to equal :func:`compose_argv` for its own binary."""
    if len(command) < 3 or command[1] != "app-server":
        raise _refuse("the command line is not an app-server run", check="argv", entry=command[1] if len(command) > 1
                      else "")
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
    for arg in command[1:]:
        if arg == "--config" or arg.startswith(("--config=", "--sandbox", "--add-dir=", "-c=")):
            raise _refuse(f"the command line carries the equivalent spelling {arg!r}", check="argv", entry=arg)
    if "--strict-config" not in command:
        raise _refuse("the command line omits --strict-config", check="argv", entry="--strict-config")
    if command.count("--listen") != 1 or command.index("--listen") + 1 >= len(command) \
            or command[command.index("--listen") + 1] != "stdio://":
        raise _refuse("the command line does not listen on stdio", check="argv", entry="--listen")
    if list(command) != compose_argv(layout, binary=Path(command[0]), model=model, effort=effort):
        raise _refuse("the command line differs from the composing command the Controller builds", check="argv",
                      entry="argv")


def _override_values(layout: TesterLayout, *, model: str, effort: str) -> dict[str, object]:
    """The value each ``-c`` item of :func:`required_overrides` sets, keyed by the dotted name as written: the item
    parsed back as TOML, so the argv and the expected policy have one source. A dotted name the reviewer's table keeps
    flat (``skills.bundled.enabled``) stays flat; any other is a leaf of its table, which is compared whole."""
    out: dict[str, object] = {}
    for item in required_overrides(layout, model=model, effort=effort):
        key = item.partition("=")[0]
        node: object = tomllib.loads(item)
        for part in key.split("."):
            node = node[part]
        if "." in key and key not in review.EFFECTIVE_POLICY:
            table, _, leaf = key.partition(".")
            out.setdefault(table, {})[leaf] = node
        else:
            out[key] = node
    return out


def effective_policy(layout: TesterLayout, roots: Sequence[Path], *, model: str = review.DEFAULT_MODEL,
                     effort: str = review.DEFAULT_EFFORT) -> dict[str, object]:
    """Every value the tester's composed ``config`` must equal (D1). The argv-pinned values (approval policy, model,
    effort, tier, trust, shell policy, ``default_permissions``) are parsed back from :func:`required_overrides`, so a
    constructor value cannot diverge from the expectation; the profile is parsed back from :func:`profile_toml`, the
    sandbox keys are unset, and the reviewer's remaining defaults (its scalar table without ``sandbox_mode``) fill
    the keys the argv does not pin."""
    profile = tomllib.loads(profile_toml(layout, roots))
    scalars = {key: value for key, value in review.EFFECTIVE_POLICY.items() if key != "sandbox_mode"}
    return {
        **scalars, **_override_values(layout, model=model, effort=effort),
        "permissions": profile["permissions"], "sandbox_mode": None, "sandbox_workspace_write": None,
    }


def _origin_of(origins: Mapping, leaf: str) -> object:
    """The origin of ``leaf``: its own or one below it (:func:`review._origin`), else the nearest ancestor table's,
    as ``config/read`` reports a table whose leaves were set together under the table's name."""
    found = review._origin(origins, leaf)
    parts = leaf.split(".")
    while found is None and len(parts) > 1:
        parts.pop()
        found = origins.get(".".join(parts))
    return found


def _set_leaves(value: object, prefix: str) -> dict[str, object]:
    """The leaves of ``value`` that carry a value (D2): a ``null`` is unset, and so is an empty table or list that is
    the whole key; an empty table or list below the key (``mcp_servers.<name> = {}``) is a set leaf, as the
    reviewer's check treats it."""
    return {leaf: item for leaf, item in review.flatten(value, prefix).items()
            if item is not None and not (leaf == prefix and item in ({}, []))}


def check_effective(composed: Mapping, layout: TesterLayout, roots: Sequence[Path], *,
                    model: str = review.DEFAULT_MODEL, effort: str = review.DEFAULT_EFFORT) -> None:
    """The tester's control (b): ``composed`` is the ``config/read`` result with layers. The layers follow the
    reviewer's rules, every key of :func:`effective_policy` is compared leaf by leaf (``null`` is unset, an unexpected
    leaf refuses), every origin must be an allowed layer, no layer other than the packaged defaults may set a
    forbidden key (the tester's own ``permissions`` and ``default_permissions`` are compared in full instead) and no
    disabled feature may be reported enabled. The error names the leaf, its value and its origin layer."""
    config = composed.get("config") if isinstance(composed, Mapping) else None
    origins = composed.get("origins") if isinstance(composed, Mapping) else None
    layers = composed.get("layers") if isinstance(composed, Mapping) else None
    if not isinstance(config, Mapping) or not isinstance(origins, Mapping) or not isinstance(layers, list):
        raise _refuse("the composed configuration is not a config/read result with layers", check="effective")
    review._check_layers(layers, layout.codex_home)
    for key, want in effective_policy(layout, roots, model=model, effort=effort).items():
        got = review._lookup(config, key)
        wanted_leaves, got_leaves = _set_leaves(want, key), _set_leaves(got, key)
        if wanted_leaves == got_leaves:
            continue
        differing = [leaf for leaf in sorted({*wanted_leaves, *got_leaves})
                     if wanted_leaves.get(leaf, "<unset>") != got_leaves.get(leaf, "<unset>")]
        leaf = next((name for name in differing if name in got_leaves), differing[0])
        raise _refuse(f"the composed configuration sets {leaf} to {got_leaves.get(leaf, '<unset>')!r}, expected "
                      f"{wanted_leaves.get(leaf, '<unset>')!r}", check="effective", key=leaf,
                      value=got_leaves.get(leaf), source=_origin_of(origins, leaf))
    for key in review.INSTRUCTION_KEYS:
        if config.get(key) and review._origin(origins, key) is None:
            raise _refuse(f"the composed configuration sets {key} from no layer the Controller can judge",
                          check="effective", key=key, value=config.get(key))
    forbidden = tuple(key for key in review.FORBIDDEN_EFFECTIVE_KEYS if key not in ("permissions", "default_permissions"))
    leaves = review.flatten(config)
    for name in sorted(origins):
        origin = origins[name]
        source = origin.get("name", origin) if isinstance(origin, Mapping) else origin
        if isinstance(source, Mapping) and source.get("type") == "packagedDefaults":
            continue
        kind = source.get("type") if isinstance(source, Mapping) else None
        if kind not in review.ORIGIN_ALLOWED and review.MANAGED_APPROVED_LEAVES.get(name, object()) != leaves.get(name):
            raise _refuse(f"the composed configuration sets {name} from a {kind} layer", check="effective",
                          key=name, value=leaves.get(name), source=origin)
        if name.split(".", 1)[0] in forbidden or (name.startswith("skills.") and name != "skills.bundled.enabled"):
            raise _refuse(f"the composed configuration sets {name}", check="effective", key=name,
                          value=leaves.get(name), source=origin)
    features = config.get("features")
    for feature in review.DISABLED_FEATURES:
        if isinstance(features, Mapping) and feature in features and features[feature] is not False:
            raise _refuse(f"the composed configuration sets features.{feature} to {features[feature]!r}", check="effective",
                          key=f"features.{feature}", value=features[feature],
                          source=review._origin(origins, f"features.{feature}"))


def _compose_config(layout: TesterLayout, binary: Path, env: Mapping[str, str], *, model: str, effort: str,
                   timeout: float, run: Callable[..., review.RunResult] | None = None) -> dict:
    """Run the tester's composing command and return the ``config/read`` result with its layers. The exchange is
    :func:`review.compose_config`'s (``initialize``, ``initialized``, ``config/read`` with ``includeLayers`` and the
    clone as ``cwd``, stdin held open by :func:`review._answered` until the answer); the requests are repeated here
    because that function builds them inline for the reviewer's argv. It needs no credential and starts no model
    request."""
    command = compose_argv(layout, binary=binary, model=model, effort=effort)
    check_compose_argv(command, layout, model=model, effort=effort)
    requests = [
        {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "workflow-controller", "version": "1"}}},
        {"method": "initialized"},
        {"id": 2, "method": "config/read", "params": {"includeLayers": True, "cwd": str(layout.clone)}},
    ]
    run = run or review.run_bounded
    result = run(command, cwd=layout.clone, env=env, timeout=timeout,
                 stdin_text="".join(json.dumps(item) + "\n" for item in requests), hold_open_until=review._answered)
    stderr = result.stderr.decode("utf-8", "replace")
    if result.timed_out or result.exit_code != 0:
        raise _refuse("the composing command did not run", check="effective", value=stderr.strip()[-400:])
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        try:
            message = json.loads(line)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") == 2:
            if isinstance(message.get("result"), dict):
                return message["result"]
            raise _refuse("the composing command answered config/read with an error", check="effective",
                          value=json.dumps(message.get("error"))[:400])
    raise _refuse("the composing command printed no config/read answer", check="effective", value=stderr.strip()[-400:])


def check_composed(layout: TesterLayout, config_text: str, roots: Sequence[Path], binary: Path,
                   env: Mapping[str, str], *, model: str = review.DEFAULT_MODEL, effort: str = review.DEFAULT_EFFORT,
                   run: Callable[..., review.RunResult] | None = None, timeout: float = LISTING_TIMEOUT) -> None:
    """The composed-configuration launch control (D4, D5): compose through the ``run`` seam, check the home again
    (the composing command writes Codex state into it), then compare with :func:`check_effective`. A separate
    control from :func:`check_launch`; a caller runs both before each launch. ``roots``, ``model`` and ``effort``
    are the ones :func:`build` was given (``Built.roots``); the first refusal stops the rest."""
    composed = _compose_config(layout, binary, env, model=model, effort=effort, timeout=timeout, run=run)
    check_home(layout, config_text)
    check_effective(composed, layout, roots, model=model, effort=effort)


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
    #: The digest of the credential copy :func:`build` wrote (what the copy-back compares against); ``""`` when unknown.
    credential_digest: str = ""
    #: The model and effort ``argv`` was built with, so a launch rebuilds and checks the same command.
    model: str = review.DEFAULT_MODEL
    effort: str = review.DEFAULT_EFFORT


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
                 child_environment(layout, binary, ownership_tag, inherited), digest, model, effort)


# ---------------------------------------------------------------------------
# The probes (D5, D6, D7)
# ---------------------------------------------------------------------------

#: Each boundary the probe command attempts, and the result that means it held. ``PROBE <name> <RESULT>``.
PROBE_EXPECTED: Mapping[str, str] = {
    "tracked": "REFUSED", "git": "REFUSED", "newfile": "REFUSED", "rename": "REFUSED", "delete": "REFUSED",
    "scratch": "WROTE", "home": "REFUSED",
    "canary_file": "BLOCKED", "canary_env": "EMPTY", "credential": "BLOCKED",
    "push": "REFUSED", "gh_name": "STANDIN", "gh_path": "FAILED", "tcp": "BLOCKED", "unix": "BLOCKED",
}

#: Probes whose line must be present but whose result is informational. A file created directly in a directory that
#: is an ancestor of a listed root (the job folder, the runtime root, the operator's home) reports success but lands
#: on a private layer, and ``/tmp`` and ``/var/tmp`` writes were refused, although a private mount would accept them;
#: measured on 0.163.0 (tester_sandbox.txt). The live test asserts the host path stays absent, which is the boundary.
PROBE_INFORMATIONAL: Mapping[str, frozenset[str]] = {
    name: frozenset({"WROTE", "REFUSED"}) for name in ("tmp", "vartmp", "sibling")
}

_PROBE_LINE_RE = re.compile(r"^PROBE (?P<name>[a-z_]+) (?P<result>[A-Z0-9]+)$", re.MULTILINE)


@dataclasses.dataclass(frozen=True)
class ProbeTargets:
    """Where the probe command aims. Every path is absolute and outside nothing the sandbox lists as readable
    except the clone; ``push_origin`` is a bare repository the live test makes readable through an extra root."""

    clone: Path
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
    """The one shell command both probe runs execute: a real attempt per boundary, every path absolute. Each line
    is ``PROBE <name> <RESULT>``; the command exits 0 whatever the attempts did."""
    q = shlex.quote
    clone = targets.clone
    tracked = clone / targets.tracked
    py_tcp = f"import socket;socket.create_connection(('127.0.0.1',{targets.tcp_port}),3)"
    py_unix = f"import socket;s=socket.socket(socket.AF_UNIX);s.settimeout(3);s.connect({str(targets.socket_path)!r})"
    return "\n".join([
        'w() { if ( : > "$2" ) 2>/dev/null; then echo "PROBE $1 WROTE"; else echo "PROBE $1 REFUSED"; fi; }',
        f"w tracked {q(str(tracked))}",
        f"w git {q(str(clone / '.git' / 'config'))}",
        f"w newfile {q(str(clone / 'probe-new-file'))}",
        f"w scratch {q(str(targets.scratch / 'probe'))}",
        f"w sibling {q(str(targets.sibling))}",
        f"w home {q(str(targets.stand_in_home / 'probe'))}",
        f"w tmp {q('/tmp/' + marker)}",
        f"w vartmp {q('/var/tmp/' + marker)}",
        f"echo x > {q(str(targets.scratch / 'rename-src'))} 2>/dev/null",
        f"if mv {q(str(targets.scratch / 'rename-src'))} {q(str(tracked))} 2>/dev/null; "
        f'then echo "PROBE rename WROTE"; else echo "PROBE rename REFUSED"; fi',
        f"if rm {q(str(tracked))} 2>/dev/null && [ ! -e {q(str(tracked))} ]; "
        f'then echo "PROBE delete WROTE"; else echo "PROBE delete REFUSED"; fi',
        f"if cat {q(str(targets.canary_file))} >/dev/null 2>&1; then echo \"PROBE canary_file READ\"; "
        f'else echo "PROBE canary_file BLOCKED"; fi',
        f'if [ -n "${{{targets.canary_env}:-}}" ]; then echo "PROBE canary_env SET"; else echo "PROBE canary_env EMPTY"; fi',
        f"if cat {q(str(targets.credential))} >/dev/null 2>&1; then echo \"PROBE credential READ\"; "
        f'else echo "PROBE credential BLOCKED"; fi',
        f"if git -C {q(str(clone))} push {q(str(targets.push_origin))} HEAD:refs/heads/probe >/dev/null 2>&1; "
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


def sandbox_probe_argv(layout: TesterLayout, *, binary: Path, command: str) -> list[str]:
    """The modelless form of D6a: ``codex sandbox -P tester -C <clone> -c <shell policy> -- sh -c <command>``,
    run with the tester's ``CODEX_HOME`` and environment. No ``-s`` and no other profile selector."""
    out = [str(binary), "sandbox", "-P", PROFILE, "-C", str(layout.clone)]
    for item in shell_policy_overrides(layout):
        out += ["-c", item]
    return out + ["--", "sh", "-c", command]
