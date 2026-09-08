"""The Controller's command-line surface (capability 10).

CP1 provides the skeleton: argument parsing for the full six-command
surface, the pinned-execution guard and the materialise-and-re-exec
mechanism that makes every non-read-only command run from an immutable
snapshot, and a fully working ``status`` command. ``inspect``, ``explain``,
``step``, ``run`` and ``resume`` are wired to their real behaviour by later
checkpoints (CP2-CP9); here they are present in the command table -- so the
guard and the parser are already complete and tested -- but their bodies
raise ``NotImplementedError`` once dispatched.
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import time
from pathlib import Path

from controller import handoff, identity, job, managed_repo, runtime
from controller.decision import Decision
from controller.errors import ControllerError, SourceSnapshotError

#: The three read-only commands. Positive guard: everything not in this set
#: requires a pinned identity (`source_kind != "unpinned"`), by construction
#: rather than by a denylist a future command could be added without
#: updating.
READ_ONLY_COMMANDS = frozenset({"inspect", "explain", "status"})
ALL_COMMANDS = frozenset({"inspect", "explain", "status", "step", "run", "resume"})

#: The `run` loop's own exit-code contract (CP8's slice of it -- the
#: full table, including 10/15/16/30/35, is CP9's own "CLI completion").
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_GATE = 10
EXIT_DECLINED = 15
EXIT_MAX_STEPS = 16
EXIT_FAIL_CLOSED = 20
EXIT_WORKER_FAILED = 30
EXIT_INCOMPLETE = 35
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
    env[identity.EXEC_HANDOFF_ENV] = __import__("json").dumps({
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


def cmd_inspect(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    raise NotImplementedError("wired to Workflow Manager / target-state reading in CP2/CP3")


def cmd_explain(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    raise NotImplementedError("wired to the decision engine in CP4/CP4B")


def cmd_step(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    require_pinned_execution()
    raise NotImplementedError("wired to job execution in CP6")


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

    target = managed_repo.inspect(args.repo, manager_bin=args.workflow_manager)
    origin_source_root = ident.origin_source_root or ident.source_root

    steps_run = 0
    while steps_run < args.max_steps:
        # The orchestration boundary: checked once before every job this
        # loop starts, including the first -- never mid-job. Both halves
        # (the test-support pause and the real handoff detection) are
        # checked here, together, per the plan's own "the run loop checks
        # for the file only at the same orchestration boundary where
        # detect() runs".
        _await_pause_file(args.pause_file)

        pending = handoff.detect(ident, origin_source_root)
        if pending is not None:
            jobs_complete, jobs_open = _classify_jobs(runtime_root)
            handoff.write_handoff_record(
                runtime_root, pending,
                jobs_complete=jobs_complete, jobs_open=jobs_open,
                next_generation_command=_next_generation_command(runtime_root, args.repo),
                now=_now(),
            )
            return EXIT_HANDOFF_PENDING

        result = job.execute_step(
            target,
            work_item_id=args.work_item,
            identity=ident,
            runtime=runtime_root,
            permission_mode=args.permission_mode or job.DEFAULT_PERMISSION_MODE,
            timeout=args.timeout,
            claude_bin=args.claude_binary,
        )
        steps_run += 1

        if isinstance(result, Decision):
            # LEGACY_READY / MILESTONE_COMPLETE: nothing ran, nothing is
            # pending.
            return EXIT_OK

        status = result["status"]
        if status == job.STATUS_GATE_BLOCKED:
            return EXIT_GATE
        if status == job.STATUS_DECLINED:
            return EXIT_DECLINED
        if status == job.STATUS_HANDOFF_PENDING:
            # A handoff.json this loop did not itself write (e.g. left
            # over from a prior invocation) -- execute_step's own step 3
            # check caught it first.
            return EXIT_HANDOFF_PENDING
        if status == job.STATUS_FAILED:
            return EXIT_WORKER_FAILED
        if status == job.STATUS_INCOMPLETE:
            return EXIT_INCOMPLETE
        # STATUS_FINISHED: this checkpoint/action is done -- loop back to
        # the orchestration boundary and decide the next one.

    return EXIT_MAX_STEPS


def cmd_resume(args: argparse.Namespace, runtime_root: Path, ident: identity.ControllerIdentity) -> int:
    require_pinned_execution()
    raise NotImplementedError("wired to durable resume in CP7")


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
