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

Subclasses are introduced by the checkpoint that first needs them. This
checkpoint (CP1) needs exactly the ones the pinned-execution mechanism and
the runtime-root guard raise; later checkpoints add their own.
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
