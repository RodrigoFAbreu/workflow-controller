"""The Controller's command-line surface (capability 10).

CP1 provided the skeleton: argument parsing for the full six-command
surface, the pinned-execution guard and the materialise-and-re-exec
mechanism that makes every non-read-only command run from an immutable
snapshot, and a fully working ``status`` command, with ``inspect``,
``explain``, ``step``, ``run`` and ``resume`` present in the command table
but stubbed. CP2-CP8 wired ``run`` (and, through it, the decision engine,
job execution, durable resume's own reconciliation module, and the
generation-handoff primitive). CP9 completes the surface: ``inspect``,
``explain``, ``step`` and ``resume`` are wired to their real behaviour
below, and the exit-code table (``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``,
"Exit codes") is now implemented in full, including ``40`` (``resume``
reconciling a record to ``INTERRUPTED``).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import threading
import time
from pathlib import Path

from controller import (
    evidence, handoff, identity, job, lock, managed_repo, milestone_branch, observe, routing, runtime, target_state,
    worker,
)
from controller.decision import Decision, decide_no_work_item, phase_to_wire
from controller.errors import ControllerError, LifecycleWorkerActiveError, SourceSnapshotError

#: The three read-only commands. Positive guard: everything not in this set
#: requires a pinned identity (`source_kind != "unpinned"`), by construction
#: rather than by a denylist a future command could be added without
#: updating.
READ_ONLY_COMMANDS = frozenset({"inspect", "explain", "status", "follow"})
ALL_COMMANDS = frozenset({"inspect", "explain", "status", "step", "run", "resume", "follow", "milestone-binding"})

#: The full exit-code contract (``docs/ai-workflow/CONTROLLER_GEN1_PLAN.md``,
#: "Exit codes"). ``2`` is argparse's own default usage-error code and is
#: never returned explicitly by this module.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_GATE = 10
EXIT_DECLINED = 15
EXIT_MAX_STEPS = 16
EXIT_FAIL_CLOSED = 20
EXIT_WORKER_FAILED = 30
EXIT_INCOMPLETE = 35
EXIT_INTERRUPTED = 40
#: `workflow-controller-automatic-lifecycle-orchestration` CP5: another
#: Controller or a previous worker holds the target worktree (its lifecycle
#: lock is held), or `resume` left a record alone because its recorded
#: worker may still run. Nothing was launched or reconciled.
EXIT_WORKER_ACTIVE = 45
EXIT_HANDOFF_PENDING = 50

#: Test-support surface (CP8): `--pause-file` is inert unless this
#: environment variable is also set to exactly `"1"`, so an ordinary
#: invocation -- including one that passes the flag by accident or by
#: copy-paste -- cannot pause.
TEST_HOOKS_ENV = "WORKFLOW_CONTROLLER_TEST_HOOKS"

_PAUSE_POLL_SECONDS = 0.05

#: The run record the current ``step``/``run`` created
#: (``workflow-controller-release-runtime-observability`` CP5).
#: ``cmd_step``/``cmd_run`` register it and never close it: the final exit
#: code is only known in :func:`main`, whose one ``finally`` closes it.
_open_run: job.RunRecord | None = None

#: ``step --follow``/``run --follow``'s renderer thread and its stop event
#: (release-runtime-observability CP6), started by :func:`_start_follower`
#: and stopped by :func:`main`.
_follower: tuple[threading.Thread, threading.Event] | None = None

#: The bounded join :func:`main` gives the renderer thread on completion.
FOLLOWER_JOIN_SECONDS = 2.0


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def require_pinned_execution() -> None:
    """The asserting mechanism: every non-read-only command function calls
    this on entry. In normal operation it never fires -- ``cli.main``'s own
    materialise-and-re-exec step (the *acting* mechanism) has already
    pinned the process by the time a command function is dispatched. It
    exists so a future code path that reaches dispatch without that step
    stops instead of running unpinned, and it is reachable in tests by
    calling a command function directly against an unpinned identity."""
    ident = identity.current()
    if ident.source_kind == identity.SOURCE_KIND_UNPINNED:
        raise SourceSnapshotError(
            "this command must run from a pinned, immutable Controller source snapshot",
            evidence={"raised_by": "cli.main", "source_kind": ident.source_kind},
        )


def _strip_runtime_dir(argv: list[str]) -> list[str]:
    """Drop any caller-supplied ``--runtime-dir``/``--runtime-dir=X`` from
    the argv the re-exec is about to build -- the resolved absolute value
    this process just computed supersedes it, and leaving both would make
    the child's runtime root depend on argparse's last-wins ordering."""
    out: list[str] = []
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token == "--runtime-dir":
            skip_next = True
            continue
        if token.startswith("--runtime-dir="):
            continue
        out.append(token)
    return out


def _reexec(*, snapshot_dir: Path, runtime_root: Path, argv: list[str],
            exec_depth_seen: int, source_kind: str, source_commit: str | None) -> None:
    """Replace this process with a fresh interpreter importing exactly the
    snapshot: ``-P`` suppresses the working-directory `sys.path` entry
    `python -m` would otherwise prepend (the exact defect that made
    revision 32's exec loop), and ``-B`` stops the import from writing
    `__pycache__` into the snapshot, which would otherwise change its own
    digest on its very first execution. Never returns."""
    child_argv = [
        sys.executable, "-P", "-B", "-m", "controller",
        "--runtime-dir", str(runtime_root),
        *_strip_runtime_dir(argv),
    ]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(snapshot_dir)
    env[identity.EXEC_HANDOFF_ENV] = json.dumps({
        "exec_depth": exec_depth_seen + 1,
        "source_kind": source_kind,
        "source_commit": source_commit,
    })
    os.execve(sys.executable, child_argv, env)  # noqa: S606 -- the whole mechanism


def _write_identity_record(runtime_root: Path, ident: identity.ControllerIdentity,
                            *, exec_depth: int) -> None:
    record = {
        "schema_version": 1,
        "generation": ident.generation,
        "source_root": str(ident.source_root),
        "origin_source_root": str(ident.origin_source_root) if ident.origin_source_root else None,
        "source_kind": ident.source_kind,
        "source_commit": ident.source_commit,
        "tree_digest": ident.tree_digest,
        "generation_source": ident.generation_source,
        "pinned_at": ident.pinned_at,
        "controller_runtime": identity.runtime_record(ident),
        "interpreter_executable": sys.executable,
        "interpreter_safe_path": bool(sys.flags.safe_path),
        "interpreter_sys_path": list(sys.path),
        "exec_depth": exec_depth,
    }
    runtime.write_json(runtime_root, "identity.json", record)


def _route_value(text: str) -> str:
    """``--model``/``--effort``'s ``type=``: a non-empty value that does not
    begin with ``-`` (``routing.check_value``); anything else is a usage
    error (exit 2)."""
    try:
        return routing.check_value(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _role_assignment(text: str) -> tuple[str, str]:
    """``--role-model``/``--role-effort``'s ``type=``: ``ROLE=VALUE`` with
    ``ROLE`` one of ``routing.ROLES``. A missing ``=``, an unknown role or
    an unusable value is a usage error (exit 2), before anything runs."""
    try:
        return routing.parse_role_assignment(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


class _RoleAssignmentAction(argparse.Action):
    """Collects repeated ``ROLE=VALUE`` assignments into a ``{role: value}``
    dict. Assigning one role twice is ambiguous, so it is a usage error
    (exit 2), never a silent last-wins."""

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        role, value = values
        assignments = dict(getattr(namespace, self.dest, None) or {})
        if role in assignments:
            raise argparse.ArgumentError(self, f"role {role!r} is assigned more than once")
        assignments[role] = value
        setattr(namespace, self.dest, assignments)


def version_text(ident: identity.ControllerIdentity | None = None) -> str:
    """``--version``'s line 1: exactly ``workflow-controller <version>``,
    the contract tests and the release pipeline assert. The version is the
    resolved runtime's -- ``ident``'s, else this process's pin's -- and
    ``unknown`` when this process's identity cannot be resolved (``--version``
    reports, it never fails on this)."""
    if ident is None:
        try:
            ident = identity.pin()
        except ControllerError:
            return f"workflow-controller {identity.UNKNOWN_VERSION}"
    return f"workflow-controller {ident.version}"


def _describe_running_runtime() -> str:
    """``describe_runtime`` for this process, or ``unidentified`` naming
    why its identity could not be resolved (a tampered snapshot, say) --
    ``--version`` and ``status`` report, they never fail on this."""
    try:
        return identity.describe_runtime(identity.pin())
    except ControllerError as exc:
        return f"unidentified ({exc.message})"


class _VersionAction(argparse.Action):
    """``--version``: computes its text only when the flag is used, so
    ``build_parser()`` stays side-effect free. Resolves no runtime root,
    writes nothing and needs no subcommand."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        print(version_text())
        print(f"runtime: {_describe_running_runtime()}")
        parser.exit(EXIT_OK)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow-controller")
    parser.add_argument("--version", action=_VersionAction,
                        help="print the Controller version and exit")
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--work-item", default=None)
    parser.add_argument("--workflow-manager", default=None)
    parser.add_argument("--claude-binary", default=None)
    parser.add_argument("--permission-mode", default=None)
    parser.add_argument("--timeout", type=int, default=None)
    # Role-based worker routing (automatic-lifecycle-orchestration CP6). The
    # per-role options beat the global ones, which beat the config file's
    # role entry, then its default, then the built-in route.
    parser.add_argument("--model", type=_route_value, default=None,
                        help="model for every launched worker, unless a --role-model overrides it")
    parser.add_argument("--effort", type=_route_value, default=None,
                        help="effort for every launched worker, unless a --role-effort overrides it")
    parser.add_argument("--role-model", metavar="ROLE=MODEL", type=_role_assignment,
                        action=_RoleAssignmentAction, default=None,
                        help="model for one role's workers (repeatable, once per role)")
    parser.add_argument("--role-effort", metavar="ROLE=EFFORT", type=_role_assignment,
                        action=_RoleAssignmentAction, default=None,
                        help="effort for one role's workers (repeatable, once per role)")
    parser.add_argument("--routing-config", metavar="PATH", default=None,
                        help="JSON routing config: {\"schema_version\": 1, \"default\": {...}, \"roles\": {...}}")
    parser.add_argument("--allow-dirty-source", action="store_true", default=False)
    parser.add_argument("--json", action="store_true", default=False)

    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_p = subparsers.add_parser("inspect")
    inspect_p.add_argument("repo")

    explain_p = subparsers.add_parser("explain")
    explain_p.add_argument("repo")

    step_p = subparsers.add_parser("step")
    step_p.add_argument("repo")
    step_p.add_argument("--follow", action="store_true", default=False,
                        help="render the run's events and worker output on stderr while it runs")

    run_p = subparsers.add_parser("run")
    run_p.add_argument("repo")
    run_p.add_argument("--follow", action="store_true", default=False,
                       help="render the run's events and worker output on stderr while it runs")
    run_p.add_argument("--max-steps", type=int, default=20)
    run_p.add_argument("--pause-file", default=None)

    resume_p = subparsers.add_parser("resume")
    resume_p.add_argument(
        "--abandon", metavar="JOB_ID", default=None,
        help="mark the pending job file JOB_ID terminal instead of reconciling (the operator "
             "disposition for a job file `resume` cannot reconcile)",
    )
    resume_p.add_argument(
        "--acknowledge-unverifiable-worker", action="store_true", default=False,
        help="with --abandon only: state that a worker whose liveness cannot be verified here is gone",
    )
    resume_p.add_argument("repo")

    subparsers.add_parser("status")

    # trunk-branch-pr-release-orchestration CP8 (TBR-R4-001): the operator
    # acknowledgement of a refusal-state binding record. `--work-item` (a
    # global option) is required; `main` refuses its absence as a usage
    # error.
    binding_p = subparsers.add_parser(
        "milestone-binding", help="acknowledge a milestone binding that is in a refusal state")
    disposition = binding_p.add_mutually_exclusive_group(required=True)
    disposition.add_argument("--new-pr", action="store_true", default=False,
                             help="rebind to the same branch; the next branch commit opens a new pull request")
    disposition.add_argument("--abandon", action="store_true", default=False,
                             help="retire the binding; the milestone is planned again from the trunk")
    binding_p.add_argument("repo")

    follow_p = subparsers.add_parser(
        "follow", help="follow a run or job's events and worker output, read-only")
    follow_target = follow_p.add_mutually_exclusive_group()
    follow_target.add_argument("--job", metavar="JOB_ID", default=None)
    follow_target.add_argument("--run", metavar="RUN_ID", default=None)
    follow_p.add_argument("--from-start", action="store_true", default=False,
                          help="replay every event, not only the last 20")
    follow_p.add_argument("repo", nargs="?", default=".")

    return parser


def cmd_status(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity,
               *, pre_existing: dict) -> int:
    """Controller-owned view: pinned identity, job records, pending
    handoff -- reported from what was durably present when this process
    *started*, since this same process writes a fresh ``identity.json``
    immediately before this body runs and that record must not be mistaken
    for evidence of prior work.

    The first line always describes the *running* process, the same text
    as ``--version``'s line 2."""
    print(f"controller: {version_text(ident)} -- {identity.describe_runtime(ident)}")
    if not pre_existing["had_any_state"]:
        print(f"no Controller runtime state at {runtime_root} (ladder row {pre_existing['ladder_row']})")
        return EXIT_OK

    if pre_existing["identity"] is not None:
        prev = pre_existing["identity"]
        print(f"pinned identity: source_kind={prev.get('source_kind')} "
              f"source_commit={prev.get('source_commit')} generation={prev.get('generation')} "
              f"tree_digest={prev.get('tree_digest')}")
    else:
        print("pinned identity: none recorded yet")

    job_ids = pre_existing["job_ids"]
    if job_ids:
        print(f"jobs: {', '.join(sorted(job_ids))}")
    else:
        print("jobs: none")

    if pre_existing["handoff"] is not None:
        print(f"handoff: pending ({pre_existing['handoff']})")
    else:
        print("handoff: none")

    _print_active(runtime_root)

    # CP8 (trunk-branch-pr-release-orchestration): one line per binding
    # record, only when one exists.
    for line in milestone_branch.binding_lines(runtime_root):
        print(line)

    print(f"runtime root: {runtime_root} (ladder row {pre_existing['ladder_row']})")
    return EXIT_OK


def _print_active(runtime_root: Path) -> None:
    """``status``'s ``active:`` section (release-runtime-observability
    CP6): every ``running`` run with its Controller's liveness and every
    non-terminal job with its worker's (read-only), across every target in
    this runtime root, each with the command that follows it."""
    runs, jobs = observe.active_runs(runtime_root), observe.active_jobs(runtime_root)
    if not runs and not jobs:
        print("active: none")
        return
    print("active:")
    for run, liveness in runs:
        process = run.get("controller_process") or {}
        print(f"  run {run.get('run_id')} ({run.get('command')}, target {run.get('target_repo')}): "
              f"controller pid {process.get('pid')} {liveness}")
        print(f"    follow: {job.follow_command(runtime_root, run.get('target_repo'))}")
    for record, liveness in jobs:
        print(f"  job {record.get('job_id')} ({record.get('status')}, target {record.get('target_repo')}): "
              f"{_job_activity_text(runtime_root, record, liveness)}")
        print(f"    follow: {job.follow_command(runtime_root, record.get('target_repo'))}")


def _job_activity_text(runtime_root: Path, record: dict, liveness: str | None) -> str:
    """One non-terminal job's activity: :func:`observe.job_activity`'s line
    for a record carrying ``worker_state`` (worker-lifecycle-ownership CP7),
    else the release-runtime-observability text, unchanged."""
    activity = observe.job_activity(record, runtime_root)
    if activity is not None:
        return activity["text"]
    process = record.get("worker_process") or {}
    return observe.drain_text(record) or (
        "no worker recorded" if liveness is None else f"worker pid {process.get('pid')} {liveness}")


def _target_jobs(runtime_root: Path, target: managed_repo.ManagedRepository) -> list[dict]:
    """``inspect``'s ``jobs`` block (worker-lifecycle-ownership CP7): one
    entry per non-terminal job for ``target``, read-only."""
    entries = []
    for record, liveness in observe.active_jobs(runtime_root):
        if record.get("target_repo") != str(target.root):
            continue
        activity = observe.job_activity(record, runtime_root)
        entry = {"job_id": record.get("job_id"), "status": record.get("status")}
        if activity is None:
            entry["text"] = _job_activity_text(runtime_root, record, liveness)
        else:
            entry.update(text=activity["text"], **observe.activity_fields(activity))
        entries.append(entry)
    return entries


def _print_target_jobs(entries: list[dict]) -> None:
    if not entries:
        return
    print("jobs:")
    for entry in entries:
        print(f"  job {entry['job_id']} ({entry['status']}): {entry['text']}")


def _follow_runtime_root(args: argparse.Namespace) -> Path:
    """The runtime root ``follow`` reads: the one ``step`` would use, found
    read-only -- the running code's kind from ``identity.resolve_runtime``
    (or a snapshot's own ``SOURCE_PIN.json``), never ``pin()``,
    materialisation or any write."""
    code_root = Path(identity.__file__).resolve().parent.parent
    pin = runtime.read_json(code_root / identity._SOURCE_PIN_NAME) if (
        code_root / identity._SOURCE_PIN_NAME).is_file() else None
    if pin is not None:
        origin = Path(pin["origin_source_root"]) if pin.get("origin_source_root") else code_root
        kind = pin.get("runtime_kind", identity.RUNTIME_KIND_SOURCE)
    else:
        origin, kind = code_root, identity.resolve_runtime(code_root).runtime_kind
    runtime_root, _row = runtime.resolve_runtime_root(
        runtime_dir=args.runtime_dir, origin_source_root=origin, runtime_kind=kind,
    )
    return runtime_root


def _stdout_sink(text: str) -> None:
    print(text, flush=True)


def cmd_follow(args: argparse.Namespace) -> int:
    """``follow`` (release-runtime-observability CP6): print a run's or a
    job's events and worker output as they are written, read-only.

    Dispatched before runtime-root creation, pinning, materialisation and
    the ``identity.json`` write: it writes nothing, takes no lock and sends
    no signal. With ``--run``/``--job`` it follows that record, live or
    finished, refusing (exit 20) an unknown id or a record for another
    target. Otherwise it follows the newest running run for ``<repo>``, or
    an orphaned job whose worker is still active, or says there is nothing
    to follow (exit 0). It never mirrors the followed run's exit code.

    A stdout whose reader has gone (``follow | head``) ends the follow
    like Ctrl-C does, with exit 0 and no traceback: the operator stopped
    reading, and nothing else is affected."""
    try:
        return _follow(args)
    except BrokenPipeError:
        # Nothing more can reach the reader. Dropping `sys.stdout` stops the
        # interpreter's final flush of the unwritten buffer from raising
        # again at exit (Python's exit 120).
        sys.stdout = None
        return EXIT_OK


def _follow(args: argparse.Namespace) -> int:
    runtime_root = _follow_runtime_root(args)
    target_repo = str(managed_repo._resolve_repository_root(Path(args.repo)))
    kind, record_id = ("run", args.run) if args.run else ("job", args.job) if args.job else (None, None)
    if kind is None:
        selected = observe.select_active(runtime_root, target_repo)
        if selected is None:
            last = observe.last_run(runtime_root, target_repo)
            if last is None:
                _stdout_sink(f"nothing active for {target_repo}; no runs recorded")
            else:
                _stdout_sink(f"nothing active for {target_repo}; last run {last['run_id']} "
                             f"{_run_outcome(last)}; replay: workflow-controller --runtime-dir {runtime_root} "
                             f"follow --run {last['run_id']} --from-start {target_repo}")
            return EXIT_OK
        kind, record_id = selected
    record = (observe.read_run if kind == "run" else observe.read_job)(runtime_root, record_id)
    if record is None:
        raise ControllerError(
            f"no readable {kind} record {record_id!r} under {runtime_root}",
            evidence={"kind": kind, "id": record_id, "runtime_root": str(runtime_root)},
        )
    if record.get("target_repo") != target_repo:
        raise ControllerError(
            f"{kind} {record_id} belongs to target {record.get('target_repo')!r}, not {target_repo}",
            evidence={"kind": kind, "id": record_id, "target_repo": record.get("target_repo")},
        )
    follow = observe.follow_run if kind == "run" else observe.follow_job
    try:
        follow(runtime_root, record_id, _stdout_sink, from_start=args.from_start, json_output=args.json)
    except KeyboardInterrupt:
        pass  # the operator stopped following; nothing else is affected
    return EXIT_OK


def _run_outcome(run: dict) -> str:
    if run.get("state") == job.RUN_STATE_ENDED:
        return f"ended with exit {run.get('exit_code')}"
    if run.get("state") == job.RUN_STATE_INTERRUPTED:
        return "was interrupted"
    return "was not closed"


def _start_follower(args: argparse.Namespace, runtime_root: Path, run_id: str) -> None:
    """The one place ``--follow`` is read. When set, start a daemon thread
    rendering run ``run_id`` from its durable logs onto a private duplicate
    of stderr (:class:`observe.FdSink`). The lifecycle calls are the same
    either way; the thread's failures stay in the thread."""
    global _follower
    if not getattr(args, "follow", False):
        return
    try:
        fd = os.dup(2)
    except OSError:
        return
    sink = observe.FdSink(fd)
    stop = threading.Event()
    thread = threading.Thread(target=_render_run, args=(runtime_root, run_id, sink, stop),
                              name="workflow-controller-follow", daemon=True)
    thread.start()
    _follower = (thread, stop)


def _render_run(runtime_root: Path, run_id: str, sink: observe.FdSink, stop: threading.Event) -> None:
    """The renderer thread's body. Any exception disables rendering for the
    rest of the process (one note, if the descriptor still works) and is
    never propagated."""
    try:
        observe.follow_run(runtime_root, run_id, sink, from_start=True, stop=stop)
    except Exception as exc:  # noqa: BLE001 -- a renderer failure never reaches the lifecycle
        sink.fail(exc)
    finally:
        try:
            os.close(sink.fd)
        except OSError:
            pass


def _stop_follower(*, join: bool) -> None:
    """Tell the renderer thread to drain and stop; give it
    :data:`FOLLOWER_JOIN_SECONDS` when ``join``. A thread still running
    after that is abandoned (it is a daemon)."""
    global _follower
    follower, _follower = _follower, None
    if follower is None:
        return
    thread, stop = follower
    stop.set()
    if join:
        thread.join(FOLLOWER_JOIN_SECONDS)


def _inspect_target(args: argparse.Namespace) -> managed_repo.ManagedRepository:
    """The five-check managed-repository read every non-``status`` command
    performs first (capability 1) -- factored once so ``inspect``,
    ``explain``, ``step``, ``run`` and ``resume`` all fail the same way on
    an unmanaged/drifted/unsupported target."""
    return managed_repo.inspect(args.repo, manager_bin=args.workflow_manager)


def _work_item_payload(work_item: target_state.WorkItemView) -> dict:
    return {
        "work_item_id": work_item.work_item_id,
        "work_item_type": work_item.work_item_type,
        "work_item_kind": work_item.work_item_kind,
        "governing_workflow_version": work_item.governing_workflow_version,
        "phase": work_item.phase,
        "plan_revision": work_item.plan_revision,
        "implementation_revision": work_item.implementation_revision,
        "functional_review_round": work_item.functional_review_round,
        "current_checkpoint_id": work_item.current_checkpoint_id,
        "last_completed_checkpoint_id": work_item.last_completed_checkpoint_id,
        "registry_complete": work_item.registry_complete,
        "incomplete_children": list(work_item.incomplete_children),
    }


def cmd_inspect(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    """``inspect``: managed-repo verification + Workflow state summary;
    read-only (capabilities 1 and 2)."""
    target = _inspect_target(args)
    snapshot = target_state.read(target)
    work_item = target_state.select_work_item(snapshot, work_item_id=args.work_item)

    # CP5: the non-acquiring lifecycle-lock probe -- `unknown` never means
    # `free`, and `step` decides by acquiring, never by this report.
    lock_state = lock.probe_lifecycle_lock(target.root)
    # worker-lifecycle-ownership CP7: the target's non-terminal jobs and
    # what each is doing, beside the lock; omitted when there is none.
    jobs = _target_jobs(runtime_root, target)
    jobs_block = {"jobs": jobs} if jobs else {}
    # CP8 (trunk-branch-pr-release-orchestration): `repository_policy` and
    # `milestone_branch`, from local Git and the binding records only, each
    # omitted -- never `null` -- when it does not apply (I1, I10).
    branch_blocks = milestone_branch.observation(_branch_context(target, runtime_root))

    if work_item is target_state.NoWorkItemYet:
        # revision 63/64's B2 bootstrap sentinel (CONTROLLER_GEN1_PLAN.md's
        # "NoWorkItemYet CLI dispatch"): there is no WorkItemView to build a
        # payload from -- report the bootstrap state directly instead.
        if args.json:
            print(json.dumps({
                "repository": {
                    "root": str(target.root),
                    "workflow_version": target.workflow_version,
                    "profile": target.profile,
                },
                "work_item": None,
                "lifecycle_lock": lock_state,
                **jobs_block,
                "controller": identity.runtime_record(ident),
                **branch_blocks,
            }))
            return EXIT_OK
        print(f"repository: {target.root} (Workflow {target.workflow_version}, profile {target.profile})")
        print(f"lifecycle lock: {lock_state}")
        _print_target_jobs(jobs)
        print("work item: none -- no non-terminal work item exists and none was explicitly named")
        _print_branch_blocks(branch_blocks)
        return EXIT_OK

    if args.json:
        print(json.dumps({
            "repository": {
                "root": str(target.root),
                "workflow_version": target.workflow_version,
                "profile": target.profile,
            },
            "work_item": _work_item_payload(work_item),
            "lifecycle_lock": lock_state,
            **jobs_block,
            "controller": identity.runtime_record(ident),
            **branch_blocks,
        }))
        return EXIT_OK

    print(f"repository: {target.root} (Workflow {target.workflow_version}, profile {target.profile})")
    print(f"lifecycle lock: {lock_state}")
    _print_target_jobs(jobs)
    print(f"work item: {work_item.work_item_id} "
          f"(type={work_item.work_item_type} kind={work_item.work_item_kind} "
          f"governing_workflow_version={work_item.governing_workflow_version})")
    print(f"phase: {work_item.phase}")
    print(f"plan_revision={work_item.plan_revision} "
          f"implementation_revision={work_item.implementation_revision} "
          f"functional_review_round={work_item.functional_review_round}")
    print(f"current_checkpoint_id={work_item.current_checkpoint_id} "
          f"last_completed_checkpoint_id={work_item.last_completed_checkpoint_id} "
          f"registry_complete={work_item.registry_complete}")
    if work_item.incomplete_children:
        print(f"incomplete children: {', '.join(work_item.incomplete_children)}")
    _print_branch_blocks(branch_blocks)
    return EXIT_OK


def _branch_context(target: managed_repo.ManagedRepository, runtime_root: Path) -> milestone_branch.Context:
    return milestone_branch.Context(repo_root=target.root, runtime_root=runtime_root)


def _print_branch_blocks(blocks: dict) -> None:
    """``inspect``'s text form of the CP8 blocks: one line each, printed
    only when the block is present."""
    policy = blocks.get("repository_policy")
    if policy is not None:
        print(f"repository policy: {policy['path']} ({policy['source']}, sha256 {policy['sha256']}) "
              f"milestone_branches={policy['milestone_branches_enabled']} release={policy['release_enabled']} "
              f"trunk={policy['trunk']['remote']}/{policy['trunk']['branch']} forge={policy['forge_repository']}")
    binding = blocks.get("milestone_branch")
    if binding is not None:
        pr = binding["pr"]
        pr_text = "no pull request" if pr is None else (
            f"pull request #{pr['number']} {pr['url']}{' (draft)' if pr['draft'] else ''}")
        observed = binding["last_observation"]
        drift = "not observed yet" if observed is None else (
            f"{'fresh' if observed['fresh'] else 'behind'} ({observed['behind']} behind the trunk), "
            f"observed {observed['observed_at']}")
        print(f"milestone branch: {binding['branch']} for {binding['work_item_id']} ({binding['state']}), "
              f"branch point {binding['branch_point']}, {pr_text}, {drift}")


def cmd_explain(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    """``explain``: the next-action decision with full evidence, and, at a
    gate, exactly what a human must do (capability 3). Read-only -- it
    only ever calls :func:`controller.evidence.decide`, never
    :func:`controller.job.execute_step`, so no job record is written and
    no worker is launched."""
    target = _inspect_target(args)
    # CP5: the job files `step`/`run` would refuse on, and the lifecycle
    # lock, are reported ahead of the decision -- for a work item and for
    # the no-work-item bootstrap alike -- through the same read-only scan
    # the refusal uses. Neither changes the exit code.
    pending = job.pending_reconciliation_jobs(runtime_root, target, ident)
    lock_state = lock.probe_lifecycle_lock(target.root)
    # worker-lifecycle-ownership CP7: each pending job's activity, and the
    # newest terminal job's stream diagnosis when it has one to explain.
    pending_details = [_pending_job_details(runtime_root, target, entry) for entry in pending]
    last_job = _last_job_diagnosis(runtime_root, target)
    last_job_block = {} if last_job is None else {"last_job": last_job}
    if not args.json:
        _print_pending_jobs(pending, pending_details)
        _print_last_job(last_job)
        print(f"lifecycle lock: {lock_state}")
    snapshot = target_state.read(target)
    work_item = target_state.select_work_item(snapshot, work_item_id=args.work_item)
    # CP8 (trunk-branch-pr-release-orchestration): the preflight outcome the
    # next step would take, from local Git and the records only (no fetch,
    # no `gh`); `None`, and the key omitted, when no policy or binding
    # applies (I1, I10). Its predicted trunk-start base is the bootstrap's
    # base, as `step` passes the passed trunk start's (squash-merge Design F).
    preflight = milestone_branch.predict(_branch_context(target, runtime_root), requested_work_item_id=args.work_item)
    preflight_block = {} if preflight is None else {"repository_preflight": preflight}
    # The same job-history read `job.execute_step` makes (automatic-
    # lifecycle-orchestration CP4B), so `explain` and `step` see the same
    # apply relaunch bound. It never raises on a job file and writes nothing.
    decision = (
        decide_no_work_item(target, base=None if preflight is None else preflight["base"])
        if work_item is target_state.NoWorkItemYet
        else evidence.decide(
            target, snapshot, work_item,
            last_apply_job=job.last_launched_apply_job_view(runtime_root, target.root, work_item.work_item_id),
        )
    )

    if args.json:
        gate = decision.gate
        print(json.dumps({
            "observed_phase": phase_to_wire(decision.observed_phase),
            "evidence": list(decision.evidence),
            "automatic": decision.automatic,
            "declined": decision.declined,
            "action": decision.action.command if decision.action is not None else None,
            "task_addendum": decision.action.task_addendum if decision.action is not None else None,
            "reason": decision.reason,
            "gate": None if gate is None else {
                "repository": gate.repository,
                "work_item_id": gate.work_item_id,
                "phase": gate.phase,
                "what_is_required": gate.what_is_required,
                "artifact_path": gate.artifact_path,
                "safe_resume_command": gate.safe_resume_command,
            },
            "pending_jobs": [{**entry.to_dict(), **details} for entry, details in zip(pending, pending_details)],
            **last_job_block,
            "lifecycle_lock": lock_state,
            "controller": identity.runtime_record(ident),
            **preflight_block,
        }))
        return EXIT_OK

    if preflight is not None:
        gate_text = "" if preflight["gate"] is None else f" ({preflight['gate']})"
        print(f"repository preflight: {preflight['action']}{gate_text} -- {preflight['detail']}")
    print(f"phase: {decision.observed_phase}")
    for line in decision.evidence:
        print(f"  evidence: {line}")
    print(f"reason: {decision.reason}")
    if decision.gate is not None:
        gate = decision.gate
        print(f"human gate -- what is required: {gate.what_is_required}")
        if gate.artifact_path is not None:
            print(f"  artifact: {gate.artifact_path}")
        print(f"  safe resume command: {gate.safe_resume_command}")
    elif decision.automatic:
        print(f"next automatic action: {decision.action.command}")
        if decision.action.task_addendum is not None:
            print(f"  task addendum: {decision.action.task_addendum}")
    elif decision.declined:
        print(f"declined by this generation (automation-safe, but not automated here): "
              f"{decision.action.command}")
    else:
        print("no action at this phase (e.g. LEGACY_READY or MILESTONE_COMPLETE)")
    return EXIT_OK


def _print_pending_jobs(pending: list, details: list[dict] | None = None) -> None:
    """``explain``'s pending report (CP5): each job file ``step``/``run``
    refuse on, with the command that clears it -- and, for a record carrying
    ``worker_state`` (worker-lifecycle-ownership CP7), its activity and its
    stream diagnosis."""
    if not pending:
        return
    print(f"pending job files: {len(pending)} -- `step`/`run` refuse until each is cleared")
    for entry, extra in zip(pending, details or [{}] * len(pending)):
        print(f"  pending job: {entry.job_id} ({entry.status or 'unreadable'}) -- {entry.reason}; "
              f"clear it with: {entry.clearing_command}")
        if extra.get("activity_text"):
            print(f"    activity: {extra['activity_text']}")
        for line in observe.diagnosis_lines(extra["stream_diagnosis"]) if extra.get("stream_diagnosis") else []:
            print(f"    {line}")


def _pending_job_details(runtime_root: Path, target: managed_repo.ManagedRepository, entry: job.PendingJob) -> dict:
    """A pending job's CP7 fields for ``explain`` (worker-lifecycle-
    ownership, plan G): ``activity``, ``worker_state``, ``waiting_on``,
    ``owned_processes`` (and the rest of :func:`observe.activity_fields`),
    plus ``stream_diagnosis`` for a ``COMPLETED`` record -- all omitted for
    a record without ``worker_state``, whose report is unchanged."""
    record = observe.read_job(runtime_root, entry.job_id)
    if record is None or record.get("target_repo") != str(target.root):
        return {}
    activity = observe.job_activity(record, runtime_root)
    if activity is None:
        return {}
    details = {**observe.activity_fields(activity), "activity_text": activity["text"]}
    diagnosis = observe.stream_diagnosis(record) if record.get("status") == job.STATUS_COMPLETED else None
    if diagnosis is not None:
        details["stream_diagnosis"] = diagnosis
    return details


def _last_job_diagnosis(runtime_root: Path, target: managed_repo.ManagedRepository) -> dict | None:
    """The newest job for ``target``, when it is terminal and its stream
    diagnosis has something to explain (:func:`observe.stream_diagnosis`)."""
    records = [r for r in observe.list_jobs(runtime_root) if r.get("target_repo") == str(target.root)]
    if not records or records[-1].get("status") not in job.TERMINAL_STATUSES:
        return None
    diagnosis = observe.stream_diagnosis(records[-1])
    if diagnosis is None:
        return None
    return {"job_id": records[-1].get("job_id"), "status": records[-1].get("status"), "stream_diagnosis": diagnosis}


def _print_last_job(last_job: dict | None) -> None:
    if last_job is None:
        return
    print(f"last job: {last_job['job_id']} ({last_job['status']})")
    for line in observe.diagnosis_lines(last_job["stream_diagnosis"]):
        print(f"  {line}")


def _routing_options(args: argparse.Namespace) -> routing.RoutingOptions:
    """The operator's routing overrides (CP6), with the ``--routing-config``
    file parsed now -- before any job record is written -- so a malformed
    one is ``RoutingConfigError`` (exit 20) and nothing runs. Read with
    ``getattr`` defaults, as ``cmd_resume`` reads ``--abandon``, so a
    caller's hand-built namespace without these options routes by the
    built-in table."""
    config_path = getattr(args, "routing_config", None)
    return routing.RoutingOptions(
        cli_model=getattr(args, "model", None),
        cli_effort=getattr(args, "effort", None),
        cli_role_models=getattr(args, "role_model", None) or {},
        cli_role_efforts=getattr(args, "role_effort", None) or {},
        config=None if config_path is None else routing.load_routing_config(config_path),
    )


def _run_one_step(
    args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity,
    target: managed_repo.ManagedRepository, routing_options: routing.RoutingOptions = routing.NO_OVERRIDES,
) -> tuple[int, dict | Decision | None]:
    """One orchestration boundary: a generation-handoff check, then (at
    most) one job execution. Shared by ``step`` (one call) and ``run``'s
    own loop body (repeated until a stop condition) -- both `step`'s "stop
    after exactly one action" and `run`'s own boundary discipline (CP8:
    handoff detection runs "after a job finishes, before the next one
    starts, never mid-job") are this same check, run once per call.

    Returns ``(exit_code, result)``, where ``result`` is ``None`` for a
    handoff, a :class:`~controller.decision.Decision` for the no-action
    class, or a ``JobRecord`` otherwise.

    With a run open (CP5), a detected handoff and a no-action decision are
    recorded in its log, and the job it launches carries its ``run_id``."""
    run = _open_run
    origin_source_root = ident.origin_source_root or ident.source_root
    pending = handoff.detect(ident, origin_source_root)
    if pending is not None:
        jobs_complete, jobs_open = _classify_jobs(runtime_root)
        handoff.write_handoff_record(
            runtime_root, pending,
            jobs_complete=jobs_complete, jobs_open=jobs_open,
            next_generation_command=_next_generation_command(runtime_root, args.repo),
            now=_now(),
        )
        if run is not None:
            run.event("handoff_detected")
        return EXIT_HANDOFF_PENDING, None

    result = job.execute_step(
        target,
        work_item_id=args.work_item,
        identity=ident,
        runtime=runtime_root,
        permission_mode=args.permission_mode or job.DEFAULT_PERMISSION_MODE,
        timeout=args.timeout,
        claude_bin=args.claude_binary,
        routing=routing_options,
        run_id=None if run is None else run.run_id,
    )

    if isinstance(result, Decision):
        # LEGACY_READY / MILESTONE_COMPLETE: nothing ran, nothing is
        # pending.
        if run is not None:
            run.event("no_action", observed_phase=phase_to_wire(result.observed_phase), reason=result.reason)
        return EXIT_OK, result

    # `execute_step`'s own `status` field, mapped to the exit-code table
    # (`docs/ai-workflow/CONTROLLER_GEN1_PLAN.md`, "Exit codes"). `FINISHED`
    # maps to `EXIT_OK` here too -- the distinction between "finished, keep
    # going" (`run`) and "finished, stop" (`step`) is the caller's own loop
    # structure, never a different exit code for the same status.
    status_exit_codes = {
        job.STATUS_GATE_BLOCKED: EXIT_GATE,
        job.STATUS_DECLINED: EXIT_DECLINED,
        job.STATUS_HANDOFF_PENDING: EXIT_HANDOFF_PENDING,
        job.STATUS_FAILED: EXIT_WORKER_FAILED,
        job.STATUS_INCOMPLETE: EXIT_INCOMPLETE,
        job.STATUS_FINISHED: EXIT_OK,
    }
    return status_exit_codes[result["status"]], result


def cmd_step(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    """``step``: execute exactly one automatic action, validate the
    transition, stop (capabilities 5, 7, 8)."""
    require_pinned_execution()
    routing_options = _routing_options(args)
    target = _inspect_target(args)
    run = _start_run("step", runtime_root, ident, target, max_steps=None)
    _start_follower(args, runtime_root, run.run_id)
    run.event("step_started", n=1)
    try:
        exit_code, _result = _run_one_step(args, runtime_root, ident, target, routing_options)
    finally:
        # Every finished child the launch recorded is collected before the
        # command returns (the between-launch reaper keeps collecting the
        # ones still running).
        worker.reap_adopted_children()
    return exit_code


def _start_run(command: str, runtime_root: Path, ident: identity.ControllerIdentity,
               target: managed_repo.ManagedRepository, *, max_steps: int | None) -> job.RunRecord:
    """Create this invocation's run record and register it in
    :data:`_open_run` for :func:`main` to close. ``command`` is the
    subcommand name, never argv; ``target_repo`` is the canonical root job
    records carry."""
    global _open_run
    if _open_run is not None:
        _open_run.discard()  # never closed (a direct call, not through `main`)
    _open_run = job.open_run(runtime_root, command=command, target_repo=str(target.root),
                             max_steps=max_steps, ident=ident)
    return _open_run


def _classify_jobs(runtime_root: Path) -> tuple[list[str], list[str]]:
    """The job ids under ``<runtime_root>/jobs/*.json``, partitioned by
    ``controller.job``'s own closed terminal/non-terminal status
    enumeration -- ``handoff.write_handoff_record``'s
    ``jobs_complete``/``jobs_open`` lists. In the ordinary case this runs
    at CP6's orchestration boundary, strictly between jobs, so
    ``jobs_open`` is normally empty; it is computed properly regardless,
    rather than assumed empty, so a record left non-terminal by an
    out-of-band crash is still reported rather than silently dropped."""
    jobs_dir = runtime_root / "jobs"
    complete: list[str] = []
    open_: list[str] = []
    if jobs_dir.is_dir():
        for path in sorted(jobs_dir.glob("*.json")):
            record = runtime.read_json(path) or {}
            status = record.get("status")
            if status in job.TERMINAL_STATUSES:
                complete.append(path.stem)
            else:
                open_.append(path.stem)
    return complete, open_


def _next_generation_command(runtime_root: Path, repo: str) -> str:
    """The exact command to start the next generation: the same runtime
    root (so the new generation's own run picks up this one's durable job
    history) and the same target repository."""
    return f"workflow-controller --runtime-dir {runtime_root} run {repo}"


def _await_pause_file(pause_file: str | None) -> None:
    """Block at the orchestration boundary while ``pause_file`` exists --
    test-support surface only, inert unless ``WORKFLOW_CONTROLLER_TEST_HOOKS``
    is set to exactly ``"1"``."""
    if not pause_file or os.environ.get(TEST_HOOKS_ENV) != "1":
        return
    pause_path = Path(pause_file)
    while pause_path.exists():
        time.sleep(_PAUSE_POLL_SECONDS)


def cmd_run(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    require_pinned_execution()

    if args.pause_file and os.environ.get(TEST_HOOKS_ENV) != "1":
        print(
            "--pause-file is test-support surface, inert unless "
            f"{TEST_HOOKS_ENV}=1 is also set",
            file=sys.stderr,
        )

    routing_options = _routing_options(args)
    target = _inspect_target(args)
    run = _start_run("run", runtime_root, ident, target, max_steps=args.max_steps)
    _start_follower(args, runtime_root, run.run_id)

    try:
        return _run_steps(args, runtime_root, ident, target, routing_options, run)
    finally:
        worker.reap_adopted_children()


def _run_steps(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity,
               target: managed_repo.ManagedRepository, routing_options: routing.RoutingOptions,
               run: job.RunRecord) -> int:
    """``cmd_run``'s loop: one step per orchestration boundary, until a
    step does not finish or ``--max-steps`` is reached."""
    steps_run = 0
    while steps_run < args.max_steps:
        # The orchestration boundary: checked once before every job this
        # loop starts, including the first -- never mid-job. Both halves
        # (the test-support pause and the real handoff detection, inside
        # `_run_one_step`) are checked here, together, per the plan's own
        # "the run loop checks for the file only at the same orchestration
        # boundary where detect() runs".
        _await_pause_file(args.pause_file)

        run.event("step_started", n=steps_run + 1)
        exit_code, result = _run_one_step(args, runtime_root, ident, target, routing_options)
        steps_run += 1
        worker.reap_adopted_children()

        if isinstance(result, dict) and result["status"] == job.STATUS_FINISHED:
            # This checkpoint/action is done -- loop back to the
            # orchestration boundary and decide the next one.
            continue

        # Every other outcome stops the loop: the no-action class
        # (`result` is a `Decision`, exit 0), a handoff (`result` is
        # `None`, exit 50), or one of `GATE_BLOCKED`/`DECLINED`/
        # `HANDOFF_PENDING`/`FAILED`/`INCOMPLETE` (a `dict`, its own exit
        # code -- `HANDOFF_PENDING` here is a `handoff.json` this loop did
        # not itself write, e.g. left over from a prior invocation, since
        # `_run_one_step`'s own handoff check already caught the ordinary
        # case first).
        return exit_code

    return EXIT_MAX_STEPS


def _abandon_summary(record: dict, runtime_root: Path) -> str:
    evidence_block = record.get("reconciliation_evidence") or {}
    job_id = record.get("job_id", "<unknown>")
    if "original" in evidence_block:
        return (
            f"{job_id}: {record.get('status')} ({evidence_block.get('code')}) -- the file was not a job "
            f"record this Controller can read, so its target could not be read and it was abandoned for "
            f"every target; its original bytes are at {runtime_root / evidence_block['original']}"
        )
    line = (
        f"{job_id}: {record.get('status')} ({evidence_block.get('code')}; was "
        f"{evidence_block.get('abandoned_status')}; validity={evidence_block.get('validity')})"
    )
    liveness = record.get("worker_liveness")
    if isinstance(liveness, dict):
        acknowledged = " (acknowledged)" if liveness.get("acknowledged") else ""
        line += f"; recorded worker liveness: {liveness.get('verdict')}{acknowledged}"
    return line


def cmd_resume(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    """``resume``: reconcile non-terminal job records, then report
    (capability 7). Never launches a worker -- ``controller.job.resume``
    contains no call to ``controller.worker.launch`` at all.

    ``resume --abandon JOB_ID [--acknowledge-unverifiable-worker]`` (CP5)
    instead marks that one pending job file terminal
    (:func:`controller.job.abandon`) and reconciles nothing else. Exit 45
    when ``resume`` left a record alone because its recorded worker is
    ``active`` or ``unverifiable``, or (worker-lifecycle-ownership CP5) a
    process its job owns is still running; also when another Controller is
    attached to a job (its supervisor lock is held), or a re-attached drain
    detached again (``OwnedWorkDetachedError``)."""
    require_pinned_execution()
    target = _inspect_target(args)
    abandon_job_id = getattr(args, "abandon", None)
    if abandon_job_id is not None:
        abandoned = job.abandon(
            target, identity=ident, runtime=runtime_root, job_id=abandon_job_id,
            acknowledge_unverifiable_worker=getattr(args, "acknowledge_unverifiable_worker", False),
        )
        if args.json:
            print(json.dumps(abandoned))
        else:
            print(_abandon_summary(abandoned, runtime_root))
        return EXIT_OK

    records = job.resume(target, identity=ident, runtime=runtime_root)

    if args.json:
        # `O1` (MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW round 2): `job.py`'s
        # own docstring for `resume` states `reconciled_this_call` is "an
        # in-memory-only marker ... never part of the persisted job-record
        # schema and never read back on a later invocation" -- `--json`
        # must report the same durable shape `jobs/<job_id>.json` holds,
        # not a superset of it, so the ephemeral marker is stripped from a
        # copy used for serialisation only; the exit-code computation below
        # still reads `reconciled_this_call` off the original `records`
        # list, unaffected. `resume_marked` (a real field `resume` may add
        # to a *terminal* record) is left alone -- only
        # `reconciled_this_call` is stripped, per this finding's fix.
        json_records = [
            {k: v for k, v in record.items() if k != "reconciled_this_call"}
            for record in records
        ]
        print(json.dumps(json_records))
    else:
        if not records:
            print(f"no job records for {target.root}")
        for record in records:
            job_id = record.get("job_id", "<unknown>")
            status = record.get("status", "<unknown>")
            marked = record.get("resume_marked")
            if marked is not None:
                print(f"{job_id}: {status} (resume_marked={marked['outcome']}: {marked['reason']})")
            else:
                print(f"{job_id}: {status}")

    # Exit 40 means "the Controller itself was interrupted, or `resume`
    # reconciled a record to `INTERRUPTED`" (CONTROLLER_GEN1_PLAN.md's exit
    # code table) -- the *event*, not the mere presence of INTERRUPTED
    # status anywhere in job history. `job.resume` marks every record it
    # actually reconciled this call with `reconciled_this_call: True`; a
    # record returned unchanged because it was *already* terminal
    # (`status in TERMINAL_STATUSES`, including a historical INTERRUPTED
    # from an earlier invocation) never carries that marker, so it cannot
    # by itself trigger exit 40 here (fixes `I2`,
    # MANUAL_EXTERNAL_IMPLEMENTATION_REVIEW round 1: this previously fired
    # on any historical terminal INTERRUPTED record, forever, regardless of
    # whether this invocation reconciled anything).
    any_interrupted = any(
        record.get("status") == job.STATUS_INTERRUPTED and record.get("reconciled_this_call")
        for record in records
    )
    # CP5: a record left `LAUNCHED` because its recorded worker is `active`
    # or `unverifiable` -- nothing about it was reconciled, and a worker may
    # still hold the worktree -- takes precedence over exit 40.
    any_worker_held = any(
        (record.get("resume_marked") or {}).get("outcome") in job.RESUME_HELD_OUTCOMES
        for record in records
    )
    if any_worker_held:
        print(f"follow it: {job.follow_command(runtime_root, target.root)}", file=sys.stderr)
        return EXIT_WORKER_ACTIVE
    return EXIT_INTERRUPTED if any_interrupted else EXIT_OK


def cmd_milestone_binding(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    """``milestone-binding --new-pr``/``--abandon`` (trunk-branch-pr-release-
    orchestration CP8, TBR-R4-001): acknowledge a binding record of
    ``--work-item`` in a refusal state (or, for ``--abandon``, a plan-stage
    one), under the lifecycle lock. Exit 0, or 20 when a check refuses and
    nothing is written. Never launches a worker, writes a job record, or
    touches a ref or a pull request."""
    require_pinned_execution()
    target = _inspect_target(args)
    disposition = milestone_branch.NEW_PR if args.new_pr else milestone_branch.ABANDON
    record = job.acknowledge_milestone_binding(target, runtime=runtime_root, work_item_id=args.work_item,
                                               disposition=disposition)
    if args.json:
        print(json.dumps(record))
    else:
        print(f"milestone-binding --{disposition}: the {record['branch']} binding of {record['work_item_id']} "
              f"is now {record['state']}")
    return EXIT_OK


_DISPATCH = {
    "inspect": cmd_inspect,
    "explain": cmd_explain,
    "step": cmd_step,
    "run": cmd_run,
    "resume": cmd_resume,
    "milestone-binding": cmd_milestone_binding,
    # "status" is dispatched specially: it needs the pre-existing-state
    # snapshot captured before this run's own identity.json write.
}


def _capture_pre_existing_state(runtime_root: Path) -> dict:
    had_any = runtime_root.is_dir() and any(runtime_root.iterdir())
    prior_identity = runtime.read_json(runtime_root / "identity.json")
    handoff = runtime.read_json(runtime_root / "handoff.json")
    jobs_dir = runtime_root / "jobs"
    job_ids = [p.stem for p in jobs_dir.glob("*.json")] if jobs_dir.is_dir() else []
    return {
        "had_any_state": had_any,
        "identity": prior_identity,
        "handoff": handoff,
        "job_ids": job_ids,
    }


def _dispatch(args: argparse.Namespace, argv: list[str]) -> int:
    command = args.command
    if command == "follow":
        # Before pinning, runtime-root creation, materialisation and the
        # identity write: `follow` only reads.
        return cmd_follow(args)
    ident = identity.pin()

    handoff = identity.read_exec_handoff()
    exec_depth = handoff.get("exec_depth", 0) if handoff else 0
    os.environ.pop(identity.EXEC_HANDOFF_ENV, None)

    origin = ident.origin_source_root or ident.source_root
    runtime_root, ladder_row = runtime.resolve_runtime_root(
        runtime_dir=args.runtime_dir, origin_source_root=origin, runtime_kind=ident.runtime_kind,
    )

    read_only = command in READ_ONLY_COMMANDS
    runtime.ensure_runtime_root(runtime_root, ladder_row=ladder_row)

    if ident.source_kind == identity.SOURCE_KIND_UNPINNED and not read_only:
        if exec_depth >= 1:
            raise SourceSnapshotError(
                "already re-execed once from an unpinned source and reached the unpinned "
                "branch again -- refusing a second exec rather than looping",
                evidence={"raised_by": "cli.main", "exec_depth": exec_depth},
            )
        # An `unidentified` runtime refuses here (exit 20): `materialise`
        # dispatches on the kind this process resolved.
        snapshot_dir = identity.materialise(
            ident.source_root, runtime_root, allow_dirty=args.allow_dirty_source,
            resolution=ident.resolution(),
        )
        # The handoff must carry the *snapshot's own* source_kind/source_commit
        # (as materialise() actually determined and published), never the
        # parent's own "unpinned" identity fields -- those are always
        # source_kind="unpinned" and would disagree with a "worktree" snapshot,
        # tripping pin()'s own cross-check in the child on every dirty run.
        snapshot_pin = runtime.read_json(snapshot_dir / "SOURCE_PIN.json")
        _reexec(
            snapshot_dir=snapshot_dir, runtime_root=runtime_root, argv=argv,
            exec_depth_seen=exec_depth, source_kind=snapshot_pin["source_kind"],
            source_commit=snapshot_pin.get("source_commit"),
        )
        raise AssertionError("os.execve returned, which should be impossible")

    pre_existing = _capture_pre_existing_state(runtime_root)
    pre_existing["ladder_row"] = ladder_row

    _write_identity_record(runtime_root, ident, exec_depth=exec_depth)

    if command == "status":
        return cmd_status(args, runtime_root, ident, pre_existing=pre_existing)

    if not read_only:
        require_pinned_execution()

    return _DISPATCH[command](args, runtime_root, ident)


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv
    parser = build_parser()
    args = parser.parse_args(raw_argv)
    if args.command == "resume" and args.acknowledge_unverifiable_worker and args.abandon is None:
        parser.error("--acknowledge-unverifiable-worker is accepted only with --abandon JOB_ID")
    if args.command == "milestone-binding" and args.work_item is None:
        parser.error("milestone-binding requires --work-item <id>")
    global _open_run
    if _open_run is not None:
        _open_run.discard()  # only a run this invocation creates is closed below
    _open_run = None
    _stop_follower(join=False)
    exit_code: int | None = None
    interrupted = False
    try:
        try:
            exit_code = _dispatch(args, raw_argv)
        except LifecycleWorkerActiveError as exc:
            # CP5: its own clause, *before* the blanket handler below, so a
            # held worktree exits 45, never 20. `LifecycleLockError` is a
            # sibling, not a subclass, and reaches the blanket clause.
            print(f"error: {exc.message}", file=sys.stderr)
            exit_code = EXIT_WORKER_ACTIVE
        except ControllerError as exc:
            print(f"error: {exc.message}", file=sys.stderr)
            exit_code = EXIT_FAIL_CLOSED
        return exit_code
    except KeyboardInterrupt:
        interrupted = True
        raise
    finally:
        _close_open_run(exit_code, interrupted=interrupted)
        _stop_follower(join=not interrupted)


def _close_open_run(exit_code: int | None, *, interrupted: bool) -> None:
    """The one closing site for the run ``cmd_step``/``cmd_run``
    registered (release-runtime-observability CP5): ``ended`` with the
    code :func:`main` computed, or ``interrupted`` on ``KeyboardInterrupt``
    (``exit_code`` ``null``, ``current_job_id`` kept). Any other exception
    leaves the record ``running``, as a killed Controller would. Closing is
    best-effort and never raises into the exit path."""
    global _open_run
    run, _open_run = _open_run, None
    if run is None:
        return
    if interrupted:
        run.interrupt()
    elif exit_code is not None:
        run.close(exit_code)
    else:
        run.discard()
