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
import time
from pathlib import Path

from controller import evidence, handoff, identity, job, managed_repo, runtime, target_state
from controller.decision import Decision, decide_no_work_item, phase_to_wire
from controller.errors import ControllerError, SourceSnapshotError

#: The three read-only commands. Positive guard: everything not in this set
#: requires a pinned identity (`source_kind != "unpinned"`), by construction
#: rather than by a denylist a future command could be added without
#: updating.
READ_ONLY_COMMANDS = frozenset({"inspect", "explain", "status"})
ALL_COMMANDS = frozenset({"inspect", "explain", "status", "step", "run", "resume"})

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
EXIT_HANDOFF_PENDING = 50

#: Test-support surface (CP8): `--pause-file` is inert unless this
#: environment variable is also set to exactly `"1"`, so an ordinary
#: invocation -- including one that passes the flag by accident or by
#: copy-paste -- cannot pause.
TEST_HOOKS_ENV = "WORKFLOW_CONTROLLER_TEST_HOOKS"

_PAUSE_POLL_SECONDS = 0.05


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
        "interpreter_executable": sys.executable,
        "interpreter_safe_path": bool(sys.flags.safe_path),
        "interpreter_sys_path": list(sys.path),
        "exec_depth": exec_depth,
    }
    runtime.write_json(runtime_root, "identity.json", record)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workflow-controller")
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--work-item", default=None)
    parser.add_argument("--workflow-manager", default=None)
    parser.add_argument("--claude-binary", default=None)
    parser.add_argument("--permission-mode", default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--allow-dirty-source", action="store_true", default=False)
    parser.add_argument("--json", action="store_true", default=False)

    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_p = subparsers.add_parser("inspect")
    inspect_p.add_argument("repo")

    explain_p = subparsers.add_parser("explain")
    explain_p.add_argument("repo")

    step_p = subparsers.add_parser("step")
    step_p.add_argument("repo")

    run_p = subparsers.add_parser("run")
    run_p.add_argument("repo")
    run_p.add_argument("--max-steps", type=int, default=20)
    run_p.add_argument("--pause-file", default=None)

    resume_p = subparsers.add_parser("resume")
    resume_p.add_argument("repo")

    subparsers.add_parser("status")

    return parser


def cmd_status(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity,
               *, pre_existing: dict) -> int:
    """Controller-owned view: pinned identity, job records, pending
    handoff -- reported from what was durably present when this process
    *started*, since this same process writes a fresh ``identity.json``
    immediately before this body runs and that record must not be mistaken
    for evidence of prior work."""
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

    print(f"runtime root: {runtime_root} (ladder row {pre_existing['ladder_row']})")
    return EXIT_OK


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
            }))
            return EXIT_OK
        print(f"repository: {target.root} (Workflow {target.workflow_version}, profile {target.profile})")
        print("work item: none -- no non-terminal work item exists and none was explicitly named")
        return EXIT_OK

    if args.json:
        print(json.dumps({
            "repository": {
                "root": str(target.root),
                "workflow_version": target.workflow_version,
                "profile": target.profile,
            },
            "work_item": _work_item_payload(work_item),
        }))
        return EXIT_OK

    print(f"repository: {target.root} (Workflow {target.workflow_version}, profile {target.profile})")
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
    return EXIT_OK


def cmd_explain(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    """``explain``: the next-action decision with full evidence, and, at a
    gate, exactly what a human must do (capability 3). Read-only -- it
    only ever calls :func:`controller.evidence.decide`, never
    :func:`controller.job.execute_step`, so no job record is written and
    no worker is launched."""
    target = _inspect_target(args)
    snapshot = target_state.read(target)
    work_item = target_state.select_work_item(snapshot, work_item_id=args.work_item)
    decision = (
        decide_no_work_item(target)
        if work_item is target_state.NoWorkItemYet
        else evidence.decide(target, snapshot, work_item)
    )

    if args.json:
        gate = decision.gate
        print(json.dumps({
            "observed_phase": phase_to_wire(decision.observed_phase),
            "evidence": list(decision.evidence),
            "automatic": decision.automatic,
            "declined": decision.declined,
            "action": decision.action.command if decision.action is not None else None,
            "reason": decision.reason,
            "gate": None if gate is None else {
                "repository": gate.repository,
                "work_item_id": gate.work_item_id,
                "phase": gate.phase,
                "what_is_required": gate.what_is_required,
                "artifact_path": gate.artifact_path,
                "safe_resume_command": gate.safe_resume_command,
            },
        }))
        return EXIT_OK

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
    elif decision.declined:
        print(f"declined by this generation (automation-safe, but not automated here): "
              f"{decision.action.command}")
    else:
        print("no action at this phase (e.g. LEGACY_READY or MILESTONE_COMPLETE)")
    return EXIT_OK


def _run_one_step(
    args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity,
    target: managed_repo.ManagedRepository,
) -> tuple[int, dict | Decision | None]:
    """One orchestration boundary: a generation-handoff check, then (at
    most) one job execution. Shared by ``step`` (one call) and ``run``'s
    own loop body (repeated until a stop condition) -- both `step`'s "stop
    after exactly one action" and `run`'s own boundary discipline (CP8:
    handoff detection runs "after a job finishes, before the next one
    starts, never mid-job") are this same check, run once per call.

    Returns ``(exit_code, result)``, where ``result`` is ``None`` for a
    handoff, a :class:`~controller.decision.Decision` for the no-action
    class, or a ``JobRecord`` otherwise."""
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
        return EXIT_HANDOFF_PENDING, None

    result = job.execute_step(
        target,
        work_item_id=args.work_item,
        identity=ident,
        runtime=runtime_root,
        permission_mode=args.permission_mode or job.DEFAULT_PERMISSION_MODE,
        timeout=args.timeout,
        claude_bin=args.claude_binary,
    )

    if isinstance(result, Decision):
        # LEGACY_READY / MILESTONE_COMPLETE: nothing ran, nothing is
        # pending.
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
    target = _inspect_target(args)
    exit_code, _result = _run_one_step(args, runtime_root, ident, target)
    return exit_code


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

    target = _inspect_target(args)

    steps_run = 0
    while steps_run < args.max_steps:
        # The orchestration boundary: checked once before every job this
        # loop starts, including the first -- never mid-job. Both halves
        # (the test-support pause and the real handoff detection, inside
        # `_run_one_step`) are checked here, together, per the plan's own
        # "the run loop checks for the file only at the same orchestration
        # boundary where detect() runs".
        _await_pause_file(args.pause_file)

        exit_code, result = _run_one_step(args, runtime_root, ident, target)
        steps_run += 1

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


def cmd_resume(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    """``resume``: reconcile non-terminal job records, then report
    (capability 7). Never launches a worker -- ``controller.job.resume``
    contains no call to ``controller.worker.launch`` at all."""
    require_pinned_execution()
    target = _inspect_target(args)
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
    return EXIT_INTERRUPTED if any_interrupted else EXIT_OK


_DISPATCH = {
    "inspect": cmd_inspect,
    "explain": cmd_explain,
    "step": cmd_step,
    "run": cmd_run,
    "resume": cmd_resume,
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
    ident = identity.pin()

    handoff = identity.read_exec_handoff()
    exec_depth = handoff.get("exec_depth", 0) if handoff else 0
    os.environ.pop(identity.EXEC_HANDOFF_ENV, None)

    origin = ident.origin_source_root or ident.source_root
    runtime_root, ladder_row = runtime.resolve_runtime_root(
        runtime_dir=args.runtime_dir, origin_source_root=origin,
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
        snapshot_dir = identity.materialise(
            ident.source_root, runtime_root, allow_dirty=args.allow_dirty_source,
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
    try:
        return _dispatch(args, raw_argv)
    except ControllerError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        return EXIT_FAIL_CLOSED
