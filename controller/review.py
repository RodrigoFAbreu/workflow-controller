"""The review-only harness seam (`workflow-controller-codex-reviewer` CP2).

A *review harness* turns a review bundle into a verdict file, in a disposable
copy of the worktree, and nothing else: it never touches the repository, the
Workflow state or a job record. ``HARNESSES`` is a closed registry; this
checkpoint defines one implementation, Codex, and **nothing calls it yet**
(the review job is CP3).

What lives here:

- the bundle values and the exact verdict header check (D5);
- the disposable copy, which never copies ``.git`` (D4);
- the real-repository before/after guard (D4, detection);
- the enforced boundary around the Codex process (D4, prevention): a
  Controller-written minimal Codex home checked before every invocation, the
  exact environment, the pinned command line, the feature allowlist, the
  configuration layers outside the home, the credential copy and its copy-back;
- the bounded runner (own session, ownership tag, deadline, group kill).

**Pre-step status.** D12's operator pre-step was run against ``codex-cli
0.162.1`` on 2026-10-15 (seven real ``codex exec`` runs, the sandbox probe, the
composing command, the home's state entries, the environment and the flags). Its
captures are under ``tests/harness_contract/codex/`` and every constant below is
taken from them; ``tests/test_fake_codex_contract.py`` pins the digests and the
captured invocation. ``PRE_STEP_CAPTURED`` is therefore true and ``HARNESSES``
registers Codex. A Codex upgrade that renames a feature, a layer type or a state
entry fails closed with that name.
"""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path

from . import gitrepo, runtime, usage
from .anchor import OWNERSHIP_VAR
from .errors import ReviewLaunchRefusedError

# ---------------------------------------------------------------------------
# The pinned boundary
# ---------------------------------------------------------------------------

#: The Codex version the constants below were taken from.
PINNED_VERSION = "codex-cli 0.162.1"

#: True since D12's operator pre-step (a real ``codex exec`` capture, the
#: sandbox probe, and the command that composes the configuration layers) was
#: recorded under ``tests/harness_contract/codex/``. While false the registry is
#: empty and the harness cannot be selected.
PRE_STEP_CAPTURED = True


DEFAULT_MODEL = "gpt-6.1-sol"
DEFAULT_EFFORT = "high"
#: The non-fast service tier (D10): ``compose.txt`` shows it composing as
#: ``service_tier="default"`` and ``thread/start`` reporting ``serviceTier: null``
#: (standard speed). The key is a free string, so control (b) compares the value.
SERVICE_TIER = "default"

#: Features the pinned Codex reports as enabled by default that a review may
#: leave on: the shell and file tools a reviewer needs, plus inert non-tool
#: features and ones the version reports ``removed``. ``code_mode_host`` is a
#: tool: gpt-6.1-sol's catalog is ``tool_mode="code_mode_only"``, so with it
#: disabled every shell and file call fails (``sandbox_probe.txt`` P1). The
#: collaboration (sub-agent) tools are an allowed tool family too: no flag or
#: ``-c`` key removes them, and a spawned sub-agent inherits the sandbox, the
#: network-off policy, the model and the effort (``sandbox_probe.txt`` P3). The
#: review prompt discourages spawning and the Controller reports each spawn with
#: its token usage (ADR 0013). Taken from
#: ``tests/harness_contract/codex/features_list.0.162.1.txt`` and ``flags.txt``.
TOOL_ALLOWLIST = frozenset({
    "code_mode_host", "shell_tool", "unified_exec", "unified_exec_tty", "view_image",
    "api_key_model_discovery", "auth_elicitation", "compaction_image_budget", "content_item_kinds",
    "enable_request_compression", "mentions_v2",
    # reported ``removed``: inert, cannot be switched off
    "collaboration_modes", "item_ids", "resize_all_images", "sqlite", "steer", "terminal_resize_reflow",
    "tool_search_always_defer_mcp_tools", "tui_app_server", "unified_exec_zsh_fork",
})

#: Every other feature the pinned Codex reports as enabled, switched off by
#: ``--disable``. A browser, CDP or computer-use tool reaches the network or
#: the desktop outside the command sandbox; plugin, remote-plugin and skill
#: dependency installation are further tool and network paths. Every name is
#: accepted by the pinned Codex and an unknown ``--disable`` name exits 1, so the
#: set fails closed on a rename (``flags.txt``).
DISABLED_FEATURES = tuple(sorted({
    "apps", "browser_annotation_api", "browser_use", "browser_use_external", "browser_use_full_cdp_access",
    "computer_use", "daemon_auto_start", "fast_mode", "goals", "guardian_approval",
    "guardian_reuse_parent_compaction", "hooks", "image_generation", "in_app_browser", "in_app_chat",
    "in_app_dictation", "in_app_local_automation", "in_app_updates", "in_app_voice", "instant_interrupt",
    "multi_agent", "plugin_sharing", "plugins", "realtime_conversation", "remote_plugin", "shell_snapshot",
    "skill_mcp_dependency_install", "skill_search", "sleep_tool", "system_proxy_fallback",
    "tool_call_mcp_elicitation", "tool_suggest", "ultrafast_mode", "unbounded_connection_retries",
    "workspace_dependencies", "worktrees", "write_stdin_approval",
}))

#: The one Controller-written ``config.toml`` of the minimal home: the pinned
#: overrides and no ``mcp_servers``, ``hooks``, ``notify``, ``profiles``,
#: ``plugins`` or project-trust table.
HOME_CONFIG_TOML = (
    "# Written by the Controller for one review job; never edited.\n"
    'model_provider = "openai"\n'
    'project_root_markers = [".git"]\n'
)

#: Configuration-bearing entries Codex reads at startup (D4, I6): none of them
#: may exist in the minimal home beyond ``config.toml`` and what
#: ``CODEX_STARTUP_SUBENTRIES`` lists.
STARTUP_LOADED = ("config.toml", "hooks", "hooks.json", "rules", "skills", "plugins", "prompts", "instructions.md",
                  "AGENTS.md", "AGENTS.override.md", "managed_config.toml", "requirements.toml")

#: The entries Codex itself writes into an empty home, none of which is read
#: as configuration: exactly the names ``state_entries.txt`` observed from
#: ``mcp list``, ``features list``, ``app-server`` and ``exec`` under the pinned
#: command line, so the set fails closed. (``.tmp/`` appears only with the
#: plugins feature on; the state databases follow the suffix rule below.)
CODEX_STATE_ENTRIES = frozenset({
    "auth.json", "sessions", "installation_id", "models_cache.json", "tmp", "thread-writer-locks",
})

#: Exact child paths Codex creates below ``skills/`` and ``plugins/`` in an
#: empty home. None: with ``skills.bundled.enabled=false`` no ``skills/`` or
#: ``plugins/`` entry is created, and without the override the bundled skills
#: (``skills/.system``) would be loaded into the model context, which D4 says
#: must stop the launch.
CODEX_STARTUP_SUBENTRIES: frozenset[str] = frozenset()

#: ``*.sqlite``, ``-wal`` and ``-shm`` state databases.
_STATE_DB_SUFFIXES = (".sqlite", ".sqlite-wal", ".sqlite-shm", ".sqlite3", "-wal", "-shm")

#: The layers outside the home that must be absent or empty (control (a)).
SYSTEM_LAYER_FILES = ("config.toml", "managed_config.toml", "requirements.toml")
SYSTEM_LAYER_DIR = Path("/etc/codex")

#: One disposition per ``ConfigLayerSource`` type ``config/read`` reports
#: (``compose.txt``): ``excluded`` (by construction: the layer is the Controller's
#: own minimal home or is disabled), ``absent`` (file-backed outside the home: the
#: layer, when listed, must carry no configuration), ``composed`` (cannot be read
#: here; the composed-values and origin checks cover it) or ``own`` (the
#: Controller's own ``-c`` layer). A layer type not in this table refuses.
CONFIG_SOURCES = {
    "packagedDefaults": "composed", "mdm": "composed", "system": "absent", "enterpriseManaged": "composed",
    "user": "excluded", "project": "excluded", "sessionFlags": "own",
    "legacyManagedConfigTomlFromFile": "absent", "legacyManagedConfigTomlFromMdm": "composed",
}

#: Sources that are not configuration layers and the control over each
#: (``compose.txt``): rules the ``--ignore-rules`` flag, ``AGENTS*.md`` the empty
#: ``instructionSources``, skills the bundled-skills override plus the stripped
#: project ``.agents/skills``, plugins and hooks the disabled features,
#: ``requirements.toml`` control (a) (``configRequirements/read`` returned
#: ``requirements: null``).
NON_LAYER_SOURCES = {
    "execpolicy rules": "--ignore-rules (exec only)",
    "AGENTS*.md": "thread/start instructionSources is empty",
    "skills": "skills.bundled.enabled=false and STRIPPED_COPY_PATHS",
    "plugins": "the plugins feature is disabled and the home has no plugins/",
    "hooks": "the hooks feature is disabled and the home has no hooks.json",
    "requirements.toml": "control (a): absent or empty under /etc/codex",
}

#: The scalar values the composed ``config`` must equal (control (b)). The sandbox
#: table, the project trust table and the model, effort and tier are built per
#: launch by :func:`effective_policy`.
EFFECTIVE_POLICY = {
    "model_provider": "openai",
    "sandbox_mode": "workspace-write",
    "approval_policy": "never",
    "web_search": "disabled",
    "notify": [],
    "mcp_servers": {},
    "hooks": {},
    "project_root_markers": [".git"],
    "skills.bundled.enabled": False,
}
#: Top-level keys that no layer other than the packaged defaults may set. An
#: empty table or a null is unset; ``chatgpt_base_url`` always reports its packaged
#: default value, which carries no origin.
FORBIDDEN_EFFECTIVE_KEYS = ("model_providers", "openai_base_url", "chatgpt_base_url", "auto_review",
                            "permissions", "default_permissions", "profiles", "profile")

#: The child environment is exactly these names (control (d)).
ENV_ALLOWLIST = ("PATH", "HOME", "TMPDIR", "CODEX_HOME", "GIT_CEILING_DIRECTORIES", "LANG", "LC_ALL", OWNERSHIP_VAR)
SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"

#: Project-local discovery removed from the copy (control (e)).
STRIPPED_COPY_PATHS = (".codex", ".agents/skills", ".agents/plugins", "AGENTS.override.md")

#: Copy exclusions (D4). ``.git`` is never copied.
COPY_EXCLUDED_NAMES = frozenset({".git", ".controller", "__pycache__", "build", "node_modules", ".gradle", ".venv",
                                 ".mypy_cache", ".pytest_cache", ".ruff_cache"})

#: What each invocation kind accepts (control (b)): ``features`` and ``mcp``
#: reject ``--strict-config``, so each kind has its own pinned flag list.
KIND_FLAGS = {
    "exec": ("-c", "--disable", "--strict-config", "--ignore-rules", "-C", "-s", "-m", "-o", "--color"),
    "features": ("-c", "--disable"),
    "mcp": ("-c", "--disable"),
    "app-server": ("-c", "--disable", "--strict-config", "--listen"),
}


# ---------------------------------------------------------------------------
# Stages, the bundle values and the verdict check (D5)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Stage:
    """One external review stage a harness serves."""

    name: str
    phase: str
    reviewer_role: str
    revision_key: str
    revision_label: str


STAGES = {
    "plan": Stage("plan", "AWAITING_MANUAL_EXTERNAL_PLAN_REVIEW", "MANUAL_EXTERNAL_PLAN_REVIEW",
                  "plan_revision", "Plan revision reviewed:"),
    "implementation": Stage("implementation", "AWAITING_MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW",
                            "MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", "implementation_revision",
                            "Implementation revision reviewed:"),
}

STATUSES = ("APPROVE", "REVISE", "BLOCK")


def parse_manifest(text: str) -> dict[str, str]:
    """The machine ``key: value`` lines of a bundle's ``MANIFEST.md`` (those
    before its first ``##`` heading)."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        if line.startswith("## "):
            break
        match = re.fullmatch(r"([a-z_]+):\s*(.*?)\s*", line)
        if match and match.group(1) not in values:
            values[match.group(1)] = match.group(2)
    return values


#: The single mapping from ``MANIFEST.md`` keys to verdict header labels, one
#: row per label. The stage revision row is added per stage by
#: :func:`expected_headers`.
HEADER_TABLE = (
    ("bundle_id", "Reviewed bundle ID:"),
    ("base_commit", "Reviewed base commit:"),
    ("work_item_id", "Work item:"),
    ("review_content_id", "Reviewed review content ID:"),
    ("review_content_id", "Reviewed review_content_id:"),
)


def expected_headers(manifest: Mapping[str, str], stage: str, model: str) -> dict[str, str]:
    """The exact header values a verdict for this bundle must carry, keyed by
    label. ``Reviewer role`` is fixed by the stage; ``Reviewer model`` is
    ``openai/<model>``. A bundle value that is missing refuses."""
    spec = STAGES[stage]
    expected: dict[str, str] = {}
    for key, label in (*HEADER_TABLE, (spec.revision_key, spec.revision_label)):
        value = manifest.get(key, "")
        if not value:
            raise ReviewLaunchRefusedError(f"the bundle's MANIFEST.md has no {key!r} line", evidence={
                "check": "manifest", "entry": key})
        expected[label] = value
    expected["Reviewer role:"] = spec.reviewer_role
    expected["Reviewer model:"] = f"openai/{model}"
    return expected


@dataclasses.dataclass(frozen=True)
class HeaderMismatch:
    label: str
    expected: str
    found: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class VerdictCheck:
    status: str | None
    mismatches: tuple[HeaderMismatch, ...]

    @property
    def ok(self) -> bool:
        return self.status in STATUSES and not self.mismatches


def verify_verdict(text: str, expected: Mapping[str, str]) -> VerdictCheck:
    """Compare the verdict's header block, **exactly**, against ``expected``
    (the C6 lesson: Codex dropped five characters of an id). The header block
    is every line before the first ``##`` heading. A label must occur exactly
    once with exactly the expected value; a missing, repeated or differing line
    is a mismatch. The Controller never edits a verdict or composes a header."""
    header: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            break
        header.append(line)
    statuses = [m.group(1) for line in header if (m := re.fullmatch(r"Status:\s*(\S.*?)\s*", line))]
    status = statuses[0] if len(statuses) == 1 and statuses[0] in STATUSES else None
    mismatches = []
    for label, want in expected.items():
        found = tuple(line[len(label):].strip() for line in header if line.startswith(label))
        if found != (want,):
            mismatches.append(HeaderMismatch(label, want, found))
    if status is None:
        mismatches.append(HeaderMismatch("Status:", "|".join(STATUSES), tuple(statuses)))
    return VerdictCheck(status, tuple(mismatches))


def mismatch_note(check: VerdictCheck) -> str:
    """The exact-values note of D5's one bounded retry."""
    lines = ["Your previous verdict's header lines did not equal the required values. Rewrite the verdict with "
             "these header lines exactly, character for character:"]
    lines += [f"- {m.label} {m.expected}" for m in check.mismatches if m.label != "Status:"]
    if any(m.label == "Status:" for m in check.mismatches):
        lines.append("- Status: APPROVE, REVISE or BLOCK, exactly one line")
    return "\n".join(lines)


def build_prompt(stage: str, expected: Mapping[str, str], *, bundle_dir: str, note: str | None = None) -> str:
    """The review prompt, built only from the bundle's own values."""
    spec = STAGES[stage]
    header = "\n".join(f"{label} {value}" for label, value in expected.items())
    text = (
        f"You are the external {spec.name} reviewer. Review the bundle in `{bundle_dir}` (start with its "
        "`MANIFEST.md` and `REVIEW_REQUEST.md`) against the files in the current directory. You may read files and "
        "run read-only commands; do not modify any file in this tree. Do the review yourself: do not spawn "
        "sub-agents or delegate any part of it.\n\n"
        "Write the verdict as Markdown whose first lines are exactly this header, then the sections "
        "`## Blocking findings`, `## Important findings`, `## Optional findings`, `## Missing tests` and "
        "`## Required acceptance criteria`:\n\n"
        f"# Review Decision\n\nStatus: APPROVE or REVISE or BLOCK (exactly one)\n{header}\n")
    return text + (f"\n{note}\n" if note else "")


# ---------------------------------------------------------------------------
# The disposable copy (D4)
# ---------------------------------------------------------------------------


def job_dir(job_id: str) -> str:
    """The job's directory relative to the runtime root."""
    return f"reviews/{job_id}"


def _copy_skip(excluded_roots: Sequence[str]) -> Callable[[str, bool], bool]:
    roots = set(excluded_roots)

    def skip(rel: str, is_dir: bool) -> bool:
        name = rel.rsplit("/", 1)[-1]
        if rel in roots or name in COPY_EXCLUDED_NAMES or name.endswith(".egg-info"):
            return True
        return False

    return skip


def build_copy(worktree: Path, runtime_root: Path, job_id: str) -> Path:
    """Build ``<runtime>/reviews/<job id>/tree``: the worktree's bytes as they
    are now (staged, unstaged and untracked alike), without ``.git``,
    ``.controller``, the runtime directory itself, caches and build output,
    without project-local Codex discovery (control (e)), and with its own
    private one-commit repository so ``git diff`` in the copy shows nothing and
    no enclosing repository can be discovered."""
    worktree = worktree.resolve()
    try:
        runtime_rel = str(runtime_root.resolve().relative_to(worktree))
    except ValueError:
        runtime_rel = ""
    tree_rel = f"{job_dir(job_id)}/tree"
    tree = runtime.copy_tree(runtime_root, worktree, tree_rel, skip=_copy_skip([runtime_rel] if runtime_rel else []))
    for rel in STRIPPED_COPY_PATHS:
        runtime.remove_tree(runtime_root, f"{tree_rel}/{rel}")
    env = _git_env(tree.parent)
    for args in (["init", "-q"], ["add", "-A", "-f"],
                 ["-c", "user.name=review", "-c", "user.email=review@invalid", "-c", "commit.gpgsign=false",
                  "-c", "core.hooksPath=/dev/null", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
                  "commit", "-q", "--allow-empty", "-m", "review copy"]):
        result = subprocess.run(["git", "-C", str(tree), *args], capture_output=True, stdin=subprocess.DEVNULL,
                                env=env, check=False)
        if result.returncode != 0:
            raise ReviewLaunchRefusedError("could not make the review copy's private repository", evidence={
                "check": "copy", "value": result.stderr.decode("utf-8", "replace").strip()})
    return tree


def _git_env(ceiling: Path) -> dict[str, str]:
    return {"PATH": SYSTEM_PATH, "HOME": str(ceiling), "LC_ALL": "C", "GIT_CEILING_DIRECTORIES": str(ceiling),
            "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}


def settle_job_dir(runtime_root: Path, job_id: str, *, keep_sessions: bool = True) -> None:
    """Remove everything of the job except the retained ``codex-home/sessions``
    (the rollout a later admission must still see, D4). Safe to call on every
    path and more than once."""
    base = job_dir(job_id)
    for rel in ("tree", "home", "tmp", "verdict.md", "prompt.md"):
        runtime.remove_tree(runtime_root, f"{base}/{rel}")
    home_rel = f"{base}/codex-home"
    if keep_sessions and (runtime_root / home_rel / "sessions").is_dir():
        runtime.remove_tree(runtime_root, home_rel, keep=("sessions",))
    else:
        runtime.remove_tree(runtime_root, home_rel)


# ---------------------------------------------------------------------------
# The real-repository before/after guard (D4, detection)
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(1 << 20), b""):
                digest.update(block)
    except OSError:
        return None
    return digest.hexdigest()


def _tree_digest(root: Path | None) -> str | None:
    if root is None or not root.is_dir():
        return None
    entries = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        entries.append(f"{path.relative_to(root)}\0{_sha256_file(path)}")
    return hashlib.sha256("\n".join(entries).encode()).hexdigest()


def snapshot_repository(root: Path, *, feedback_dir: Path | None = None, state_identity: str | None = None,
                        exclude: Sequence[str] = ()) -> dict:
    """What the guard compares: ``HEAD``, ``status --porcelain``,
    ``for-each-ref``, the repository ``config`` digest, a manifest of every
    non-ignored worktree file (path, size, SHA-256: a second edit to an already
    modified file shows, which an unchanged ``status`` cannot), the real
    feedback directory's digest and the protocol's state identity. Git is run
    without optional locks, so the snapshot does not write ``.git``."""
    quiet = ["--no-optional-locks", "-c", "core.fsmonitor=false"]
    head = gitrepo._git(root, [*quiet, "rev-parse", "HEAD"], None).strip()
    status = gitrepo._git(root, [*quiet, "status", "--porcelain=v1", "-z"], None)
    if exclude:  # the job's own directory, and a collapsed untracked parent of it, are not the worktree's change
        status = "\0".join(entry for entry in status.split("\0") if not any(
            entry[3:].rstrip("/") == e or entry[3:].startswith(f"{e}/")
            or (entry[3:].endswith("/") and f"{e}/".startswith(entry[3:]))
            for e in exclude if entry[3:]))
    refs = gitrepo._git(root, [*quiet, "for-each-ref", "--format=%(refname) %(objectname)"], None)
    listing = gitrepo._git(root, [*quiet, "ls-files", "-z", "--cached", "--others", "--exclude-standard"], None)
    manifest = {}
    for rel in sorted(set(filter(None, listing.split("\0")))):
        if any(rel == e or rel.startswith(f"{e}/") for e in exclude):
            continue
        path = root / rel
        try:
            size = path.lstat().st_size
        except OSError:
            manifest[rel] = None
            continue
        manifest[rel] = [size, _sha256_file(path) if path.is_file() and not path.is_symlink() else None]
    common = gitrepo._git(root, [*quiet, "rev-parse", "--git-common-dir"], None).strip()
    common_dir = (root / common).resolve()
    return {"head": head, "status": status, "refs": refs, "files": manifest, "state_identity": state_identity,
            "config": _sha256_file(common_dir / "config"), "feedback": _tree_digest(feedback_dir)}


def compare_snapshots(before: Mapping, after: Mapping) -> list[str]:
    """The names of the snapshot facts that differ; any difference voids the
    verdict (``INCOMPLETE``, never ingested)."""
    differing = [key for key in ("head", "status", "refs", "config", "feedback", "state_identity")
                 if before.get(key) != after.get(key)]
    changed_files = sorted(set(before["files"]) ^ set(after["files"])
                           | {p for p in before["files"] if p in after["files"] and before["files"][p] != after["files"][p]})
    return differing + [f"file:{p}" for p in changed_files]


# ---------------------------------------------------------------------------
# The environment, the home and the command line
# ---------------------------------------------------------------------------


def _refuse(message: str, **evidence) -> ReviewLaunchRefusedError:
    return ReviewLaunchRefusedError(message, evidence=evidence)


def interpreter_dir(binary: Path) -> Path | None:
    """The directory of the interpreter a script binary's ``#!`` line names
    (the ``node`` of the installed ``codex.js``), or ``None``."""
    try:
        with binary.resolve().open("rb") as fh:
            first = fh.readline(512).decode("utf-8", "replace")
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    parts = first[2:].split()
    if not parts:
        return None
    interpreter = parts[0]
    if interpreter.endswith("/env") and len(parts) > 1:
        return None  # resolved through the PATH built from the binary's own directory
    return Path(interpreter).parent


def resolve_binary(name: str) -> Path:
    """The path of ``reviewer.binary``: a path as given, or a name looked up on
    the Controller's own ``PATH``. Refuses what cannot be resolved. The symlink is
    deliberately **not** resolved (``env.txt``): its directory also holds the
    ``node`` the ``#!/usr/bin/env node`` launcher needs, and the resolved
    ``codex.js`` directory does not."""
    candidate = Path(name)
    if candidate.is_absolute() or "/" in name:
        resolved = candidate
    else:
        resolved = next((Path(d) / name for d in os.environ.get("PATH", "").split(":")
                         if d and (Path(d) / name).is_file()), candidate)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise _refuse(f"the reviewer binary {name!r} cannot be resolved", check="binary", entry=name)
    return resolved


def child_environment(layout: "JobLayout", binary: Path, ownership_tag: str, inherited: Mapping[str, str] | None = None
                      ) -> dict[str, str]:
    """The child environment: exactly :data:`ENV_ALLOWLIST`. ``PATH`` is the
    binary's directory, its interpreter's directory and the system directories;
    the ownership tag is the job id appended to any tag already inherited, as
    ``worker.launch`` does, so ``resume`` finds the group by tag."""
    inherited = os.environ if inherited is None else inherited
    dirs = [str(binary.parent)]
    interp = interpreter_dir(binary)
    if interp is not None and str(interp) not in dirs:
        dirs.append(str(interp))
    tags = inherited.get(OWNERSHIP_VAR)
    return {
        "PATH": ":".join([*dirs, SYSTEM_PATH]),
        "HOME": str(layout.home), "TMPDIR": str(layout.tmp), "CODEX_HOME": str(layout.codex_home),
        "GIT_CEILING_DIRECTORIES": str(layout.base), "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        OWNERSHIP_VAR: f"{tags}:{ownership_tag}" if tags else ownership_tag,
    }


@dataclasses.dataclass(frozen=True)
class JobLayout:
    """Where one review job's files live under ``<runtime>/reviews/<job id>/``."""

    runtime_root: Path
    job_id: str

    @property
    def base(self) -> Path:
        return self.runtime_root / job_dir(self.job_id)

    @property
    def tree(self) -> Path:
        return self.base / "tree"

    @property
    def home(self) -> Path:
        return self.base / "home"

    @property
    def tmp(self) -> Path:
        return self.base / "tmp"

    @property
    def codex_home(self) -> Path:
        return self.base / "codex-home"

    @property
    def verdict(self) -> Path:
        return self.base / "verdict.md"

    def rel(self, *parts: str) -> str:
        return "/".join([job_dir(self.job_id), *parts])


def _overrides(kind: str, *, model: str, effort: str, tmpdir: Path | None, tree: Path | None) -> list[str]:
    """The ``-c`` overrides of an invocation kind, in the order they are emitted.
    ``exec`` and ``app-server`` (the composing command) carry the same set, so
    the composed configuration is the one the review runs under. Every item was
    accepted under ``--strict-config`` (``exec_argv.json``); the project trust
    table uses the inline-table form, because the dotted quoted form is rejected."""
    overrides = ['model_provider="openai"', "notify=[]", 'web_search="disabled"', 'project_root_markers=[".git"]']
    if kind in ("exec", "app-server"):
        overrides += [f'model="{model}"', f'model_reasoning_effort="{effort}"', f'service_tier="{SERVICE_TIER}"',
                      'approval_policy="never"', 'sandbox_mode="workspace-write"',
                      "sandbox_workspace_write.network_access=false",
                      "sandbox_workspace_write.exclude_slash_tmp=true",
                      "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                      f"sandbox_workspace_write.writable_roots={json.dumps([str(tmpdir)] if tmpdir else [])}",
                      f"projects={{{json.dumps(str(tree))}={{trust_level=\"untrusted\"}}}}" if tree else "projects={}",
                      "skills.bundled.enabled=false"]
    return overrides


def argv(kind: str, *, binary: Path, tree: Path | None = None, verdict: Path | None = None, model: str = DEFAULT_MODEL,
         effort: str = DEFAULT_EFFORT, tmpdir: Path | None = None, prompt: str | None = None) -> list[str]:
    """The only constructor of a Codex command line. Every invocation kind is
    built from its own pinned flag list; explicit overrides outrank the
    operator's own configuration. ``features`` and ``mcp list`` take the
    overrides they accept and never ``--strict-config``; ``app-server`` is the
    command that composes the configuration layers (:func:`compose_config`)."""
    flags = KIND_FLAGS[kind]
    out = [str(binary)]
    if kind == "features":
        out += ["features", "list"]
    elif kind == "mcp":
        out += ["mcp", "list"]
    else:
        out += [kind]
    if "--strict-config" in flags:
        out.append("--strict-config")
    for feature in DISABLED_FEATURES:
        out += ["--disable", feature]
    for item in _overrides(kind, model=model, effort=effort, tmpdir=tmpdir, tree=tree):
        out += ["-c", item]
    if kind == "exec":
        out += ["-s", "workspace-write", "-C", str(tree), "--ignore-rules", "--color", "never", "-o", str(verdict)]
        out.append(prompt if prompt is not None else "-")
    elif kind == "app-server":
        out += ["--listen", "stdio://"]
    return out


def required_overrides(kind: str, *, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT,
                       tmpdir: Path | None = None, tree: Path | None = None) -> tuple[str, ...]:
    """The overrides a launch must find in an argv (the argv contract): the
    complete set, model, effort, tier, sandbox table, project trust and bundled
    skills included."""
    return tuple(_overrides(kind, model=model, effort=effort, tmpdir=tmpdir, tree=tree))


def check_argv(kind: str, command: Sequence[str], *, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT,
               tmpdir: Path | None = None, tree: Path | None = None, verdict: Path | None = None) -> None:
    """Refuse a launch whose command line omits a pinned item: a disable for any
    feature of :data:`DISABLED_FEATURES`, an override of :func:`required_overrides`
    (and any ``-c`` that is not one of them, so a later duplicate cannot win),
    the sandbox, the working copy (``-C``), the rules switch, the verdict path, a
    profile argument, a flag the invocation kind does not accept, or
    ``--approve-for-me``."""
    present_disables = {command[i + 1] for i, a in enumerate(command[:-1]) if a == "--disable"}
    for feature in DISABLED_FEATURES:
        if feature not in present_disables:
            raise _refuse(f"the command line does not disable the {feature!r} feature", check="argv", entry=feature)
    if "code_mode_host" in present_disables:
        raise _refuse("the command line disables the 'code_mode_host' tool", check="argv", entry="code_mode_host")
    present = [command[i + 1] for i, a in enumerate(command[:-1]) if a == "-c"]
    required = required_overrides(kind, model=model, effort=effort, tmpdir=tmpdir, tree=tree)
    for item in required:
        if item not in present:
            raise _refuse(f"the command line omits the pinned override {item!r}", check="argv", entry=item)
    for item in present:
        if item not in required:
            raise _refuse(f"the command line carries the unpinned override {item!r}", check="argv", entry=item)
    banned = {"-p", "--profile", "--approve-for-me", "--dangerously-bypass-approvals-and-sandbox",
              "--dangerously-bypass-hook-trust", "--add-dir", "--oss", "--ephemeral", "--enable"}
    for arg in command[1:]:
        if arg in banned:
            raise _refuse(f"the command line carries {arg!r}", check="argv", entry=arg)

    def value_of(flag: str) -> str | None:
        return command[command.index(flag) + 1] if command.count(flag) == 1 and command.index(flag) + 1 < len(command) \
            else None

    if kind == "exec":
        if command.count("-s") != 1 or value_of("-s") != "workspace-write":
            raise _refuse("the command line does not select the workspace-write sandbox", check="argv", entry="-s")
        if "--strict-config" not in command:
            raise _refuse("the command line omits --strict-config", check="argv", entry="--strict-config")
        if "--ignore-rules" not in command:
            raise _refuse("the command line omits --ignore-rules", check="argv", entry="--ignore-rules")
        if tree is None or value_of("-C") != str(tree):
            raise _refuse("the command line does not run in the review copy (-C)", check="argv", entry="-C")
        if verdict is None or value_of("-o") != str(verdict):
            raise _refuse("the command line does not write the verdict to the job's file (-o)", check="argv", entry="-o")
        if value_of("--color") != "never":
            raise _refuse("the command line does not pin --color never", check="argv", entry="--color")
    elif kind == "app-server":
        if "--strict-config" not in command:
            raise _refuse("the command line omits --strict-config", check="argv", entry="--strict-config")
        if value_of("--listen") != "stdio://":
            raise _refuse("the command line does not listen on stdio", check="argv", entry="--listen")
    elif "--strict-config" in command:
        raise _refuse(f"{kind!r} does not accept --strict-config", check="argv", entry="--strict-config")


def build_home(layout: JobLayout, operator_home: Path) -> str:
    """Write the Controller's minimal Codex home: a ``config.toml`` holding
    only the pinned overrides, a private writable copy of the operator's
    credential file (mode 0600, so a token refresh succeeds inside the review)
    and a ``sessions/`` directory. Returns the SHA-256 of the credential file
    as captured. An operator home without a credential file refuses."""
    source = operator_home / "auth.json"
    try:
        data = source.read_bytes()
    except OSError:
        raise _refuse("the operator's Codex home has no credential file", check="credentials", source=str(source)) from None
    runtime.write_private_bytes(layout.runtime_root, layout.rel("codex-home", "config.toml"), HOME_CONFIG_TOML.encode())
    runtime.write_private_bytes(layout.runtime_root, layout.rel("codex-home", "auth.json"), data)
    for sub in ("codex-home/sessions", "home", "tmp"):
        (layout.base / sub).mkdir(parents=True, exist_ok=True)
    return hashlib.sha256(data).hexdigest()


def check_home(layout: JobLayout) -> None:
    """The startup-load check, run before **every** Codex invocation, ``mcp
    list`` included: what Codex loads at startup is exactly the Controller's.
    ``config.toml`` equals the pinned digest and no other configuration-bearing
    entry exists (a ``STARTUP_LOADED`` match refuses unless it is exactly a
    path on :data:`CODEX_STARTUP_SUBENTRIES`); every other entry must be a
    pinned Codex-written state entry. An entry in neither set refuses the next
    launch and the error names it. The home is carried forward between
    invocations, never rebuilt."""
    home = layout.codex_home
    config = home / "config.toml"
    if _sha256_file(config) != hashlib.sha256(HOME_CONFIG_TOML.encode()).hexdigest():
        raise _refuse("the minimal Codex home's config.toml differs from the pinned file", check="home",
                      entry="config.toml", source=str(config))
    for entry in sorted(home.iterdir(), key=lambda p: p.name):
        name = entry.name
        if name == "config.toml":
            continue
        if name in ("skills", "plugins"):
            _check_startup_subentries(home, entry)
            continue
        if name in STARTUP_LOADED or name.startswith("hooks") or name.endswith(".toml"):
            raise _refuse(f"the minimal Codex home holds the startup-loaded entry {name!r}", check="home", entry=name)
        if name in CODEX_STATE_ENTRIES or name.endswith(_STATE_DB_SUFFIXES):
            continue
        raise _refuse(f"the minimal Codex home holds the unexpected entry {name!r}", check="home", entry=name)


def _check_startup_subentries(home: Path, top: Path) -> None:
    for child in sorted(top.iterdir(), key=lambda p: p.name):
        rel = f"{top.name}/{child.name}"
        if rel not in CODEX_STARTUP_SUBENTRIES:
            raise _refuse(f"the minimal Codex home holds the startup-loaded entry {rel!r}", check="home", entry=rel)


def check_outside_layers(system_dir: Path = SYSTEM_LAYER_DIR) -> None:
    """Control (a): every file-backed layer outside the home must be absent or
    empty. A path that exists with content, or cannot be read, refuses and is
    named."""
    for name in SYSTEM_LAYER_FILES:
        path = system_dir / name
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise _refuse(f"the configuration layer {path} cannot be read: {exc}", check="layer", source=str(path)) from None
        if data.strip():
            raise _refuse(f"the configuration layer {path} carries content", check="layer", source=str(path))


def parse_features(text: str) -> dict[str, bool]:
    """``codex features list`` output as ``{name: enabled}``."""
    features = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-1] in ("true", "false"):
            features[parts[0]] = parts[-1] == "true"
    return features


def check_features(listing: str) -> None:
    """The drift check: refuse when a feature is reported enabled that is not
    on the allowlist (a feature a Codex upgrade added or turned on stops the
    review until a person extends the allowlist or the disable set)."""
    for name, enabled in sorted(parse_features(listing).items()):
        if enabled and name not in TOOL_ALLOWLIST:
            raise _refuse(f"the feature {name!r} is enabled and is not allowlisted", check="features", entry=name)


def check_mcp_list(output: str) -> None:
    """Refuse unless ``mcp list`` reports zero servers."""
    if "No MCP servers configured" not in output:
        raise _refuse("`mcp list` reports configured servers", check="mcp", value=output.strip()[:400])


def flatten(value: object, prefix: str = "") -> dict[str, object]:
    """The leaves of ``value`` under dotted keys, lists by index, the way
    ``config/read`` keys its ``origins`` (``sandbox_workspace_write.writable_roots.0``).
    An empty table or list is itself a leaf."""
    out: dict[str, object] = {}
    if isinstance(value, dict) and value:
        for key, item in value.items():
            out.update(flatten(item, f"{prefix}.{key}" if prefix else key))
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            out.update(flatten(item, f"{prefix}.{index}" if prefix else str(index)))
    else:
        out[prefix] = value
    return out


def _lookup(config: Mapping, dotted: str) -> object:
    node: object = config
    for part in dotted.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


def _origin(origins: Mapping, key: str) -> object:
    """The origin ``config/read`` reports for ``key``: the leaf itself, or the
    first leaf below it. Origin keys embed project paths unescaped, so they are
    only ever matched by prefix, never split."""
    if key in origins:
        return origins[key]
    for name in sorted(origins):
        if name.startswith(f"{key}."):
            return origins[name]
    return None


def effective_policy(*, model: str, effort: str, tree: Path, tmpdir: Path) -> dict[str, object]:
    """Every value the composed ``config`` must equal for this launch: the scalar
    table, the model, effort and tier, the sandbox table whole and the project
    trust table whole."""
    wanted = dict(EFFECTIVE_POLICY)
    wanted.update({"model": model, "model_reasoning_effort": effort, "service_tier": SERVICE_TIER,
                   "sandbox_workspace_write": {"writable_roots": [str(tmpdir)], "network_access": False,
                                               "exclude_tmpdir_env_var": True, "exclude_slash_tmp": True},
                   "projects": {str(tree): {"trust_level": "untrusted"}}})
    return wanted


def check_effective(composed: Mapping, *, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT, tree: Path,
                    tmpdir: Path, codex_home: Path | None = None) -> None:
    """Control (b): ``composed`` is the ``config/read`` result with layers
    (``config``, ``origins``, ``layers``). Every key of :func:`effective_policy`
    must equal the pinned value (a table compared leaf by leaf, an unexpected leaf
    included), no layer may be of an unknown type or carry configuration where it
    must be absent, and none of :data:`FORBIDDEN_EFFECTIVE_KEYS` may be set by a
    layer other than the packaged defaults (an empty table or a null is unset).
    The error names the origin layer, the key and the value."""
    config = composed.get("config") if isinstance(composed, Mapping) else None
    origins = composed.get("origins") if isinstance(composed, Mapping) else None
    layers = composed.get("layers") if isinstance(composed, Mapping) else None
    if not isinstance(config, Mapping) or not isinstance(origins, Mapping) or not isinstance(layers, list):
        raise _refuse("the composed configuration is not a config/read result with layers", check="effective")
    _check_layers(layers, codex_home)
    for key, want in effective_policy(model=model, effort=effort, tree=tree, tmpdir=tmpdir).items():
        got = _lookup(config, key)
        if got == want or (want in ({}, []) and got is None):
            continue
        wanted_leaves, got_leaves = flatten(want, key), flatten(got, key)
        differing = [leaf for leaf in sorted({*wanted_leaves, *got_leaves})
                     if wanted_leaves.get(leaf, "<unset>") != got_leaves.get(leaf, "<unset>")]
        leaf = next((name for name in differing if name in got_leaves), differing[0] if differing else key)
        raise _refuse(f"the composed configuration sets {leaf} to {got_leaves.get(leaf, '<unset>')!r}, expected "
                      f"{wanted_leaves.get(leaf, '<unset>')!r}", check="effective", key=leaf,
                      value=got_leaves.get(leaf), source=_origin(origins, leaf))
    for name in sorted(origins):
        origin = origins[name]
        if isinstance(origin, Mapping) and origin.get("type") == "packagedDefaults":
            continue
        if name.split(".", 1)[0] in FORBIDDEN_EFFECTIVE_KEYS:
            raise _refuse(f"the composed configuration sets {name}", check="effective", key=name,
                          value=flatten(config).get(name), source=origin)


def _check_layers(layers: Sequence, codex_home: Path | None) -> None:
    for layer in layers:
        name = layer.get("name") if isinstance(layer, Mapping) else None
        kind = name.get("type") if isinstance(name, Mapping) else None
        if kind not in CONFIG_SOURCES:
            raise _refuse(f"the composed configuration has a layer of the unknown type {kind!r}", check="effective",
                          entry=str(kind), source=name)
        disposition = CONFIG_SOURCES[kind]
        if disposition == "absent" and layer.get("config"):
            raise _refuse(f"the {kind} layer carries configuration", check="effective", entry=kind, source=name)
        if kind == "project" and not layer.get("disabledReason"):
            raise _refuse("a project layer is enabled", check="effective", entry=kind, source=name)
        if kind == "user" and codex_home is not None and name.get("file") != str(codex_home / "config.toml"):
            raise _refuse("the user layer is not the minimal home's config.toml", check="effective", entry=kind,
                          source=name)


# ---------------------------------------------------------------------------
# The credential copy and its copy-back (D4)
# ---------------------------------------------------------------------------


def copy_back_credentials(layout: JobLayout, operator_home: Path, captured_digest: str) -> str:
    """Copy the minimal home's ``auth.json`` back over the operator's, under
    ``<runtime>/reviews/codex-auth.lock``, **only if** it differs from the
    captured copy, still parses as a credential file and the operator's file
    still has the captured digest. Returns what happened: ``unchanged``,
    ``copied``, ``operator-changed``, ``invalid`` or ``missing``. The operator's
    file is never opened for writing otherwise and is never deleted; the
    lock only orders Controller reviews, not an interactive Codex."""
    mine = layout.codex_home / "auth.json"
    theirs = operator_home / "auth.json"
    lock_fd = runtime.open_lock_file(layout.runtime_root, "reviews/codex-auth.lock")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            data = mine.read_bytes()
        except OSError:
            return "missing"
        if hashlib.sha256(data).hexdigest() == captured_digest:
            return "unchanged"
        try:
            parsed = json.loads(data)
        except ValueError:
            return "invalid"
        if not isinstance(parsed, dict) or not parsed:
            return "invalid"
        if _sha256_file(theirs) != captured_digest:
            return "operator-changed"
        try:
            mode = theirs.stat().st_mode & 0o777
            runtime.replace_external_file(theirs, data, mode=mode)
        except OSError:
            return "missing"
        return "copied"
    finally:
        os.close(lock_fd)


# ---------------------------------------------------------------------------
# The bounded runner
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class RunResult:
    exit_code: int | None
    timed_out: bool
    stdout: bytes
    stderr: bytes
    pid: int
    pgid: int


def run_bounded(command: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float,
                stdin_text: str | None = None, on_spawn: Callable[[int, int], None] | None = None) -> RunResult:
    """Run ``command`` in its own session with ``env`` exactly as given, under
    a hard deadline. On the deadline, or on any exception (``KeyboardInterrupt``
    included), the whole process group is killed and the leader reaped before
    the outcome or the exception is returned. The group gets no inherited
    descriptor. ``on_spawn(pid, pgid)`` is called straight after the spawn."""
    proc = subprocess.Popen(list(command), cwd=cwd, env=dict(env), stdin=subprocess.PIPE if stdin_text is not None
                            else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            start_new_session=True, close_fds=True)
    pgid = proc.pid
    try:
        if on_spawn is not None:
            on_spawn(proc.pid, pgid)
        try:
            out, err = proc.communicate(stdin_text.encode() if stdin_text is not None else None, timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            _kill_group(pgid)
            out, err = proc.communicate()
            timed_out = True
        # descendants that outlive the leader belong to the same group
        _kill_group(pgid)
        return RunResult(proc.returncode, timed_out, out, err, proc.pid, pgid)
    except BaseException:
        _kill_group(pgid)
        with contextlib.suppress(Exception):
            proc.communicate(timeout=5)
        proc.wait()
        raise


def _kill_group(pgid: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


# ---------------------------------------------------------------------------
# The composing command (control (b)) and the job's rollouts
# ---------------------------------------------------------------------------


def compose_config(binary: Path, layout: "JobLayout", env: Mapping[str, str], *, model: str, effort: str,
                   timeout: float) -> dict:
    """Run the composing command and return the ``config/read`` result with its
    layers. It is the app-server JSON-RPC method over stdio
    (``compose.txt``): ``initialize``, ``initialized``, ``config/read`` with
    ``includeLayers`` and the review copy as ``cwd``, then stdin closes and the
    server exits. It needs no credential and starts no server, hook or notify
    command; it writes only the state entries :data:`CODEX_STATE_ENTRIES` lists.
    It runs under :func:`run_bounded`, so it has the same deadline and group
    kill, and it carries the exec's own overrides, so the composed values are the
    ones the review runs under."""
    command = argv("app-server", binary=binary, tree=layout.tree, model=model, effort=effort, tmpdir=layout.tmp)
    check_argv("app-server", command, model=model, effort=effort, tmpdir=layout.tmp, tree=layout.tree)
    requests = [
        {"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "workflow-controller", "version": "1"}}},
        {"method": "initialized"},
        {"id": 2, "method": "config/read", "params": {"includeLayers": True, "cwd": str(layout.tree)}},
    ]
    result = run_bounded(command, cwd=layout.tree, env=env, timeout=timeout,
                         stdin_text="".join(json.dumps(item) + "\n" for item in requests))
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


#: ``codex_error_info`` values that mean a usage or rate limit (the binary's
#: snake_case strings and the protocol's camelCase spelling).
LIMIT_ERRORS = frozenset({"usage_limit_exceeded", "rate_limit_exceeded", "usageLimitExceeded", "rateLimitExceeded"})


def _is_limit_error(info: object) -> bool:
    if isinstance(info, str):
        return info in LIMIT_ERRORS
    if not isinstance(info, dict):
        return False
    if any(key in LIMIT_ERRORS for key in info):
        return True
    for key in ("http_connection_failed", "httpConnectionFailed"):
        failed = info.get(key)
        if isinstance(failed, dict) and 429 in (failed.get("http_status_code"), failed.get("httpStatusCode")):
            return True
    return False


@dataclasses.dataclass
class RolloutScan:
    """What the job's rollouts say, across every file: whether Codex reported a
    usage limit, and each sub-agent session with its token usage."""

    limit: bool = False
    limit_detail: str | None = None
    subagents: list[dict] = dataclasses.field(default_factory=list)


def scan_rollouts(codex_home: Path) -> RolloutScan:
    """Read **every** rollout under ``<codex-home>/sessions/`` (a sub-agent
    writes its own). A limit is the ``task_complete`` event's
    ``payload.error.codex_error_info`` being a limit error, a non-null
    ``token_count.rate_limits.rate_limit_reached_type`` or a window at 100
    percent; the transcript text on stderr is never read. A sub-agent is a
    rollout whose ``session_meta.payload.source.subagent.thread_spawn`` is set
    (not the file count: D5's retry legitimately writes a second root rollout)."""
    scan = RolloutScan()
    root = Path(codex_home) / "sessions"
    try:
        files = sorted(p for p in root.glob("*/*/*/rollout-*.jsonl") if p.is_file())
    except OSError:
        return scan
    for path in files:
        subagent = False
        tokens: int | None = None
        try:
            fh = open(path, "rb")
        except OSError:
            continue
        with fh:
            for raw in fh:
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                payload = event.get("payload") if isinstance(event, dict) else None
                if not isinstance(payload, dict):
                    continue
                kind = event.get("type")
                if kind == "session_meta":
                    source = payload.get("source")
                    spawn = source.get("subagent") if isinstance(source, dict) else None
                    subagent = isinstance(spawn, dict) and bool(spawn.get("thread_spawn"))
                elif payload.get("type") == "token_count":
                    info = payload.get("info")
                    usage_block = info.get("total_token_usage") if isinstance(info, dict) else None
                    if isinstance(usage_block, dict) and isinstance(usage_block.get("total_tokens"), int):
                        tokens = usage_block["total_tokens"]
                    limits = payload.get("rate_limits")
                    if isinstance(limits, dict):
                        reached = limits.get("rate_limit_reached_type")
                        full = [w for w in (limits.get("primary"), limits.get("secondary"))
                                if isinstance(w, dict) and isinstance(w.get("used_percent"), (int, float))
                                and w["used_percent"] >= 100]
                        if reached is not None or full:
                            scan.limit, scan.limit_detail = True, f"rate_limit_reached_type={reached!r}"
                elif payload.get("type") == "task_complete":
                    error = payload.get("error")
                    info = error.get("codex_error_info") if isinstance(error, dict) else None
                    if _is_limit_error(info):
                        scan.limit, scan.limit_detail = True, json.dumps(info)
        if subagent:
            scan.subagents.append({"rollout": str(path.relative_to(root)), "total_tokens": tokens})
    return scan


# ---------------------------------------------------------------------------
# The harness
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ReviewHarness:
    """A frozen description of one review harness. ``argv`` builds its command
    line; ``end_readings`` reads the usage readings it left in its own home;
    ``verdict_family`` is the model family a verdict's ``Reviewer model``
    carries (what the Workflow's distinct-model rule compares)."""

    name: str
    verdict_family: str
    argv: Callable[..., list[str]]
    end_readings: Callable[[Path], list[dict]]


def _codex_end_readings(codex_home: Path) -> list[dict]:
    return usage.read_codex_home(codex_home)


CODEX = ReviewHarness("codex", "openai", argv, _codex_end_readings)

#: The closed registry. Empty until D12's pre-step has been recorded: D4 never
#: falls back to detection alone.
HARNESSES: dict[str, ReviewHarness] = {"codex": CODEX} if PRE_STEP_CAPTURED else {}


def harness(name: str) -> ReviewHarness:
    """The registered harness ``name``; an unregistered name refuses."""
    try:
        return HARNESSES[name]
    except KeyError:
        raise _refuse(f"no review harness is registered as {name!r}", check="harness", entry=name,
                      registered=sorted(HARNESSES)) from None


def _preflight(layout: JobLayout, binary: Path, env: Mapping[str, str], model: str, effort: str,
               compose: Callable[[Mapping[str, str]], Mapping], system_dir: Path, timeout: float) -> None:
    """Every refusal of the boundary that can be decided before the review
    starts, each run before each invocation: the layers outside the home, the
    home itself, ``mcp list``, ``features list`` and the composed configuration."""
    check_outside_layers(system_dir)
    for kind in ("mcp", "features"):
        check_home(layout)
        command = argv(kind, binary=binary, model=model, effort=effort)
        check_argv(kind, command, model=model, effort=effort)
        result = run_bounded(command, cwd=layout.tree, env=env, timeout=timeout)
        if result.exit_code != 0 or result.timed_out:
            raise _refuse(f"`{kind} list` did not run", check=kind, value=result.stderr.decode("utf-8", "replace")[:400])
        (check_mcp_list if kind == "mcp" else check_features)(result.stdout.decode("utf-8", "replace"))
    check_home(layout)
    check_effective(compose(env), model=model, effort=effort, tree=layout.tree, tmpdir=layout.tmp,
                    codex_home=layout.codex_home)


def _retain_verdict(runtime_root: Path, job_id: str, attempt: int, text: str, status: str | None,
                    disposition: str) -> dict:
    """Keep a verdict the harness obtained (Workflow condition 2 of workflow#28:
    the courier records every verdict it obtains, REVISE included): its text under
    ``<runtime>/reviews/<job id>-verdict-<attempt>.md`` and its digest, ``Status:``
    and disposition in the returned entry, which the job record carries."""
    rel = f"reviews/{job_id}-verdict-{attempt}.md"
    data = text.encode("utf-8")
    entry = {"attempt": attempt, "sha256": hashlib.sha256(data).hexdigest(), "status": status,
             "disposition": disposition, "path": rel}
    with contextlib.suppress(OSError):
        runtime.write_bytes(runtime_root, rel, data)
    return entry


def launch_review(*, worktree: Path, runtime_root: Path, job_id: str, harness_name: str, binary_name: str,
                  operator_home: Path,
                  stage: str, manifest: Mapping[str, str], bundle_dir: str, model: str, effort: str,
                  timeout: float, feedback_dir: Path | None = None, state_identity: Callable[[], str | None] | None = None,
                  compose: Callable[[Mapping[str, str]], Mapping] | None = None, system_dir: Path = SYSTEM_LAYER_DIR,
                  inherited_env: Mapping[str, str] | None = None, on_spawn: Callable[[int, int], None] | None = None,
                  out: dict | None = None) -> dict:
    """Run one review end to end and return ``{"status", "verdict", "check",
    "outcome", "credentials", "detail", "readings", "verdicts", "subagents"}``:
    the copy is built, the boundary checked before each of the (at most two)
    invocations, the verdict compared exactly with one bounded retry, the real
    repository compared before and after, the credential copy written back, and
    the job's files removed on every path. ``outcome`` is ``verdict``
    (``verdict`` holds the text), ``mismatch`` (second mismatch), ``changed``
    (the repository or the state identity moved: the verdict is void), ``empty``
    (no verdict), ``limit`` (Codex's own rollout says so, never the transcript),
    ``timeout`` or ``failed``. ``verdicts`` lists every verdict obtained, with its
    digest, status and disposition; ``subagents`` the sub-agent sessions the job's
    rollouts show, with their token usage. The harness is called only for a
    registered name. ``compose`` defaults to :func:`compose_config`. ``out``,
    when given, is the dict the result is built in, so a caller whose run is
    interrupted (an exception propagating out) still reads the ``readings`` and
    ``credentials`` the ``finally`` below left in it."""
    layout = JobLayout(runtime_root, job_id)
    selected = harness(harness_name)  # refuses an unregistered name before anything is built
    binary = resolve_binary(binary_name)
    expected = expected_headers(manifest, stage, model)
    identity = state_identity or (lambda: None)
    if compose is None:
        def compose(env: Mapping[str, str]) -> Mapping:
            return compose_config(binary, layout, env, model=model, effort=effort, timeout=min(timeout, 120))
    try:
        own = [str(runtime_root.resolve().relative_to(worktree.resolve()))]
    except ValueError:
        own = []  # the review's own files are not part of the worktree being guarded
    before = snapshot_repository(worktree, feedback_dir=feedback_dir, state_identity=identity(), exclude=own)
    captured = None
    result: dict = out if out is not None else {}
    result.update({"status": None, "verdict": None, "check": None, "outcome": "failed", "credentials": None,
                   "detail": None, "readings": [], "verdicts": [], "subagents": []})
    try:
        build_copy(worktree, runtime_root, job_id)
        captured = build_home(layout, operator_home)
        tag = job_id
        env = child_environment(layout, binary, tag, inherited_env)
        note = None
        for attempt in (1, 2):
            # the boundary is re-checked before every launch, the retry's included
            _preflight(layout, binary, env, model, effort, compose, system_dir, min(timeout, 120))
            prompt = build_prompt(stage, expected, bundle_dir=bundle_dir, note=note)
            command = argv("exec", binary=binary, tree=layout.tree, verdict=layout.verdict, model=model, effort=effort,
                           tmpdir=layout.tmp, prompt="-")
            check_argv("exec", command, model=model, effort=effort, tmpdir=layout.tmp, tree=layout.tree,
                       verdict=layout.verdict)
            check_home(layout)
            check_outside_layers(system_dir)
            run = run_bounded(command, cwd=layout.tree, env=env, timeout=timeout, stdin_text=prompt,
                              on_spawn=on_spawn if attempt == 1 else None)
            scan = scan_rollouts(layout.codex_home)
            result["subagents"] = scan.subagents
            verdict = layout.verdict.read_text("utf-8") if layout.verdict.is_file() else ""
            status = verify_verdict(verdict, expected).status if verdict.strip() else None
            if run.timed_out:
                if verdict.strip():
                    result["verdicts"].append(_retain_verdict(runtime_root, job_id, attempt, verdict, status, "not-ingested: timeout"))
                result.update(outcome="timeout", detail="the review passed its deadline")
                break
            if run.exit_code != 0 or not verdict.strip():
                if verdict.strip():
                    result["verdicts"].append(_retain_verdict(runtime_root, job_id, attempt, verdict, status,
                                                              f"not-ingested: exit {run.exit_code}"))
                text = run.stdout.decode("utf-8", "replace") + run.stderr.decode("utf-8", "replace")
                result.update(outcome="limit" if scan.limit else ("empty" if run.exit_code == 0 else "failed"),
                              detail=(f"Codex reported a limit ({scan.limit_detail})" if scan.limit
                                      else text.strip()[-400:]))
                break
            check = verify_verdict(verdict, expected)
            result.update(check=check, status=check.status)
            if check.ok:
                result["verdicts"].append(_retain_verdict(runtime_root, job_id, attempt, verdict, check.status, "returned"))
                result.update(outcome="verdict", verdict=verdict, detail=None)
                break
            result["verdicts"].append(_retain_verdict(
                runtime_root, job_id, attempt, verdict, check.status,
                "not-ingested: header mismatch, retried once" if attempt == 1 else "not-ingested: header mismatch"))
            result.update(outcome="mismatch", detail=mismatch_note(check))
            note = mismatch_note(check)
            runtime.remove_tree(runtime_root, layout.rel("verdict.md"))
        after = snapshot_repository(worktree, feedback_dir=feedback_dir, state_identity=identity(), exclude=own)
        differing = compare_snapshots(before, after)
        if differing:
            for entry in result["verdicts"]:
                if entry["disposition"] == "returned":
                    entry["disposition"] = "void: the repository or the state identity changed"
            result.update(outcome="changed", verdict=None, detail="changed during the review: " + ", ".join(differing[:20]))
    finally:
        _settle(result, runtime_root, layout, operator_home, captured, selected)
    return result


def _settle(result: dict, runtime_root: Path, layout: JobLayout, operator_home: Path, captured: str | None,
            selected: ReviewHarness) -> None:
    """The three clean-up steps of every path, each guarded: one failing step
    never skips the next (the private ``auth.json`` copy is always removed) and
    never replaces the exception already propagating (a Ctrl-C still exits 130);
    with nothing in flight the first failure is raised."""
    failures: list[Exception] = []
    steps: list[Callable[[], None]] = []
    if captured is not None:
        steps.append(lambda: result.__setitem__("credentials", copy_back_credentials(layout, operator_home, captured)))
    steps.append(lambda: result.__setitem__("readings", selected.end_readings(layout.codex_home)))
    steps.append(lambda: result.__setitem__("subagents", scan_rollouts(layout.codex_home).subagents))
    steps.append(lambda: settle_job_dir(runtime_root, layout.job_id))
    for step in steps:
        try:
            step()
        except Exception as exc:  # noqa: BLE001 -- recorded; raised below only when nothing else is in flight
            failures.append(exc)
    if failures and sys.exc_info()[0] is None:
        raise failures[0]


# ---------------------------------------------------------------------------
# The retained sessions (D4, D7): what a later admission must still see
# ---------------------------------------------------------------------------

#: The usage role a review job is admitted and routed under.
REVIEW_ROLE = "external-review"


def retained_codex_homes(runtime_root: Path) -> list[Path]:
    """Every retained review home, ``<runtime>/reviews/*/codex-home``: the
    rollouts of earlier Controller reviews (one that hit a limit included),
    which a later admission reads beside the operator's own home."""
    base = Path(runtime_root) / "reviews"
    try:
        return sorted(p for p in base.glob("*/codex-home") if (p / "sessions").is_dir())
    except OSError:
        return []


def sweep_retained(runtime_root: Path, *, now: float) -> list[str]:
    """Remove the retained ``sessions/`` of every settled review job whose
    rate-limit windows have all passed their reset, together with the (then
    empty) job directory. A directory that still holds anything but the
    sessions (a running job's copy and home) is never touched. Returns the
    job ids removed."""
    base = Path(runtime_root) / "reviews"
    removed: list[str] = []
    try:
        jobs = sorted(p for p in base.iterdir() if p.is_dir())
    except OSError:
        return removed
    for path in jobs:
        try:
            names = {p.name for p in path.iterdir()}
            home = path / "codex-home"
            inner = {p.name for p in home.iterdir()} if home.is_dir() else set()
        except OSError:
            continue
        if not names <= {"codex-home"} or not inner <= {"sessions"}:
            continue
        if any(reading["resets_at"] > now for reading in usage.read_codex_home(home)):
            continue
        runtime.remove_tree(runtime_root, job_dir(path.name))
        removed.append(path.name)
    return removed
