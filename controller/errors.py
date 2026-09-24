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
    human-readable sentence (``message``), and an ``evidence`` mapping
    available for callers to inspect programmatically. ``controller.cli.main``
    itself reports only ``message``; it does not render ``evidence``.
    """

    code: str = "CONTROLLER_ERROR"

    def __init__(self, message: str, *, evidence: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.evidence: dict = evidence if evidence is not None else {}

    def __repr__(self) -> str:  # pragma: no cover -- diagnostic convenience
        return f"{type(self).__name__}({self.message!r}, evidence={self.evidence!r})"


class RuntimeContainmentError(ControllerError):
    """A write was attempted outside its permitted root.

    Raised by ``controller.runtime._assert_contained``, the package-wide
    containment guard: reached via ``write_json``/``write_bytes`` (through
    ``_atomic_write``) for the runtime root, and directly by
    ``identity.py``'s snapshot-materialisation sites against their own
    narrower roots. This is what makes "the Controller never writes
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

    Raised from four call sites, each of which sets
    ``evidence['raised_by']`` to name itself: ``"materialise"`` (a snapshot
    directory exists but fails re-verification, or the recorded/reused
    ``source_kind``/``source_commit`` disagree with the parent's exec
    handoff, or ``GENERATION.json`` is malformed or unreadable at both the
    committed ``HEAD`` and the worktree fallback), ``"pin"`` (a process
    already inside a snapshot finds its recomputed digest does not match
    both the directory's own name and its recorded ``tree_digest``, or an
    inherited ``WORKFLOW_CONTROLLER_EXEC_HANDOFF`` disagrees with what
    ``pin()`` itself just read), ``"cli.main"`` (the ``exec_depth >= 1``
    fail-safe: a process reached the ``"unpinned"`` branch after already
    having been re-execed once, and refuses a second exec rather than
    looping), or ``"detect"`` (``controller.handoff.detect``: the origin
    source repository's committed ``HEAD`` carries no readable, well-formed
    ``controller/GENERATION.json`` -- deliberately no worktree fallback
    here, since an uncommitted edit must never be read as an approved
    generation).
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
    """The manifest's ``workflow_version`` fails the Controller's two-tier
    baseline rule (``managed_repo.SUPPORTED_WORKFLOW_LINE`` then
    ``managed_repo.VALIDATED_WORKFLOW_RELEASES``), regardless of
    ``profile``. ``evidence['reason']`` distinguishes a version outside the
    supported line entirely (``"outside_supported_line"``) from one inside
    the line that has not been individually validated
    (``"unvalidated_release"``)."""

    code = "UNSUPPORTED_WORKFLOW_VERSION"


class UnsupportedInstallProfileError(ControllerError):
    """The manifest's ``profile`` is not in
    ``managed_repo.SUPPORTED_PROFILES``, even though ``workflow_version``
    itself is supported and validated."""

    code = "UNSUPPORTED_INSTALL_PROFILE"


# ---------------------------------------------------------------------------
# CP3 -- read-only target-repository Workflow state reading
# (``controller.target_state``).
# ---------------------------------------------------------------------------


class MissingWorkflowStateError(ControllerError):
    """The target repository has no
    ``docs/ai-workflow/WORKFLOW_STATE.json``.

    Raised by ``controller.target_state.read`` before anything else it
    does -- a managed repository with no Workflow state at all is not a
    state the Controller may reason about.
    """

    code = "MISSING_WORKFLOW_STATE"


class MalformedWorkflowStateError(ControllerError):
    """``WORKFLOW_STATE.json`` exists but cannot be trusted.

    Covers: invalid JSON, JSON that does not parse to an object, an
    absent or non-``1`` ``schema_version``, a ``work_items`` entry whose
    own ``work_item_id`` field disagrees with its key, and an
    ``active_work_item_id`` naming a work item absent from ``work_items``.
    Never a best-effort parse -- every one of these is a refusal.
    """

    code = "MALFORMED_WORKFLOW_STATE"


class UnknownPhaseError(ControllerError):
    """A work item's ``phase`` is outside the Controller's closed,
    twenty-member known-phase set (``target_state.KNOWN_PHASES``, a
    literal copy of the installed reference release's own
    ``workflow_state.KNOWN_PHASES`` -- the Controller never imports
    ``scripts/``).

    A phase the Controller does not recognise is never guessed at; a
    future Workflow release adding a phase must be a Controller release
    too.
    """

    code = "UNKNOWN_PHASE"


class AmbiguousWorkItemError(ControllerError):
    """No single work item can be resolved as the target without a
    guess.

    Raised by ``controller.target_state.select_work_item`` when: an
    explicit ``--work-item`` names an id absent from the snapshot; or no
    explicit id and no ``active_work_item_id`` are available and the
    snapshot's non-terminal work items number more than one.
    ``evidence['candidates']`` names every candidate considered -- the
    Controller never picks one on its own. The **zero**-candidate case with
    no explicit id (revision 63, B2) is *not* this refusal -- it returns
    ``target_state.NoWorkItemYet`` instead, since there is no candidate to
    be ambiguous among.
    """

    code = "AMBIGUOUS_WORK_ITEM"


class MalformedTargetRegistryError(ControllerError):
    """A work item's own declared ``registry_path`` cannot be trusted to
    derive ``registry_complete`` from.

    Raised when the path does not resolve to a file inside the
    repository, cannot be read or parsed as a JSON object, declares no
    non-empty ``checkpoints`` array of ``{"id": ...}`` entries, or whose
    own ``work_item_id`` field disagrees with the work item that declared
    it. A registry the Controller cannot authoritatively read is never
    read as vacuously complete or incomplete -- it is a named refusal.
    """

    code = "MALFORMED_TARGET_REGISTRY"


# ---------------------------------------------------------------------------
# CP4 -- next-action decision engine (``controller.decision``).
# ---------------------------------------------------------------------------


class MissingCommandsDirectoryError(ControllerError):
    """``controller.decision.classify_command_files``'s ``commands_dir``
    does not exist, or exists but is not a directory.

    ``Path.glob`` treats an absent directory as vacuously empty rather than
    refusing, which would otherwise let ``classify_command_files`` return
    ``{}`` for a missing/mis-pointed directory -- indistinguishable from a
    genuinely empty one, and weaker than the function's own declared
    fail-closed, total-classification contract (`O1`,
    MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW round 1: non-gating for
    Generation 1 today, since the only current callers already point at an
    existing directory, but left total against a future caller or a
    mis-pointed property check that would otherwise silently observe an
    empty command surface). ``evidence['commands_dir']`` names the path.
    """

    code = "MISSING_COMMANDS_DIRECTORY"


class NoSupportedActionError(ControllerError):
    """The observed phase has no automatic action, no human gate, and is
    not one of the deliberately inert phases (``LEGACY_READY``,
    ``MILESTONE_COMPLETE``).

    Raised for the four vocabulary phases the frozen Workflow release's
    own ``KNOWN_PHASES`` carries but no writer ever persists
    (``SELF_REVIEWING_PLAN``, ``AWAITING_TECHNICAL_APPROVAL``,
    ``FIXING_FUNCTIONAL_FINDINGS``, ``AWAITING_USER_ACCEPTANCE``) --
    ``evidence['phase']`` names the phase and ``message`` explains that no
    writer produces it -- and, defensively, for any phase string that
    reaches ``decision.decide`` without having gone through
    ``target_state.read``'s own ``UnknownPhaseError`` gate first, which
    should be unreachable in practice.
    """

    code = "NO_SUPPORTED_ACTION"


class HumanGateError(ControllerError):
    """A caller attempted to treat a gated :class:`~controller.decision.Decision`
    (``gate is not None``, or ``automatic`` is ``False``) as one safe to act
    on automatically.

    ``decision.decide`` itself never raises this -- a gate is ordinary,
    reportable data, not a refusal. It exists for the later checkpoints
    that consume a ``Decision`` (CP6's ``execute_step``, most directly):
    the launch guard those checkpoints implement is asserted positively in
    their own tests, and this is the named exception a defensive internal
    check raises if a non-automatic decision ever reached a worker launch
    despite that guard -- an invariant violation, never an ordinary
    control-flow path.
    """

    code = "HUMAN_GATE"


# ---------------------------------------------------------------------------
# CP5 -- fresh Claude worker abstraction (``controller.worker``).
# ---------------------------------------------------------------------------


class WorkerLaunchError(ControllerError):
    """The worker subprocess itself could not be started.

    Raised when ``subprocess.Popen`` fails with an ``OSError`` -- e.g. the
    resolved ``claude_bin`` does not exist or is not executable -- or when
    the worker's stream files cannot be created or opened before the spawn
    (``workflow-controller-release-runtime-observability`` CP4). Distinct
    from every outcome ``WorkerResult.outcome`` classifies: those all
    require the worker process to have actually started and either run to
    completion or be interrupted mid-flight.
    """

    code = "WORKER_LAUNCH_ERROR"


class WorkerFailedError(ControllerError):
    """A caller treated a :class:`~controller.worker.WorkerResult` whose
    ``outcome`` is ``FAILURE`` as though the worker had succeeded.

    Never raised by ``controller.worker.launch`` itself -- ``FAILURE`` is
    ordinary, reportable classification data, not a refusal. Reserved for
    a later checkpoint (CP6's job execution) that asserts a completed
    job's worker outcome before recording a Workflow transition as
    verified.
    """

    code = "WORKER_FAILED"


class WorkerInterruptedError(ControllerError):
    """A caller treated a :class:`~controller.worker.WorkerResult` whose
    ``outcome`` is ``INTERRUPTED`` as though the worker had completed.

    Never raised by ``controller.worker.launch`` itself, for the same
    reason as :class:`WorkerFailedError`.
    """

    code = "WORKER_INTERRUPTED"


class WorkerAmbiguousResultError(ControllerError):
    """A caller treated a :class:`~controller.worker.WorkerResult` whose
    ``outcome`` is ``AMBIGUOUS`` as though the worker's result were
    decidable.

    Never raised by ``controller.worker.launch`` itself, for the same
    reason as :class:`WorkerFailedError`.
    """

    code = "WORKER_AMBIGUOUS_RESULT"


class StaleJobRecordError(ControllerError):
    """A non-terminal job record could not be believed, and the whole
    ``resume`` call was aborted rather than reconciling around it.

    Raised by ``controller.job.resume`` when a record fails the
    validation pass (``controller.job.validate_record``) while its own
    ``status`` is non-terminal -- or absent/unrecognised, the fail-closed
    default for a status this generation cannot evaluate -- and also when
    a non-terminal record's ``status`` is outside the closed
    reconciliation table's ten-member enumeration entirely (the table's
    own last, unknown-status row). ``evidence`` names the record's
    ``job_id``, its ``work_item_id`` and the specific reason it could not
    be trusted. Never raised for a *terminal* record failing the same
    checks -- those are surfaced in ``resume``'s own result, marked
    malformed or unreadable, and never stop the reconciliation of live
    work beside them (round 8's I2's own carve-out).
    """

    code = "STALE_JOB_RECORD"


class UnreconcilableJobError(ControllerError):
    """A ``LAUNCHED`` job record's target shows evidence the Controller
    cannot account for: a moved Git ``HEAD`` alongside an unmoved phase
    and an unsatisfied predicate, or a phase that moved somewhere outside
    the action's own declared ``to_any_of`` set.

    Raised by ``controller.job.resume``. A deliberate refusal, not a
    heuristic -- guessing whether to retry could duplicate a commit or
    silently skip real work. ``evidence`` names both the pre- and
    post-state observations (phase and target ``HEAD``) so a human can
    reconcile the record manually.
    """

    code = "UNRECONCILABLE_JOB"


class GenerationHandoffPendingError(ControllerError):
    """The origin Controller source repository's committed ``HEAD``
    declares a generation number **older** than the one currently
    running -- a genuine revert, never the ordinary "source moved
    forward" handoff (that case returns a :class:`~controller.handoff.
    Handoff`, it never raises).

    Raised by ``controller.handoff.detect``. Both the running (pinned)
    and the approved generation are read from the *same* committed
    ``HEAD`` (CP1's own generation-source rule -- see ``identity.
    _read_generation`` and ``handoff._read_approved_generation``), so an
    uncommitted local bump to ``controller/GENERATION.json`` can never by
    itself make the two disagree; reaching this refusal means the origin
    repository's own history moved backwards under a running process,
    which means an assumption elsewhere is wrong, so this stops rather
    than silently continuing. ``evidence`` names the pinned/approved
    generation and commit pair.
    """

    code = "GENERATION_HANDOFF_PENDING"


class UserOnlyCommandError(ControllerError):
    """``controller.worker.launch`` refused a ``task`` naming one of
    ``USER_ONLY_COMMANDS``'s four bare command names.

    The second, independent denylist layer: ``controller.worker`` keeps
    its own literal copy of the four-command set and its own token scan,
    deliberately separate from CP4's ``controller.decision.
    derive_user_only_commands`` -- defence in depth for the single most
    consequential invariant in the milestone, "never fabricate user
    approval". Raised before any subprocess is spawned;
    ``evidence['matched_token']`` names the exact token that matched.
    """

    code = "USER_ONLY_COMMAND"


# ---------------------------------------------------------------------------
# workflow-controller-automatic-lifecycle-orchestration CP5 -- worker
# lifecycle and concurrency (``controller.lock``, ``controller.job``).
# ---------------------------------------------------------------------------


class LifecycleWorkerActiveError(ControllerError):
    """The target worktree's lifecycle lock is held, or a recorded worker
    may still be running there -- exit ``45``, never the blanket ``20``.

    Raised by ``controller.lock.acquire_lifecycle_lock`` when ``flock``
    raises ``BlockingIOError`` (contention, and only contention), and by
    ``controller.job.abandon`` when a record's liveness verdict refuses it.
    ``controller.cli.main`` catches this class in its own ``except`` clause
    *before* the blanket ``ControllerError`` handler. It launches nothing
    and reconciles nothing. ``evidence['lock_path']`` names the lock when
    the lock was the refusal.
    """

    code = "LIFECYCLE_WORKER_ACTIVE"


class LifecycleWorkerUnverifiableError(LifecycleWorkerActiveError):
    """``resume --abandon`` refused a record whose recorded worker is
    ``unverifiable`` here (another or unidentifiable host, another pid
    namespace, no boot identity with a possibly live group, or a
    ``killpg``-only answer) without ``--acknowledge-unverifiable-worker``.

    A subclass of :class:`LifecycleWorkerActiveError`, so it exits ``45``
    exactly as ``resume``'s own ``worker_unverifiable`` outcome does. It
    never names a process group to end: nothing observable here ties the
    recorded one to a running process.
    """

    code = "LIFECYCLE_WORKER_UNVERIFIABLE"


class LifecycleLockError(ControllerError):
    """The lifecycle lock could not be taken for any reason other than
    contention: an ``OSError`` from opening the git directory, or from
    ``flock`` with any errno other than ``EWOULDBLOCK``/``EAGAIN``
    (``ENOLCK``, ``EBADF``, ``EINVAL``, ...).

    A **sibling** of :class:`LifecycleWorkerActiveError`, never a subclass,
    so the exit-45 clause can never catch it: it exits ``20`` through the
    blanket handler. ``evidence`` names the path, the operation (``open``
    or ``flock``) and the errno's name.
    """

    code = "LIFECYCLE_LOCK_ERROR"


class GitDirectoryUnresolvableError(ControllerError):
    """A target root that still exists, but whose own git directory
    (``git rev-parse --absolute-git-dir``) cannot be resolved -- so there
    is no lifecycle lock to take. Never a silently skipped lock: exit
    ``20``. ``evidence`` names the root and Git's own stderr.
    """

    code = "GIT_DIRECTORY_UNRESOLVABLE"


class PendingJobReconciliationError(ControllerError):
    """``step``/``run`` refused to decide or launch because an earlier job
    file for this target is still pending reconciliation: a non-terminal
    record (``PLANNED``, ``LAUNCHED``, ``COMPLETED``, or a status outside
    the enumeration), or a file under ``jobs/`` that does not parse as a
    JSON object (its target cannot be read, so it counts, fail closed).

    Deliberately distinct from :class:`UnreconcilableJobError`, which
    means something else. ``evidence['pending_jobs']`` lists each pending
    job id with the command that clears it.
    """

    code = "PENDING_JOB_RECONCILIATION"


class JobAbandonRefusedError(ControllerError):
    """``resume --abandon JOB_ID`` refused: ``JOB_ID`` does not name a
    pending job file directly under ``jobs/``, or the record is terminal,
    belongs to another target, or was written by a newer Controller
    generation. Exit ``20``. A refusal on the recorded worker's liveness is
    :class:`LifecycleWorkerActiveError`/:class:`LifecycleWorkerUnverifiableError`
    instead (exit ``45``).
    """

    code = "JOB_ABANDON_REFUSED"


# ---------------------------------------------------------------------------
# workflow-controller-automatic-lifecycle-orchestration CP6 -- role-based
# worker routing (``controller.routing``).
# ---------------------------------------------------------------------------


class RoutingConfigError(ControllerError):
    """The ``--routing-config`` file cannot be used: it is unreadable or not
    JSON, its ``schema_version`` is not ``1``, or it carries an unknown
    key, an unknown role, a duplicate key, or a value that is not a
    non-empty string (or that begins with ``-``, so it would be read as an
    option). Raised before any job record is written or any worker is
    launched: exit ``20``, like every other malformed configuration.
    ``evidence`` names the path and the offending entry.

    A malformed ``--role-model``/``--role-effort`` on the command line is
    never this error: argparse refuses it as a usage error (exit ``2``).
    """

    code = "ROUTING_CONFIG_ERROR"


# ---------------------------------------------------------------------------
# workflow-controller-trunk-branch-pr-release-orchestration CP2 -- the
# repository policy (``controller.repo_policy``).
# ---------------------------------------------------------------------------


class InvalidRepositoryPolicyError(ControllerError):
    """A committed ``.workflow-controller/policy.json`` cannot be used: it is
    not UTF-8 JSON, carries a duplicate or unknown key, an unsupported
    ``schema_version``, an unknown adapter ``kind``, an unknown or misplaced
    placeholder, or a value that fails its rule (a branch or tag format that
    does not render to a valid ref, a tag format that is not invertible, an
    ``abandoned_tags`` entry that does not render from ``tag_format``, ...).
    Also raised when Git cannot say whether the policy exists or what it
    holds: an undecidable read is a refusal, never "absent".

    A present but inadmissible policy is never ignored: every command that
    consults it refuses, exit ``20``. ``evidence`` names the path, the
    revision read, the offending field and the problem.
    """

    code = "INVALID_REPOSITORY_POLICY"
