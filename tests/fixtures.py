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
    workflow_version: str = "2.3.1",
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
    dest: Path, *, workflow_version: str = "2.3.1", profile: str = "full",
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


def write_stub_workflow_manager(
    path: Path, *, verify_exit: int = 0, status_exit: int = 0,
    verify_stdout: str = "workflow 2.3.1 (full profile) -- clean",
    status_stdout: str = "workflow 2.3.1 (full profile) -- clean",
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
