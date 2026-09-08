"""The Controller's refusal taxonomy.

Every fail-closed condition the Controller can reach is a named exception
deriving from :class:`ControllerError`. ``ControllerError`` is the only
exception type ``controller.cli`` catches at the top level -- anything else
propagates as a crash, because an unexpected exception is not a state the
Controller may reason about (``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``,
"Refusal taxonomy").

This module imports nothing from the rest of the package (the dependency
graph in the plan's "Dependency direction" section has every other module
importing, directly or transitively, from here).

Subclasses are introduced by the checkpoint that first needs them. CP1
needs exactly the ones the pinned-execution mechanism and the runtime-root
guard raise; CP2 adds the managed-repository-inspection refusals below;
later checkpoints add their own.
"""

from __future__ import annotations


class ControllerError(Exception):
    """Base class for every named Controller refusal.

    Carries a machine-readable ``code`` (defaults to the class name), a
    human-readable sentence (the exception's own ``str()``), and an
    ``evidence`` mapping the CLI reports verbatim rather than reconstructing
    from a traceback.
    """

    code: str = "CONTROLLER_ERROR"

    def __init__(self, message: str, *, evidence: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.evidence: dict = evidence if evidence is not None else {}

    def __repr__(self) -> str:  # pragma: no cover -- diagnostic convenience
        return f"{type(self).__name__}({self.message!r}, evidence={self.evidence!r})"


class RuntimeContainmentError(ControllerError):
    """A write was attempted outside the resolved runtime root.

    Raised by ``controller.runtime.write_json`` (and by anything that writes
    through it, including snapshot materialisation). There is no other write
    path in the package, so this is what makes "the Controller never writes
    Workflow state" a checked invariant rather than a promise.
    """

    code = "RUNTIME_CONTAINMENT_VIOLATION"


class RuntimeRootUnwritableError(ControllerError):
    """The resolved runtime root cannot be created or written.

    Carries the resolved path and the runtime-root ladder row that produced
    it (``evidence['path']``, ``evidence['ladder_row']``). Never a bare
    ``PermissionError`` or ``OSError`` -- the whole premise of this taxonomy
    is that every refusal is named and mapped to an exit code an outer
    supervisor can branch on.
    """

    code = "RUNTIME_ROOT_UNWRITABLE"


class DirtyControllerSourceError(ControllerError):
    """A non-read-only command was invoked against a dirty Controller source
    tree without ``--allow-dirty-source``.

    "Dirty" is scoped to the snapshot's own pathspec (``controller/`` and
    ``pyproject.toml``): ``git status --porcelain -- controller
    pyproject.toml`` producing any output.
    """

    code = "DIRTY_CONTROLLER_SOURCE"


class SourceSnapshotError(ControllerError):
    """The immutable-source-snapshot mechanism refused.

    Raised from three call sites only, each of which sets
    ``evidence['raised_by']`` to name itself: ``"materialise"`` (a snapshot
    directory exists but fails re-verification, or the recorded/reused
    ``source_kind``/``source_commit`` disagree with the parent's exec
    handoff, or ``GENERATION.json`` is malformed or unreadable at both the
    committed ``HEAD`` and the worktree fallback), ``"pin"`` (a process
    already inside a snapshot finds its recomputed digest does not match
    both the directory's own name and its recorded ``tree_digest``, or an
    inherited ``WORKFLOW_CONTROLLER_EXEC_HANDOFF`` disagrees with what
    ``pin()`` itself just read), or ``"cli.main"`` (the ``exec_depth >= 1``
    fail-safe: a process reached the ``"unpinned"`` branch after already
    having been re-execed once, and refuses a second exec rather than
    looping).
    """

    code = "SOURCE_SNAPSHOT_ERROR"


# ---------------------------------------------------------------------------
# CP2 -- managed-repository inspection (``controller.managed_repo``).
# ---------------------------------------------------------------------------


class NotARepositoryError(ControllerError):
    """The inspected path is not inside a Git working tree.

    Raised when ``git -C <path> rev-parse --show-toplevel`` does not exit
    0 -- checked before anything else ``managed_repo.inspect`` does.
    """

    code = "NOT_A_REPOSITORY"


class UnmanagedRepositoryError(ControllerError):
    """The repository has no ``.workflow-manager/installation.json``.

    Absence of the manifest is never treated as "assume clean" -- it means
    Workflow Manager has not installed anything here, which is exactly the
    unsafe-operation case the milestone brief asks the Controller to
    refuse.
    """

    code = "UNMANAGED_REPOSITORY"


class MalformedInstallationManifestError(ControllerError):
    """``.workflow-manager/installation.json`` exists but cannot be trusted.

    Raised when the file does not parse as a JSON object, or does not
    declare ``schema_version`` (present and equal to ``1``),
    ``workflow_version`` or ``profile``. A parsed field with no consumer is
    exactly what this checkpoint's ``schema_version`` gate closes.
    """

    code = "MALFORMED_INSTALLATION_MANIFEST"


class WorkflowManagerUnavailableError(ControllerError):
    """No usable ``workflow-manager`` executable could be resolved.

    Resolution is tried, in order, from ``--workflow-manager``, then
    ``WORKFLOW_CONTROLLER_WORKFLOW_MANAGER``, then
    ``shutil.which("workflow-manager")``. Absence is never treated as
    "assume clean".
    """

    code = "WORKFLOW_MANAGER_UNAVAILABLE"


class DriftedInstallationError(ControllerError):
    """``workflow-manager verify``/``status`` did not both exit ``0``.

    ``verify``'s exit code is the admission gate; ``status`` is run as a
    corroborating signal. ``evidence`` carries both commands' verbatim
    ``returncode``/``stdout``/``stderr`` -- the Controller never parses
    either command's prose for semantics, only its exit status.
    """

    code = "DRIFTED_INSTALLATION"


class UnsupportedWorkflowVersionError(ControllerError):
    """The manifest's ``workflow_version`` is outside the Controller's
    supported set (``managed_repo.SUPPORTED_INSTALLATIONS``), regardless
    of ``profile``."""

    code = "UNSUPPORTED_WORKFLOW_VERSION"


class UnsupportedInstallProfileError(ControllerError):
    """The manifest's ``(workflow_version, profile)`` pair is not in
    ``managed_repo.SUPPORTED_INSTALLATIONS``, even though
    ``workflow_version`` alone is supported."""

    code = "UNSUPPORTED_INSTALL_PROFILE"
