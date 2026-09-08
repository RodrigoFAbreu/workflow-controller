"""Managed-repository inspection (capability 1,
``docs/ACTIVE_MILESTONE.md``).

``inspect()`` is the Controller's sole entry point onto a target
repository's Workflow installation. It never reimplements Workflow
Manager's own install/update/drift semantics: it takes ``workflow-manager
verify``'s exit code as the sole admission signal (measured fact, not an
assumption: ``status`` exits ``0`` even on an *unmanaged* repository, so
its exit code carries no admission signal at all -- see
``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``, CP2), and reads the installed
``workflow_version``/``profile`` from the Manager's own on-disk manifest,
never from either command's stdout prose.

Five ordered, fail-closed checks, refusing at the first failure:

1. ``git -C <path> rev-parse --show-toplevel`` resolves the canonical
   repository root, else :class:`~controller.errors.NotARepositoryError`.
2. ``.workflow-manager/installation.json`` exists, parses as a JSON object,
   and declares ``schema_version == 1``, ``workflow_version`` and
   ``profile``, else :class:`~controller.errors.UnmanagedRepositoryError`
   (missing manifest) or
   :class:`~controller.errors.MalformedInstallationManifestError`.
3. A ``workflow-manager`` executable resolves, else
   :class:`~controller.errors.WorkflowManagerUnavailableError`.
4. ``workflow-manager verify <root>`` and ``... status <root>`` both exit
   ``0``, else :class:`~controller.errors.DriftedInstallationError`,
   carrying both commands' verbatim output as evidence.
5. ``(workflow_version, profile)`` is in :data:`SUPPORTED_INSTALLATIONS`,
   else :class:`~controller.errors.UnsupportedWorkflowVersionError`
   (unsupported version) or
   :class:`~controller.errors.UnsupportedInstallProfileError` (supported
   version, unsupported profile).

This module never reads a command file or ``WORKFLOW_STATE.json`` --
that ordering guarantee (step 5's refusal precedes any such read) holds
by construction, not by an explicit check.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
from pathlib import Path

from controller.errors import (
    DriftedInstallationError,
    MalformedInstallationManifestError,
    NotARepositoryError,
    UnmanagedRepositoryError,
    UnsupportedInstallProfileError,
    UnsupportedWorkflowVersionError,
    WorkflowManagerUnavailableError,
)

_MANIFEST_REL_PATH = ".workflow-manager/installation.json"

#: The single environment variable a caller may use to point the
#: Controller at a non-``PATH`` ``workflow-manager`` executable, checked
#: after ``--workflow-manager`` and before ``shutil.which``.
MANAGER_ENV = "WORKFLOW_CONTROLLER_WORKFLOW_MANAGER"

#: The closed ``(workflow_version, profile)`` admission set. Both 2.3.1
#: profiles install the identical command/tooling surface the Controller
#: reads (measured, not assumed -- ``EXT-PLAN-R31-O1``'s disposition in
#: the plan): only ``full`` additionally installs the ``conformance``
#: category, and nothing in it is read here. A profile the Manager adds
#: later is refused until it has been measured.
SUPPORTED_INSTALLATIONS: frozenset[tuple[str, str]] = frozenset(
    {("2.3.1", "runtime"), ("2.3.1", "full")}
)


@dataclasses.dataclass(frozen=True)
class ManagedRepository:
    """What :func:`inspect` established about a target repository.

    ``root`` is the canonical repository root Git itself named (step 1),
    never the path the caller passed -- which may be a subdirectory, or
    carry a trailing slash or symlink component. ``manifest`` is the full
    parsed ``installation.json`` object. ``verify``/``status`` are each
    ``{"returncode": int, "stdout": str, "stderr": str}`` -- the Manager's
    verbatim output, unparsed.
    """

    root: Path
    manifest: dict
    workflow_version: str
    profile: str
    verify: dict
    status: dict


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def _resolve_repository_root(path: Path) -> Path:
    result = _run(["git", "-C", str(path), "rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        raise NotARepositoryError(
            f"{path} is not a Git repository (or none of its parents is): "
            f"{result.stderr.strip()}",
            evidence={"path": str(path), "git_stderr": result.stderr.strip()},
        )
    return Path(result.stdout.strip()).resolve()


def _read_manifest(root: Path) -> dict:
    manifest_path = root / _MANIFEST_REL_PATH
    if not manifest_path.is_file():
        raise UnmanagedRepositoryError(
            f"{root} has no {_MANIFEST_REL_PATH} -- not a Workflow-managed repository",
            evidence={"root": str(root), "manifest_path": str(manifest_path)},
        )
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MalformedInstallationManifestError(
            f"{manifest_path} could not be read/parsed as JSON: {exc}",
            evidence={"manifest_path": str(manifest_path), "error": str(exc)},
        ) from exc
    if not isinstance(manifest, dict):
        raise MalformedInstallationManifestError(
            f"{manifest_path} does not contain a JSON object",
            evidence={"manifest_path": str(manifest_path)},
        )
    schema_version = manifest.get("schema_version")
    if schema_version != 1:
        raise MalformedInstallationManifestError(
            f"{manifest_path} declares schema_version={schema_version!r}, expected 1",
            evidence={"manifest_path": str(manifest_path), "schema_version": schema_version},
        )
    workflow_version = manifest.get("workflow_version")
    if not isinstance(workflow_version, str) or not workflow_version:
        raise MalformedInstallationManifestError(
            f"{manifest_path} does not declare a workflow_version",
            evidence={"manifest_path": str(manifest_path)},
        )
    profile = manifest.get("profile")
    if not isinstance(profile, str) or not profile:
        raise MalformedInstallationManifestError(
            f"{manifest_path} does not declare a profile",
            evidence={"manifest_path": str(manifest_path)},
        )
    return manifest


def _resolve_manager_bin(manager_bin: str | None) -> str:
    """``--workflow-manager``, then ``MANAGER_ENV``, then
    ``shutil.which("workflow-manager")`` -- whichever source supplies a
    candidate first is final: a candidate that fails to resolve is a
    refusal, never a silent fall-through to the next source."""
    if manager_bin:
        resolved = shutil.which(manager_bin)
        if resolved is None:
            raise WorkflowManagerUnavailableError(
                f"--workflow-manager {manager_bin!r} does not resolve to an executable",
                evidence={"source": "--workflow-manager", "requested": manager_bin},
            )
        return resolved

    env_value = os.environ.get(MANAGER_ENV)
    if env_value:
        resolved = shutil.which(env_value)
        if resolved is None:
            raise WorkflowManagerUnavailableError(
                f"{MANAGER_ENV}={env_value!r} does not resolve to an executable",
                evidence={"source": MANAGER_ENV, "requested": env_value},
            )
        return resolved

    resolved = shutil.which("workflow-manager")
    if resolved is None:
        raise WorkflowManagerUnavailableError(
            "no workflow-manager executable found: not passed via --workflow-manager, not "
            f"set via {MANAGER_ENV}, and shutil.which('workflow-manager') found nothing -- "
            "absence is never treated as a clean installation",
            evidence={"source": "PATH", "env_var": MANAGER_ENV},
        )
    return resolved


def _run_manager(manager_bin: str, subcommand: str, root: Path) -> dict:
    try:
        result = _run([manager_bin, subcommand, str(root)])
    except OSError as exc:
        raise WorkflowManagerUnavailableError(
            f"{manager_bin} {subcommand} {root} could not be run: {exc}",
            evidence={"manager_bin": manager_bin, "subcommand": subcommand, "error": str(exc)},
        ) from exc
    return {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def inspect(path: str | os.PathLike, *, manager_bin: str | None = None) -> ManagedRepository:
    """Run the five ordered checks against ``path`` and return a
    :class:`ManagedRepository` on success. Refuses at the first failing
    check with a named :class:`~controller.errors.ControllerError`
    subclass; never guesses or defaults to "assume clean"."""
    root = _resolve_repository_root(Path(path))
    manifest = _read_manifest(root)
    resolved_manager = _resolve_manager_bin(manager_bin)

    verify = _run_manager(resolved_manager, "verify", root)
    status = _run_manager(resolved_manager, "status", root)
    if verify["returncode"] != 0 or status["returncode"] != 0:
        raise DriftedInstallationError(
            f"{root}'s Workflow installation failed verification "
            f"(verify exit {verify['returncode']}, status exit {status['returncode']})",
            evidence={"root": str(root), "verify": verify, "status": status},
        )

    workflow_version = manifest["workflow_version"]
    profile = manifest["profile"]
    supported_versions = sorted({version for version, _ in SUPPORTED_INSTALLATIONS})
    if workflow_version not in supported_versions:
        raise UnsupportedWorkflowVersionError(
            f"{root} runs Workflow {workflow_version!r}, which is not in the Controller's "
            f"supported set {supported_versions}",
            evidence={
                "root": str(root),
                "observed_workflow_version": workflow_version,
                "supported_workflow_versions": supported_versions,
            },
        )

    pair = (workflow_version, profile)
    if pair not in SUPPORTED_INSTALLATIONS:
        raise UnsupportedInstallProfileError(
            f"{root} runs Workflow {workflow_version!r} with profile {profile!r}, which is not "
            f"in the Controller's supported set {sorted(SUPPORTED_INSTALLATIONS)}",
            evidence={
                "root": str(root),
                "observed_workflow_version": workflow_version,
                "observed_profile": profile,
                "supported_installations": sorted(SUPPORTED_INSTALLATIONS),
            },
        )

    return ManagedRepository(
        root=root,
        manifest=manifest,
        workflow_version=workflow_version,
        profile=profile,
        verify=verify,
        status=status,
    )
