"""Shared test instrumentation for the Controller's own test suite.

Not part of the package: never imported by ``controller/*.py``, only by
``tests/*.py``. Builds real, disposable Git-repository fixtures the
pinned-execution mechanism can be exercised against with real
subprocesses, rather than mocking the pieces the mechanism's own
correctness depends on (``os.execve``, ``git archive``, an installed
console script).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import unittest.mock
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_PKG = REPO_ROOT / "controller"
PYPROJECT = REPO_ROOT / "pyproject.toml"
SETUP_PY = REPO_ROOT / "setup.py"

#: Set to ``"1"`` to make a missing wheel-build prerequisite a test failure
#: instead of a skip.
REQUIRE_PACKAGING_TESTS_ENV = "CONTROLLER_REQUIRE_PACKAGING_TESTS"


def run(args: list[str], *, cwd: Path | None = None, env: dict | None = None,
        check: bool = True, input: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, cwd=cwd, env=env, capture_output=True, text=True, check=check, input=input,
    )


def build_checkout(dest: Path, *, generation: int | None = 1, committed: bool = True) -> Path:
    """Copy this repository's real ``controller/`` package,
    ``pyproject.toml`` and ``setup.py`` (the build hook) into a fresh
    directory, and -- unless the caller wants a dirty fixture -- ``git
    init`` and commit it. A minimal, real
    checkout the mechanism can be run against without ever touching this
    repository's own working tree."""
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        CONTROLLER_PKG, dest / "controller",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "SOURCE_PIN.json", "BUILD_INFO.json"),
    )
    shutil.copy2(PYPROJECT, dest / "pyproject.toml")
    shutil.copy2(SETUP_PY, dest / "setup.py")
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


def wheel_build_prerequisite() -> str | None:
    """``None`` when ``sys.executable -m pip wheel --no-deps
    --no-build-isolation`` can build this project, otherwise the missing
    piece. A ``--no-build-isolation`` build needs setuptools >= 70.1, or an
    older setuptools plus the ``wheel`` package; setuptools merely being
    importable is not enough."""
    probe = subprocess.run(
        [sys.executable, "-c",
         "import importlib.util, setuptools; "
         "print(setuptools.__version__); "
         "print(importlib.util.find_spec('wheel') is not None)"],
        capture_output=True, text=True, check=False,
    )
    if probe.returncode != 0:
        return f"setuptools is not importable by {sys.executable}"
    version_text, has_wheel = probe.stdout.split()
    try:
        version = tuple(int(part) for part in version_text.split(".")[:2])
    except ValueError:
        return f"unparseable setuptools version {version_text!r}"
    if version < (70, 1) and has_wheel != "True":
        return f"setuptools {version_text} < 70.1 and the wheel package is not installed"
    pip = subprocess.run([sys.executable, "-m", "pip", "--version"],
                         capture_output=True, text=True, check=False)
    if pip.returncode != 0:
        return f"pip is not available to {sys.executable}"
    return None


def require_wheel_build(test: unittest.TestCase) -> None:
    """Skip ``test`` naming the missing prerequisite, or fail it when
    ``CONTROLLER_REQUIRE_PACKAGING_TESTS=1``."""
    missing = wheel_build_prerequisite()
    if missing is None:
        return
    if os.environ.get(REQUIRE_PACKAGING_TESTS_ENV) == "1":
        test.fail(f"wheel-build prerequisite missing: {missing}")
    raise unittest.SkipTest(f"wheel-build prerequisite missing: {missing}")


def build_wheel(source: Path, out_dir: Path, *, env: dict | None = None,
                check: bool = True) -> subprocess.CompletedProcess:
    """``pip wheel --no-deps --no-build-isolation`` of ``source`` into
    ``out_dir`` -- in-tree, through ``source/build``."""
    full_env = dict(os.environ if env is None else env)
    full_env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    full_env.setdefault("PIP_NO_INPUT", "1")
    return run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
         "--wheel-dir", str(out_dir), str(source)],
        env=full_env, check=check,
    )


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
        implementation_review_stages=None,
        technical_review_block_pins=(),
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
    review_content_id: str | None = None, reviewed_implementation_head: str | None = None,
    implementation_revision: int | str | None = None, worktree_root: str | None = None,
) -> str:
    """A ``MANIFEST.md`` in the generator's own line order. ``stage``/
    ``work_item_id``/``plan_revision`` and the implementation-stage
    ``review_content_id``/``reviewed_implementation_head``/
    ``implementation_revision``/``worktree_root`` default to ``None`` (line
    omitted), so every caller that does not pass them gets byte-identical
    output."""
    lines = ["# Bundle manifest", ""]
    if stage is not None:
        lines.append(f"stage: {stage}")
    if bundle_id is not None:
        lines.append(f"bundle_id: {bundle_id}")
    if review_content_id is not None:
        lines.append(f"review_content_id: {review_content_id}")
    if work_item_id is not None:
        lines.append(f"work_item_id: {work_item_id}")
    if plan_revision is not None:
        lines.append(f"plan_revision: {plan_revision}")
    if reviewed_implementation_head is not None:
        lines.append(f"reviewed_implementation_head: {reviewed_implementation_head}")
    if implementation_revision is not None:
        lines.append(f"implementation_revision: {implementation_revision}")
    if worktree_root is not None:
        lines.append(f"worktree_root: {worktree_root}")
    if generation_head is not None:
        lines.append(f"generation_head: {generation_head}")
    lines.append("")
    lines.append("## Protected paths")
    lines.append("")
    return "\n".join(lines) + "\n"


def build_plan_manifest_text(
    work_item_id: str, plan_revision: int | str, *, bundle_id: str = "b" * 64,
    generation_head: str | None = "0" * 40,
) -> str:
    """A plan-stage ``MANIFEST.md`` coherent with ``work_item_id`` at
    ``plan_revision`` (``evidence.plan_bundle_coherence``)."""
    return build_manifest_text(
        bundle_id=bundle_id, generation_head=generation_head, stage="plan",
        work_item_id=work_item_id, plan_revision=plan_revision,
    )


def write_plan_manifest(root: Path, work_item_id: str, plan_revision: int | str, **kwargs) -> Path:
    """Write a coherent plan-stage ``MANIFEST.md`` at the work item's own
    plan-stage bundle dir (``.ai-review/<id>/current/``)."""
    return write_manifest(
        root, f".ai-review/{work_item_id}/current",
        build_plan_manifest_text(work_item_id, plan_revision, **kwargs),
    )


def target_worktree_root(root: Path) -> str:
    """``git rev-parse --show-toplevel`` for a fixture repository -- the
    value the real generator records as a manifest's ``worktree_root``."""
    return run(["git", "rev-parse", "--show-toplevel"], cwd=root).stdout.strip()


def build_implementation_manifest_text(
    work_item_id: str, implementation_revision: int | str, *, worktree_root: str | None,
    reviewed_implementation_head: str | None = "1" * 40, generation_head: str | None = "0" * 40,
    bundle_id: str | None = "b" * 64, review_content_id: str | None = "c" * 64,
    stage: str | None = "implementation",
) -> str:
    """An implementation-stage ``MANIFEST.md`` (the counterpart of
    :func:`build_plan_manifest_text`), coherent with a work item at
    ``implementation_revision`` whose ``reviewed_implementation_head`` is the
    one given, generated in ``worktree_root`` at ``generation_head``
    (``evidence.implementation_bundle_coherence``). Any field passed as
    ``None`` is omitted."""
    return build_manifest_text(
        bundle_id=bundle_id, generation_head=generation_head, stage=stage,
        work_item_id=work_item_id, review_content_id=review_content_id,
        reviewed_implementation_head=reviewed_implementation_head,
        implementation_revision=implementation_revision, worktree_root=worktree_root,
    )


def write_implementation_manifest(
    root: Path, work_item_id: str, implementation_revision: int | str, *, scoped: bool = True,
    **kwargs,
) -> Path:
    """Write an implementation-stage ``MANIFEST.md`` at the scoped
    (``.ai-review/<id>/current/``) or flat (``.ai-review/current/``) bundle
    dir. ``worktree_root`` defaults to the fixture repository's own
    ``git rev-parse --show-toplevel`` and ``generation_head`` to its
    current ``HEAD``, so the default output is coherent."""
    kwargs.setdefault("worktree_root", target_worktree_root(root))
    kwargs.setdefault("generation_head", current_head(root))
    bundle_dir = f".ai-review/{work_item_id}/current" if scoped else ".ai-review/current"
    return write_manifest(
        root, bundle_dir,
        build_implementation_manifest_text(work_item_id, implementation_revision, **kwargs),
    )


def write_implementation_bundle(
    root: Path, work_item_id: str, implementation_revision: int | str, *, scoped: bool = True,
    review_content_id: str = "c" * 64, author_files: bool = True, **manifest_kwargs,
) -> Path:
    """A coherent implementation-stage bundle (`workflow-controller-
    automatic-lifecycle-orchestration` CP4): :func:`write_implementation_manifest`
    plus, unless ``author_files`` is false, the four author files
    ``/milestone-implement`` step 4 writes, each stating what the generator
    requires of it -- ``IMPLEMENTATION_SUMMARY.md``'s
    ``implementation_revision:`` and ``REVIEW_REQUEST.md``'s
    ``review_content_id:`` (the manifest's). Returns the bundle directory."""
    manifest_path = write_implementation_manifest(
        root, work_item_id, implementation_revision, scoped=scoped, review_content_id=review_content_id,
        **manifest_kwargs,
    )
    bundle = manifest_path.parent
    if author_files:
        (bundle / "IMPLEMENTATION_SUMMARY.md").write_text(
            f"# Implementation summary\n\nimplementation_revision: {implementation_revision}\n",
        )
        (bundle / "REVIEW_REQUEST.md").write_text(
            f"# Review request\n\nstage: implementation\nreview_content_id: {review_content_id}\n",
        )
        (bundle / "TEST_RESULTS.md").write_text("# Test results\n\nall green\n")
        (bundle / "CONTEXT_FILES.txt").write_text("README.md\n")
    return bundle


def fake_worker_plan_manifest_env(root: Path, work_item_id: str, plan_revision: int | str) -> dict[str, str]:
    """``FAKE_CLAUDE_WRITES`` env override making the fake worker itself
    write a coherent plan-stage ``MANIFEST.md`` -- the artifact every
    plan-bundle-producing ``ExpectedOutcome`` row's postcondition requires
    (`workflow-controller-worker-execution-hardening` CP3)."""
    path = root / ".ai-review" / work_item_id / "current" / "MANIFEST.md"
    return {"FAKE_CLAUDE_WRITES": json.dumps([
        {"path": str(path), "text": build_plan_manifest_text(work_item_id, plan_revision)},
    ])}


def registry_rel_path(work_item_id: str) -> str:
    """The conventional ``registry_path`` a fixture work item declares."""
    return f"docs/ai-workflow/registry/{work_item_id}-registry.json"


def write_registry(root: Path, work_item_id: str, checkpoint_ids: tuple[str, ...] | list[str]) -> str:
    """Write a minimal, well-formed checkpoint registry declaring
    ``checkpoint_ids`` for ``work_item_id`` at :func:`registry_rel_path`,
    and return that relative path (a work item entry's ``registry_path``)."""
    rel_path = registry_rel_path(work_item_id)
    write_target_registry(root, rel_path, {
        "schema_version": 1,
        "work_item_id": work_item_id,
        "plan_revision": 1,
        "checkpoints": [
            {"id": checkpoint_id, "name": checkpoint_id, "depends_on": [], "complexity": 1,
             "session_target": 1}
            for checkpoint_id in checkpoint_ids
        ],
    })
    return rel_path


def update_workflow_state(root: Path, work_item_id: str, **fields) -> dict:
    """Read ``WORKFLOW_STATE.json`` under ``root``, update ``work_item_id``'s
    entry with ``fields`` and write it back -- a fake worker's state write
    (the Workflow writers' own effects, simulated), returning the new
    state."""
    state_path = root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json"
    state = json.loads(state_path.read_text())
    state["work_items"][work_item_id].update(fields)
    write_workflow_state(root, state)
    return state


def implementation_review_ledger(
    review_content_id: str = "c" * 64, *, local_bundle_id: str | None = None,
    manual_bundle_id: str | None = None,
) -> dict:
    """An ``implementation_review_stages`` ledger in the shape
    ``record_local_implementation_review``/
    ``record_manual_implementation_review`` write: the content id plus one
    completed ``APPROVE`` stage entry per bundle id given."""
    ledger: dict = {"review_content_id": review_content_id}
    for role, bundle_id in (
        ("LOCAL_MODEL_IMPLEMENTATION_REVIEW", local_bundle_id),
        ("MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW", manual_bundle_id),
    ):
        if bundle_id is not None:
            ledger[role] = {
                "verdict": "APPROVE", "bundle_id": bundle_id, "round": 1,
                "completed_at": "2026-01-01T00:00:00Z",
            }
    return ledger


def commit_paths(root: Path, message: str, *paths: str) -> str:
    """Stage exactly ``paths`` (relative to ``root``) and commit them,
    returning the new commit's full SHA -- a commit that deliberately leaves
    every other working-tree change (e.g. a pending ``WORKFLOW_STATE.json``
    write) uncommitted."""
    run(["git", "add", "--", *paths], cwd=root)
    run(["git", "commit", "-q", "-m", message], cwd=root)
    return current_head(root)


def build_implementation_target(
    tmp_root: Path, *, phase: str, governing_workflow_version: str | None = "2.2",
    work_item_id: str = "wi-1", checkpoint_ids: tuple[str, ...] = ("CP1", "CP2"),
    checkpoints: dict | None = None, **overrides,
):
    """A real target repository for the implementation-stage
    ``ExpectedOutcome`` rows (`workflow-controller-automatic-lifecycle-
    orchestration` CP2): a registry declaring ``checkpoint_ids``, a work
    item entry at ``phase`` whose ``checkpoints`` default to every one
    ``COMPLETE``, a current plan approval, and ``.ai-review/`` ignored the
    way a real installation ignores it -- all **committed**, so ``HEAD``
    records the seeded state and a later worker commit is observable.
    ``overrides`` update the entry before the commit."""
    root = tmp_root / "target"
    build_target_git_repo(root)
    (root / "README.md").write_text("target fixture\n")
    (root / ".gitignore").write_text(".ai-review/\n")
    base_commit = commit_all(root, "initial")
    entry = {
        "work_item_type": "product",
        "work_item_kind": "product",
        "work_item_id": work_item_id,
        "governing_workflow_version": governing_workflow_version,
        "phase": phase,
        "plan_revision": 1,
        "implementation_revision": None,
        "reviewed_implementation_head": None,
        "state_revision": 1,
        "checkpoints": (
            checkpoints if checkpoints is not None
            else {cid: {"status": "COMPLETE", "start_commit": base_commit} for cid in checkpoint_ids}
        ),
        "current_bundle_id": None,
        "last_completed_checkpoint_id": None,
        "base_commit": base_commit,
        "parent_work_item_id": None,
        "registry_path": write_registry(root, work_item_id, checkpoint_ids),
        "plan_approval": {"status": "CURRENT"},
    }
    entry.update(overrides)
    write_workflow_state(root, {
        "schema_version": 1, "active_work_item_id": work_item_id, "work_items": {work_item_id: entry},
    })
    commit_all(root, "seed workflow state")
    return build_target_managed_repository(root)


def write_rejected_marker(root: Path, work_item_id: str, *, scoped: bool, detail: str = "withdrawn") -> Path:
    """Write the ``REJECTED`` marker at its scoped or flat path."""
    base = (root / ".ai-review" / work_item_id) if scoped else (root / ".ai-review")
    base.mkdir(parents=True, exist_ok=True)
    path = base / "REJECTED"
    path.write_text(detail + "\n")
    return path


# ---------------------------------------------------------------------------
# `workflow-controller-automatic-lifecycle-orchestration` CP2 -- scripted
# worker effects for the implementation-stage `ExpectedOutcome` rows: each
# performs a Workflow writer's own state write, commit and bundle
# generation (or deliberately omits one), after pre-state capture and
# before verification.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def forced_automatic_action(phase: str, command: str, work_item_id: str = "wi-1"):
    """``evidence.decide`` returning the automatic ``command`` at ``phase``
    -- the selection CP3's general dispatch rule makes; CP2 owns only what
    happens once it is launched."""
    from controller import job
    from controller.decision import Action, Decision

    decision = Decision(
        observed_phase=phase, evidence=(), action=Action(command=f"{command} {work_item_id}"),
        automatic=True, gate=None, declined=False, reason="CP2 test fixture: selection is CP3's",
    )
    with unittest.mock.patch.object(job.evidence, "decide", return_value=decision):
        yield


@contextlib.contextmanager
def scripted_worker(side_effect):
    """Run ``side_effect(target_root)`` as the worker's own work, then the
    real ``worker.launch`` of ``fake_claude.py`` (a ``SUCCESS`` result) --
    so everything after pre-state capture sees exactly what a worker left
    behind."""
    from controller import job

    real_launch = job.worker.launch

    def launch(task, *, cwd, **kwargs):
        if side_effect is not None:
            side_effect(Path(cwd))
        return real_launch(task, cwd=cwd, **kwargs)

    with unittest.mock.patch.object(job.worker, "launch", launch):
        yield


def state_entry(root: Path, work_item_id: str = "wi-1") -> dict:
    """The work item's ``WORKFLOW_STATE.json`` entry, read from the working tree."""
    state = json.loads((root / "docs" / "ai-workflow" / "WORKFLOW_STATE.json").read_text())
    return state["work_items"][work_item_id]


def complete_checkpoint_effect(checkpoint_id: str, *, phase: str = "IMPLEMENTING", commit: str = "all"):
    """``/milestone-implement`` step 1f: the checkpoint's own change plus
    ``complete_checkpoint``'s state write (``phase`` becomes
    ``SELF_REVIEWING_IMPLEMENTATION`` on the last one), committed together
    (``commit="all"``), with only the product change committed
    (``"product"``, the state write left pending), or not at all
    (``"none"``)."""
    def effect(root: Path) -> None:
        checkpoints = dict(state_entry(root)["checkpoints"])
        checkpoints[checkpoint_id] = {**checkpoints.get(checkpoint_id, {}), "status": "COMPLETE"}
        (root / f"{checkpoint_id}.txt").write_text(f"{checkpoint_id} implemented\n")
        update_workflow_state(
            root, "wi-1", checkpoints=checkpoints, phase=phase, last_completed_checkpoint_id=checkpoint_id,
        )
        if commit == "all":
            commit_all(root, f"Implement {checkpoint_id}")
        elif commit == "product":
            commit_paths(root, f"Implement {checkpoint_id}", f"{checkpoint_id}.txt")
    return effect


def generation_effect(
    review_phase: str, *, revision: int = 1, enter_self_review: bool = False, fix_commit: bool = False,
    same_content: bool = False, manifest: bool = True, manifest_revision=None,
    manifest_reviewed_head=None, manifest_generation_head=None, rejected: bool = False,
    bundle_id: str = "f" * 64,
):
    """A bundle generation (`/milestone-implement` step 4, or
    `/apply-implementation-review` step 7): optionally step 2's committed
    ``SELF_REVIEWING_IMPLEMENTATION`` transition and/or a fix commit
    first, then ``record_bundle_generation``'s state write committed alone
    (the generation-record commit T), then the generator's manifest at
    ``generation_head = T``. Each ``manifest_*`` override injects one
    stale/wrong clause; ``manifest=False`` skips generation; ``rejected``
    leaves a ``REJECTED`` marker beside it."""
    def effect(root: Path) -> None:
        if enter_self_review:
            update_workflow_state(root, "wi-1", phase="SELF_REVIEWING_IMPLEMENTATION")
            commit_all(root, "Enter SELF_REVIEWING_IMPLEMENTATION")
        if fix_commit:
            (root / "fix.txt").write_text("review fix\n")
            commit_paths(root, "Apply a review fix", "fix.txt")
        entry = state_entry(root)
        if same_content:
            reviewed_head = entry["reviewed_implementation_head"]
            update_workflow_state(root, "wi-1", phase=review_phase)
        else:
            reviewed_head = current_head(root)
            update_workflow_state(
                root, "wi-1", phase=review_phase, implementation_revision=revision,
                reviewed_implementation_head=reviewed_head,
            )
        record = commit_all(root, "Record implementation bundle generation")
        if manifest:
            write_implementation_manifest(
                root, "wi-1",
                manifest_revision if manifest_revision is not None else state_entry(root)["implementation_revision"],
                reviewed_implementation_head=manifest_reviewed_head or reviewed_head,
                generation_head=manifest_generation_head(reviewed_head, record) if manifest_generation_head else record,
                bundle_id=bundle_id,
            )
        if rejected:
            write_rejected_marker(root, "wi-1", scoped=True, detail="finalize failed")
    return effect


def review_writes_effect(*, feedback: str | None = None, **state_fields):
    """A review-stage writer's effect: ``REVIEW_FEEDBACK.md`` (when given)
    plus a state write -- left **uncommitted**, as every `"2.2"`
    review-stage writer leaves it."""
    def effect(root: Path) -> None:
        if feedback is not None:
            write_review_feedback(root, ".ai-review/wi-1/feedback", feedback)
        if state_fields:
            update_workflow_state(root, "wi-1", **state_fields)
    return effect


# ---------------------------------------------------------------------------
# `workflow-controller-automatic-lifecycle-orchestration` CP7 -- the scripted
# fake worker (`tests/fake_claude.py`'s `FAKE_CLAUDE_SCRIPT`): a real worker
# subprocess whose effect is a list of writes, commits and deletes, keyed on
# the exact task it is launched with.
# ---------------------------------------------------------------------------


def script_write(path: str, text: str) -> dict:
    """A scripted ``write`` of ``text`` to the target-relative ``path``
    (``{HEAD}``/``{HEAD^}``/``{HEAD~N}`` resolved at write time)."""
    return {"action": "write", "path": path, "text": text}


def script_commit(message: str, *paths: str) -> dict:
    """A scripted commit of exactly ``paths`` with ``message``."""
    return {"action": "commit", "paths": list(paths), "message": message}


def script_delete(path: str) -> dict:
    """A scripted removal of a target-relative file or directory tree."""
    return {"action": "delete", "path": path}


def trailer_message(subject: str, *trailers: tuple[str, str]) -> str:
    """A commit message whose final paragraph is exactly ``trailers``, in
    order -- the shape ``git interpret-trailers --parse`` reads."""
    return subject + "\n\n" + "\n".join(f"{key}: {value}" for key, value in trailers)


def write_worker_script(path: Path, script: dict[str, list[list[dict]]]) -> Path:
    """Write a ``FAKE_CLAUDE_SCRIPT`` file: exact task -> the action list of
    each successive invocation of it."""
    path.write_text(json.dumps(script, indent=2) + "\n")
    return path


def scripted_worker_tasks(script_path: Path) -> list[str]:
    """The task of every scripted-worker invocation so far, in order (read
    from the counter file beside ``script_path``)."""
    from tests.fake_claude import script_invocations_path

    counter = Path(script_invocations_path(script_path))
    if not counter.exists():
        return []
    return [json.loads(line)["task"] for line in counter.read_text().splitlines() if line.strip()]


def perform_script_actions(root: Path, actions: list[dict]) -> None:
    """Perform scripted actions in-process against ``root`` -- the same
    implementation the scripted worker runs, for seeding a pre-state
    without launching one."""
    from tests.fake_claude import perform_actions

    perform_actions(actions, root)


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
