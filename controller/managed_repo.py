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
5. **The two-tier "Supported Workflow baseline" rule** (revision 68,
   manual external plan review round 67's ``I1``): ``workflow_version``
   must first parse as a dotted release inside :data:`SUPPORTED_WORKFLOW_LINE`
   (a necessary pre-filter and diagnostic classifier only), then must be an
   **exact member** of :data:`VALIDATED_WORKFLOW_RELEASES` (the real
   admission gate -- a release inside the supported line that has never
   been individually measured is refused too, distinguished in its
   evidence from a wrong-line refusal), else
   :class:`~controller.errors.UnsupportedWorkflowVersionError`; and
   ``profile`` must be a member of :data:`SUPPORTED_PROFILES`, else
   :class:`~controller.errors.UnsupportedInstallProfileError`.

This module never reads a command file or ``WORKFLOW_STATE.json`` --
that ordering guarantee (step 5's refusal precedes any such read) holds
by construction, not by an explicit check.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
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

#: A manifest's ``workflow_version`` must parse as a dotted release whose
#: major and minor components are exactly these two -- a necessary
#: pre-filter and diagnostic classifier only, never by itself sufficient
#: for admission (``VALIDATED_WORKFLOW_RELEASES`` below is the actual
#: gate). Lets a refusal say "wrong line" instead of "not yet validated".
SUPPORTED_WORKFLOW_LINE = "2.5"

#: The actual admission gate (revision 68, manual external plan review
#: round 67's ``I1``): ``workflow_version`` must be an **exact member** of
#: this closed set, not merely a member of ``SUPPORTED_WORKFLOW_LINE``.
#: ``2.5.1`` is its only element because it is the only release this
#: Controller's inventories and baseline-verification suites were ever
#: actually measured against. Growing this set is a deliberate act, never
#: automatic from a parsed major/minor match: re-measure the inventories
#: and baseline-verification suites against the newly-installed release,
#: then add its version string here by name, in a plan revision that
#: states what was measured.
VALIDATED_WORKFLOW_RELEASES: frozenset[str] = frozenset({"2.5.1"})

#: Unchanged in membership and justification since revision 32: both
#: profiles install the identical command/tooling surface the Controller
#: reads (measured, not assumed -- ``EXT-PLAN-R31-O1``'s disposition in
#: the plan); only ``full`` additionally installs the ``conformance``
#: category, and nothing in it is read here. A profile the Manager adds
#: later is refused until it has been measured.
SUPPORTED_PROFILES: frozenset[str] = frozenset({"runtime", "full"})

#: The one concrete release every inventory in the plan is derived from
#: and re-derivable against. Since revision 68 it is also the sole member
#: of ``VALIDATED_WORKFLOW_RELEASES`` -- the derivation pin and the
#: admission gate currently name the same release, but remain two
#: different mechanisms; a future validated release would extend the
#: latter without necessarily moving the former.
REFERENCE_WORKFLOW_RELEASE = "2.5.1"

_DOTTED_RELEASE_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


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
    if "workflow_version" not in manifest or not isinstance(manifest["workflow_version"], str):
        raise MalformedInstallationManifestError(
            f"{manifest_path} does not declare a workflow_version",
            evidence={"manifest_path": str(manifest_path)},
        )
    # An empty string is a *declared* value -- a string that fails the
    # baseline predicate's "must parse as a dotted release" test, exactly
    # like `"latest"` -- so it is a version-admission refusal
    # (`UnsupportedWorkflowVersionError`, step 5), never a malformed-
    # manifest one: this field was declared, just not usefully.
    workflow_version = manifest["workflow_version"]
    profile = manifest.get("profile")
    if not isinstance(profile, str) or not profile:
        raise MalformedInstallationManifestError(
            f"{manifest_path} does not declare a profile",
            evidence={"manifest_path": str(manifest_path)},
        )
    return manifest


def installed_workflow_version(root: Path) -> str:
    """The ``workflow_version`` the target's installation manifest declares
    right now: a plain read through :func:`_read_manifest`, never a Workflow
    Manager call and never an admission check
    (``docs/ai-workflow/CONTROLLER_WORKFLOW_2_6_INTEGRATION_PLAN.md``, I3).
    ``controller.job`` compares it with the release a step was admitted
    under, or a job was launched under; it never selects a contract. A
    missing manifest raises :class:`~controller.errors.UnmanagedRepositoryError`
    and an unreadable one :class:`~controller.errors.MalformedInstallationManifestError`,
    exactly as :func:`inspect` would -- including an ``OSError`` from the
    parser's own existence check (Python 3.12's ``Path.is_file`` raises one
    for an unsearchable directory), so these two are the only errors a
    caller has to catch."""
    root = Path(root)
    try:
        return _read_manifest(root)["workflow_version"]
    except OSError as exc:
        manifest_path = root / _MANIFEST_REL_PATH
        raise MalformedInstallationManifestError(
            f"{manifest_path} could not be read: {exc}",
            evidence={"manifest_path": str(manifest_path), "error": str(exc)},
        ) from exc


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


def _workflow_line(workflow_version: str) -> str | None:
    """The ``major.minor`` line of a dotted ``major.minor.patch`` release
    string, or ``None`` if it does not parse as one at all -- never a
    best-effort prefix match."""
    match = _DOTTED_RELEASE_RE.match(workflow_version)
    if match is None:
        return None
    major, minor, _patch = match.groups()
    return f"{major}.{minor}"


def _check_workflow_version(workflow_version: str, root: Path) -> None:
    """The two-tier baseline rule: a coarser, cheaper wrong-line refusal
    first, then the real admission gate -- exact membership of
    ``VALIDATED_WORKFLOW_RELEASES``. The two refusals carry distinct
    evidence ``reason`` values so they are never conflated in a report a
    human reads."""
    line = _workflow_line(workflow_version)
    if line != SUPPORTED_WORKFLOW_LINE:
        raise UnsupportedWorkflowVersionError(
            f"{root} runs Workflow {workflow_version!r}, which is outside the Controller's "
            f"supported line {SUPPORTED_WORKFLOW_LINE!r} (reference release "
            f"{REFERENCE_WORKFLOW_RELEASE!r})",
            evidence={
                "root": str(root),
                "observed_workflow_version": workflow_version,
                "supported_workflow_line": SUPPORTED_WORKFLOW_LINE,
                "reference_workflow_release": REFERENCE_WORKFLOW_RELEASE,
                "reason": "outside_supported_line",
            },
        )
    if workflow_version not in VALIDATED_WORKFLOW_RELEASES:
        raise UnsupportedWorkflowVersionError(
            f"{root} runs Workflow {workflow_version!r}, which is in the Controller's supported "
            f"line {SUPPORTED_WORKFLOW_LINE!r} but has not been individually validated "
            f"(validated releases: {sorted(VALIDATED_WORKFLOW_RELEASES)})",
            evidence={
                "root": str(root),
                "observed_workflow_version": workflow_version,
                "supported_workflow_line": SUPPORTED_WORKFLOW_LINE,
                "validated_workflow_releases": sorted(VALIDATED_WORKFLOW_RELEASES),
                "reference_workflow_release": REFERENCE_WORKFLOW_RELEASE,
                "reason": "unvalidated_release",
            },
        )


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

    _check_workflow_version(workflow_version, root)

    if profile not in SUPPORTED_PROFILES:
        raise UnsupportedInstallProfileError(
            f"{root} runs Workflow {workflow_version!r} with profile {profile!r}, which is not "
            f"in the Controller's supported profile set {sorted(SUPPORTED_PROFILES)}",
            evidence={
                "root": str(root),
                "observed_workflow_version": workflow_version,
                "observed_profile": profile,
                "supported_profiles": sorted(SUPPORTED_PROFILES),
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
