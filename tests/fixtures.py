"""Shared test instrumentation for the Controller's own test suite.

Not part of the package: never imported by ``controller/*.py``, only by
``tests/*.py``. Builds real, disposable Git-repository fixtures the
pinned-execution mechanism can be exercised against with real
subprocesses, rather than mocking the pieces the mechanism's own
correctness depends on (``os.execve``, ``git archive``, an installed
console script).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_PKG = REPO_ROOT / "controller"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def run(args: list[str], *, cwd: Path | None = None, env: dict | None = None,
        check: bool = True, input: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, check=check, input=input,
    )


def build_checkout(dest: Path, *, generation: int | None = 1, committed: bool = True) -> Path:
    """Copy this repository's real ``controller/`` package and
    ``pyproject.toml`` into a fresh directory, and -- unless the caller
    wants a dirty fixture -- ``git init`` and commit it. A minimal, real
    checkout the mechanism can be run against without ever touching this
    repository's own working tree."""
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        CONTROLLER_PKG, dest / "controller",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "SOURCE_PIN.json"),
    )
    shutil.copy2(PYPROJECT, dest / "pyproject.toml")
    # Without this, the *parent* process's own unpinned import of
    # `controller/` (before it ever reaches materialise()'s dirty check)
    # writes `controller/__pycache__/*.pyc` into the fixture checkout, which
    # would make every fixture read as dirty -- exactly the failure mode
    # CP1's own plan text names for this repository's real `.gitignore`.
    (dest / ".gitignore").write_text("__pycache__/\n*.py[cod]\n*.egg-info/\n")
    generation_path = dest / "controller" / "GENERATION.json"
    if generation is not None:
        generation_path.write_text(json.dumps({"schema_version": 1, "generation": generation}) + "\n")
    else:
        generation_path.unlink(missing_ok=True)

    run(["git", "init", "-q"], cwd=dest)
    run(["git", "config", "user.email", "controller-tests@example.invalid"], cwd=dest)
    run(["git", "config", "user.name", "Controller Tests"], cwd=dest)
    if committed:
        run(["git", "add", "-A"], cwd=dest)
        run(["git", "commit", "-q", "-m", "checkout"], cwd=dest)
    return dest


_editable_install_cache: dict[Path, Path] = {}


def editable_install(checkout: Path, venv_dir: Path) -> Path:
    """Build a venv and an editable (``pip install -e``) install of
    ``checkout``, and return the path to the installed
    ``workflow-controller`` console script -- the exact route CP1 test 1
    and test 8 require, since ``python -m controller`` and ``python -P -m
    controller`` each import a different package in the parent than an
    operator's real invocation does."""
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True, capture_output=True)
    pip = venv_dir / "bin" / "pip"
    run([str(pip), "install", "--quiet", "-e", str(checkout)])
    return venv_dir / "bin" / "workflow-controller"


def run_controller_module(args: list[str], *, cwd: Path, env: dict, check: bool = False) -> subprocess.CompletedProcess:
    """Run this repository's real ``controller`` package as
    ``python -m controller`` with an explicit ``PYTHONPATH``, for tests
    that do not need console-script fidelity."""
    full_env = dict(env)
    full_env.setdefault("PYTHONPATH", str(REPO_ROOT))
    return subprocess.run(
        [sys.executable, "-m", "controller", *args],
        cwd=cwd, env=full_env, capture_output=True, text=True, check=check,
    )


# ---------------------------------------------------------------------------
# CP2 -- managed-repository inspection fixtures.
# ---------------------------------------------------------------------------


def build_bare_git_repo(dest: Path) -> Path:
    """A real, otherwise-empty Git repository with no Workflow Manager
    installation at all -- the ``UnmanagedRepositoryError`` fixture."""
    dest.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "-q"], cwd=dest)
    run(["git", "config", "user.email", "controller-tests@example.invalid"], cwd=dest)
    run(["git", "config", "user.name", "Controller Tests"], cwd=dest)
    return dest


def write_installation_manifest(
    root: Path,
    *,
    workflow_version: str = "2.5.1",
    profile: str = "full",
    schema_version: object = 1,
    include_schema_version: bool = True,
    include_workflow_version: bool = True,
    include_profile: bool = True,
) -> Path:
    """Write ``.workflow-manager/installation.json`` under ``root``. Every
    field the malformed-manifest tests need to omit or corrupt is an
    explicit keyword here rather than post-hoc string surgery, so each
    fixture states exactly what it is missing."""
    manifest_dir = root / ".workflow-manager"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {}
    if include_schema_version:
        manifest["schema_version"] = schema_version
    if include_workflow_version:
        manifest["workflow_version"] = workflow_version
    if include_profile:
        manifest["profile"] = profile
    (manifest_dir / "installation.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest_dir / "installation.json"


def build_managed_repo(
    dest: Path, *, workflow_version: str = "2.5.1", profile: str = "full",
) -> Path:
    """A real Git repository carrying a syntactically valid
    ``.workflow-manager/installation.json`` -- the fixture every CP2 test
    that only needs the manifest half (not a real ``workflow-manager``
    verify/status pass) is built on."""
    build_bare_git_repo(dest)
    write_installation_manifest(dest, workflow_version=workflow_version, profile=profile)
    run(["git", "add", "-A"], cwd=dest)
    run(["git", "commit", "-q", "-m", "managed"], cwd=dest)
    return dest


def build_workflow_line_fixture(dest: Path, *, workflow_version: str, profile: str = "full") -> Path:
    """A managed-repo fixture whose ``.claude/commands/`` tree and
    ``scripts/workflow_state.py`` are byte-identical copies of this
    repository's own reference ``2.5.1`` tree -- so the phase set, the
    command-file partition and the user-only set CP3/CP4 read are, by
    construction, identical to the admitted case -- while
    ``installation.json`` alone declares ``workflow_version`` (``REQ-T18B``,
    ``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``'s CP2 section). Refusing
    this fixture at CP2, without ever reaching a command-file or
    ``KNOWN_PHASES`` read, is the assertion that discriminates the
    two-tier admission rule from one that (wrongly) falls back to an
    inventory-equality check when the exact release is unrecognised."""
    build_bare_git_repo(dest)
    copy_real_commands_dir(dest / ".claude" / "commands")
    scripts_dir = dest / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO_ROOT / "scripts" / "workflow_state.py", scripts_dir / "workflow_state.py")
    write_installation_manifest(dest, workflow_version=workflow_version, profile=profile)
    run(["git", "add", "-A"], cwd=dest)
    run(["git", "commit", "-q", "-m", "line-fixture"], cwd=dest)
    return dest


def write_stub_workflow_manager(
    path: Path, *, verify_exit: int = 0, status_exit: int = 0,
    verify_stdout: str = "workflow 2.5.1 (full profile) -- clean",
    status_stdout: str = "workflow 2.5.1 (full profile) -- clean",
) -> Path:
    """A hermetic, offline stand-in for the real ``workflow-manager``
    executable: an executable shell script whose ``verify``/``status``
    exit codes and stdout are the caller's own, so the drift/asymmetry
    tests never depend on the real Manager's actual behaviour."""
    script = (
        "#!/bin/sh\n"
        "sub=\"$1\"\n"
        "shift\n"
        "case \"$sub\" in\n"
        "  verify)\n"
        f"    echo {verify_stdout!r}\n"
        f"    exit {verify_exit}\n"
        "    ;;\n"
        "  status)\n"
        f"    echo {status_stdout!r}\n"
        f"    exit {status_exit}\n"
        "    ;;\n"
        "  *)\n"
        "    echo \"stub workflow-manager: unknown subcommand $sub\" >&2\n"
        "    exit 1\n"
        "    ;;\n"
        "esac\n"
    )
    path.write_text(script)
    path.chmod(0o755)
    return path


# ---------------------------------------------------------------------------
# CP3 -- target-state reader fixtures.
# ---------------------------------------------------------------------------


def write_workflow_state(root: Path, state: dict) -> Path:
    """Write ``docs/ai-workflow/WORKFLOW_STATE.json`` under ``root`` with
    exactly ``state`` as its content (a plain dict the caller builds so
    each malformed-state test states precisely what it corrupts)."""
    state_dir = root / "docs" / "ai-workflow"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "WORKFLOW_STATE.json").write_text(json.dumps(state, indent=2) + "\n")
    return state_dir / "WORKFLOW_STATE.json"


def write_workflow_state_raw(root: Path, raw_text: str) -> Path:
    """Like :func:`write_workflow_state`, but writes ``raw_text`` verbatim
    -- for the invalid-JSON fixture, which has no dict form."""
    state_dir = root / "docs" / "ai-workflow"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "WORKFLOW_STATE.json").write_text(raw_text)
    return state_dir / "WORKFLOW_STATE.json"


def write_workflow_config(root: Path, config: dict) -> Path:
    """Write ``docs/ai-workflow/WORKFLOW_CONFIG.json`` under ``root``."""
    state_dir = root / "docs" / "ai-workflow"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "WORKFLOW_CONFIG.json").write_text(json.dumps(config, indent=2) + "\n")
    return state_dir / "WORKFLOW_CONFIG.json"


def write_target_registry(root: Path, rel_path: str, registry: dict) -> Path:
    """Write a registry JSON file at ``<root>/<rel_path>`` -- the
    ``registry_path`` a work item entry can declare."""
    full_path = root / rel_path
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(json.dumps(registry, indent=2) + "\n")
    return full_path


# ---------------------------------------------------------------------------
# CP4 -- decision-engine fixtures.
# ---------------------------------------------------------------------------

REAL_COMMANDS_DIR = REPO_ROOT / ".claude" / "commands"


def build_work_item_view(*, work_item_id: str = "wi-1", phase: str = "PLANNING",
                          governing_workflow_version: str | None = "2.1", **overrides):
    """A real ``controller.target_state.WorkItemView`` with every field
    populated by a sensible default, so a decision-engine test states only
    the fields it cares about."""
    from controller.target_state import WorkItemView

    defaults: dict = dict(
        work_item_id=work_item_id,
        work_item_type="product",
        work_item_kind="product",
        governing_workflow_version=governing_workflow_version,
        phase=phase,
        plan_revision=1,
        implementation_revision=None,
        functional_review_round=None,
        base_commit="0" * 40,
        reviewed_implementation_head=None,
        current_checkpoint_id=None,
        last_completed_checkpoint_id=None,
        checkpoints={},
        current_bundle_id=None,
        plan_approval=None,
        technical_approval=None,
        functional_acceptance_status=None,
        plan_review_stages=None,
        parent_work_item_id=None,
        incomplete_children=(),
        registry_complete=None,
        state_revision=1,
    )
    defaults.update(overrides)
    return WorkItemView(**defaults)


def copy_real_commands_dir(dest: Path) -> Path:
    """A real, on-disk copy of this repository's own
    ``.claude/commands/`` -- this repository is itself a frozen Workflow
    installation (2.5.1 since revision 64's baseline update), so its
    seventeen command files are the same external artifact a target
    managed repository carries. Copying rather than pointing at
    ``REAL_COMMANDS_DIR`` directly keeps a fixture that mutates a file
    (the extra-file / discriminating-recogniser tests) from ever touching
    this repository's own working tree."""
    dest.mkdir(parents=True, exist_ok=True)
    for path in REAL_COMMANDS_DIR.glob("*.md"):
        shutil.copy2(path, dest / path.name)
    return dest


def write_command_file(commands_dir: Path, name: str, text: str) -> Path:
    """Write a single synthetic command file -- for the partition and
    denylist-recogniser fixtures that need a file the real seventeen do
    not carry."""
    commands_dir.mkdir(parents=True, exist_ok=True)
    path = commands_dir / f"{name}.md"
    path.write_text(text)
    return path


# ---------------------------------------------------------------------------
# CP4B -- evidence-reading fixtures.
# ---------------------------------------------------------------------------


def build_target_git_repo(root: Path) -> Path:
    """A real, minimal Git repository at ``root`` -- ``controller.evidence``
    shells out to ``git rev-parse HEAD``/``git log`` against the *target*
    repository itself, so its own tests need a real repository with real
    commits, distinct from ``build_managed_repo`` (CP2), which only needs
    a syntactically valid ``.workflow-manager/installation.json`` and
    never runs a real ``git log``."""
    root.mkdir(parents=True, exist_ok=True)
    run(["git", "init", "-q"], cwd=root)
    run(["git", "config", "user.email", "controller-tests@example.invalid"], cwd=root)
    run(["git", "config", "user.name", "Controller Tests"], cwd=root)
    return root


def commit_all(root: Path, message: str, *, allow_empty: bool = False) -> str:
    """Stage everything under ``root`` and commit it (or, with
    ``allow_empty``, commit with nothing staged -- the round-scoped
    functional-checklist evidence fixture's own shape), returning the new
    commit's full SHA."""
    run(["git", "add", "-A"], cwd=root)
    args = ["git", "commit", "-q", "-m", message]
    if allow_empty:
        args = ["git", "commit", "-q", "--allow-empty", "-m", message]
    run(args, cwd=root)
    return run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()


def current_head(root: Path) -> str:
    return run(["git", "rev-parse", "HEAD"], cwd=root).stdout.strip()


def write_review_feedback(root: Path, feedback_dir_rel: Path | str, text: str) -> Path:
    """Write ``<feedback_dir>/REVIEW_FEEDBACK.md`` under ``root``."""
    path = root / feedback_dir_rel / "REVIEW_FEEDBACK.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def build_review_feedback_text(
    *, status: str | None = "APPROVE", reviewer_role: str | None = None,
    reviewed_bundle_id: str | None = "b" * 64, reviewed_base_commit: str | None = "0" * 40,
    work_item: str | None = "wi-1", reviewed_content_id: str | None = "c" * 64,
    extra_lines: tuple[str, ...] = (),
) -> str:
    """Assemble a ``REVIEW_FEEDBACK.md`` provenance block from named
    fields -- any field left ``None`` is simply omitted, so a test states
    only what it wants missing. ``extra_lines`` are appended to the
    provenance block verbatim (e.g. a bare-identifier citation, or a
    duplicate line), still ahead of the first ``## `` heading."""
    lines = ["# Review Decision", ""]
    if status is not None:
        lines.append(f"Status: {status}")
    if reviewer_role is not None:
        lines.append(f"Reviewer role: {reviewer_role}")
    if reviewed_bundle_id is not None:
        lines.append(f"Reviewed bundle ID: {reviewed_bundle_id}")
    if reviewed_base_commit is not None:
        lines.append(f"Reviewed base commit: {reviewed_base_commit}")
    if work_item is not None:
        lines.append(f"Work item: {work_item}")
    if reviewed_content_id is not None:
        lines.append(f"Reviewed review content ID: {reviewed_content_id}")
    lines.extend(extra_lines)
    lines.append("")
    lines.append("## Blocking findings")
    lines.append("")
    lines.append("Status: this is prose quoting the template, never a live field.")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_manifest(root: Path, bundle_dir_rel: Path | str, text: str) -> Path:
    """Write ``<bundle_dir>/MANIFEST.md`` under ``root``."""
    path = root / bundle_dir_rel / "MANIFEST.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def build_manifest_text(
    *, bundle_id: str | None = "b" * 64, generation_head: str | None = "0" * 40,
    stage: str | None = None, work_item_id: str | None = None, plan_revision: int | str | None = None,
) -> str:
    """A ``MANIFEST.md`` in the generator's own line order. ``stage``/
    ``work_item_id``/``plan_revision`` default to ``None`` (line omitted),
    so every caller that does not pass them gets byte-identical output."""
    lines = ["# Bundle manifest", ""]
    if stage is not None:
        lines.append(f"stage: {stage}")
    if bundle_id is not None:
        lines.append(f"bundle_id: {bundle_id}")
    if work_item_id is not None:
        lines.append(f"work_item_id: {work_item_id}")
    if plan_revision is not None:
        lines.append(f"plan_revision: {plan_revision}")
    if generation_head is not None:
        lines.append(f"generation_head: {generation_head}")
    lines.append("")
    lines.append("## Protected paths")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_rejected_marker(root: Path, work_item_id: str, *, scoped: bool, detail: str = "withdrawn") -> Path:
    """Write the ``REJECTED`` marker at its scoped or flat path."""
    base = (root / ".ai-review" / work_item_id) if scoped else (root / ".ai-review")
    base.mkdir(parents=True, exist_ok=True)
    path = base / "REJECTED"
    path.write_text(detail + "\n")
    return path


def build_target_managed_repository(root: Path):
    """A minimal, real ``managed_repo.ManagedRepository`` pointed at
    ``root`` -- ``target_state.read`` only ever reads ``.root`` off it, so
    the other fields are inert placeholders rather than a real Workflow
    Manager inspection."""
    from controller.managed_repo import ManagedRepository

    root.mkdir(parents=True, exist_ok=True)
    return ManagedRepository(
        root=root, manifest={}, workflow_version="2.3.1", profile="full",
        verify={"returncode": 0, "stdout": "", "stderr": ""},
        status={"returncode": 0, "stdout": "", "stderr": ""},
    )
